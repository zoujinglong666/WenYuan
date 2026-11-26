"""对话接口：一次性问答 + SSE 流式问答。

会话持久化（spec.md RQ-1）：请求带可选 conversation_id，问答完成后
把「问题 + 回答 + 引用」写入会话仓库；响应 / done 事件回传会话 id。
落库失败只告警不阻断问答 —— 历史是增强能力，不能反过来拖垮主链路。
"""

from __future__ import annotations

import asyncio
import json
import logging

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse

from app.api.schemas import ChatRequest, ChatResponse, Citation, RetrievalTrace
from app.config import settings
from app.core.sensitive import contains_sensitive
from app.rag.graph import get_rag_graph
from app.retrieval.acl import UserContext, build_acl_ids
from app.storage.factory import get_conversation_repo

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/v1", tags=["chat"])

#: 命中敏感词时的固定提示文案（spec RQ-2：拒绝但不羞辱）
REFUSAL_MESSAGE = "您的提问包含不文明用语，请调整措辞、文明提问后再试。"

#: 生成并发信号灯（惰性创建：绑定运行中的事件循环）。
#: LLM 挂起/变慢时如果没有上限，每路都在白烧 token 与占坑，
#: 用户只看到无限转圈 —— 满员时干脆 429，把「稍后再试」亮出来。
_generation_sem: asyncio.Semaphore | None = None


def _generation_gate() -> asyncio.Semaphore:
    global _generation_sem
    if _generation_sem is None:
        _generation_sem = asyncio.Semaphore(settings.generation_max_concurrency)
    return _generation_sem


async def _acquire_generation_slot() -> asyncio.Semaphore:
    """非阻塞式占坑：拿不到立即 429，不让请求挂队列里干等。"""
    sem = _generation_gate()
    try:
        await asyncio.wait_for(sem.acquire(), timeout=0.25)
    except TimeoutError as exc:
        raise HTTPException(
            status_code=429,
            detail=f"当前有 {settings.generation_max_concurrency} 路问答正在生成，请稍后再试",
        ) from exc
    return sem


def _refuse(req: ChatRequest) -> bool:
    """入口守卫：命中敏感词直接拒绝，不进检索、不调模型。"""
    return settings.enable_sensitive_filter and contains_sensitive(req.question)


async def current_user(request: ChatRequest) -> UserContext:
    """构建用户上下文。

    ⚠️ 生产环境必须从鉴权中间件取身份，不能直接信客户端传的 user_id ——
    否则任何人都能伪造别人的身份去读别人的文档。
    这里保留函数形态，方便接入 SSO / 网关鉴权。
    """
    return UserContext(
        user_id=request.user_id,
        departments=request.departments,
        roles=request.roles,
        history=[m.model_dump() for m in request.history],
    )


async def _persist_exchange(
    req: ChatRequest,
    *,
    answer: str,
    citations: list[dict],
    known_conv_id: str | None,
) -> str | None:
    """把一轮问答落库，返回会话 id（新建或沿用）。

    - 携带的 conversation_id 无效（不存在 / 非本人）时**静默回落新建**：
      前端清了 localStorage 或换了账号是常态，不该为此报错（spec A6）。
    """
    repo = get_conversation_repo()
    conv_id = known_conv_id
    if conv_id:
        ok = await repo.append_message(
            conv_id, user_id=req.user_id, role="user", content=req.question
        )
        if not ok:
            conv_id = None
    if conv_id is None:
        conv = await repo.create(user_id=req.user_id)
        conv_id = conv["id"]
        await repo.append_message(
            conv_id, user_id=req.user_id, role="user", content=req.question
        )
    if answer.strip():
        await repo.append_message(
            conv_id, user_id=req.user_id, role="assistant", content=answer, citations=citations
        )
    return conv_id


@router.post("/chat", response_model=ChatResponse)
async def chat(req: ChatRequest) -> ChatResponse:
    # 敏感词拦截（spec RQ-2）：拒绝轮照常落库，留完整对话审计记录
    if _refuse(req):
        conv_id: str | None = None
        try:
            conv_id = await _persist_exchange(
                req, answer=REFUSAL_MESSAGE, citations=[], known_conv_id=req.conversation_id
            )
        except Exception:  # noqa: BLE001
            logger.warning("会话落库失败（不影响本次回答）", exc_info=True)
        return ChatResponse(
            answer=REFUSAL_MESSAGE, citations=[], conversation_id=conv_id, refused=True
        )

    user = await current_user(req)
    acl_ids = build_acl_ids(user)

    sem = await _acquire_generation_slot()
    graph = get_rag_graph()
    try:
        result = await graph.run(
            req.question,
            acl_ids=acl_ids,
            history=user.history,
            doc_ids=req.doc_ids,
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("问答失败")
        raise HTTPException(status_code=500, detail=f"问答失败: {exc}") from exc
    finally:
        sem.release()

    conv_id: str | None = None
    try:
        conv_id = await _persist_exchange(
            req,
            answer=result.get("answer", ""),
            citations=result.get("citations", []),
            known_conv_id=req.conversation_id,
        )
    except Exception:  # noqa: BLE001
        logger.warning("会话落库失败（不影响本次回答）", exc_info=True)

    return ChatResponse(
        answer=result.get("answer", ""),
        citations=[Citation(**c) for c in result.get("citations", [])],
        retrieval=RetrievalTrace(**result.get("retrieval", {})),
        conversation_id=conv_id,
    )


@router.post("/chat/stream")
async def chat_stream(req: ChatRequest) -> StreamingResponse:
    """SSE 流式问答。

    事件序列：``queries`` → ``trace`` → ``delta``* → ``citations`` → ``done``；
    出错时发 ``error`` 事件后结束流（连接不重置，前端能显示具体原因）。
    ``done`` 事件额外携带 ``conversation_id``（本轮归属的会话）。
    """
    user = await current_user(req)
    acl_ids = build_acl_ids(user)
    graph = get_rag_graph()

    # 敏感词拦截（spec RQ-2）：发 refused + done 后直接收流，
    # 不进检索与生成 —— 拒绝轮同样落库留审计记录
    if _refuse(req):
        async def refusal_source():
            state = {"conv_id": req.conversation_id, "persisted": False}
            try:
                yield _sse("refused", {"type": "refused", "message": REFUSAL_MESSAGE})
                state["conv_id"] = await _persist_exchange(
                    req,
                    answer=REFUSAL_MESSAGE,
                    citations=[],
                    known_conv_id=req.conversation_id,
                )
                state["persisted"] = True
            except Exception:  # noqa: BLE001
                logger.warning("拒绝轮落库失败", exc_info=True)
            finally:
                yield _sse(
                    "done",
                    {
                        "type": "done",
                        "answer": REFUSAL_MESSAGE,
                        "citations": [],
                        "refused": True,
                        "conversation_id": state["conv_id"],
                    },
                )

        return StreamingResponse(
            refusal_source(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "Connection": "keep-alive"},
        )

    # 占坑失败直接 429 —— 放在敏感词拦截之后：拒绝轮不进生成，不占并发名额
    # （SSE 还没开始，标准 HTTP 响应，前端能拿到状态码）
    sem = await _acquire_generation_slot()

    # 落库状态：跨事件累计，流结束时统一写入
    state = {"answer": "", "citations": [], "conv_id": req.conversation_id, "persisted": False}

    async def _persist() -> str | None:
        try:
            return await _persist_exchange(
                req, answer=state["answer"], citations=state["citations"], known_conv_id=state["conv_id"]
            )
        except Exception:  # noqa: BLE001
            logger.warning("会话落库失败（不影响本次回答）", exc_info=True)
            return state["conv_id"]

    async def event_source():
        try:
            async for event in graph.run_stream(
                req.question,
                acl_ids=acl_ids,
                history=user.history,
                doc_ids=req.doc_ids,
            ):
                etype = event.get("type")
                if etype == "delta":
                    state["answer"] += event.get("text", "")
                elif etype == "citations":
                    state["citations"] = event.get("citations", [])
                elif etype == "done":
                    state["conv_id"] = await _persist()
                    state["persisted"] = True
                    event = {**event, "conversation_id": state["conv_id"]}
                yield _sse(event["type"], event)
        except Exception as exc:  # noqa: BLE001
            logger.exception("流式问答失败")
            yield _sse("error", {"type": "error", "message": str(exc)})
        finally:
            # 客户端中止 / 流被截断：已生成的部分回答仍要入库（spec A7）。
            # 正常结束已在 done 分支落过库，这里只在没收尾时兜底。
            if not state["persisted"]:
                asyncio.get_running_loop().create_task(_persist())
            sem.release()

    return StreamingResponse(
        event_source(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            # 关掉 nginx 缓冲，否则增量会被攒成一坨
            "X-Accel-Buffering": "no",
        },
    )


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"
