"""本地演示桩服务 —— 前端开发与走查用，**不是生产组件**。

为什么需要它
------------
真实后端依赖 Qdrant / Elasticsearch / Postgres / TEI / vLLM，
在没 GPU 的机器上跑不起来，前端就无法自测。
这个桩按 `app/api/schemas.py` 的同一份契约返回合成数据，
让界面能被完整点一遍：流式问答、引用溯源、检索调试、文档上传。

生产部署永远不会用到它 —— `app.main` 不引用本模块。

    python -m scripts.dev_stub          # 默认 127.0.0.1:8099
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from app.api.schemas import (
    ChatRequest,
    ChatResponse,
    ChunkSource,
    Citation,
    DocumentInfo,
    IngestResponse,
    RetrievalTrace,
    SearchHit,
    SearchRequest,
    SearchResponse,
)

app = FastAPI(title="企业知识库 · 演示桩", version="0.0.0-stub")

DIST = Path("web/dist").resolve()

_DOCS: list[DocumentInfo] = [
    DocumentInfo(
        doc_id="doc-employee-handbook",
        filename="员工手册.pdf",
        title="员工手册",
        status="ready",
        version=3,
        chunk_count=248,
        parser="mineru",
        acl_ids=["public", "dept:hr"],
    ),
    DocumentInfo(
        doc_id="doc-travel-policy",
        filename="差旅报销制度.docx",
        title="差旅报销制度",
        status="ready",
        version=2,
        chunk_count=64,
        parser="office",
        acl_ids=["public"],
    ),
    DocumentInfo(
        doc_id="doc-contract-flow",
        filename="合同审批流程说明.md",
        title="合同审批流程说明",
        status="ready",
        version=1,
        chunk_count=31,
        parser="plain",
        acl_ids=["public", "role:manager"],
    ),
    DocumentInfo(
        doc_id="doc-onboarding",
        filename="新员工入职指引.pdf",
        title="新员工入职指引",
        status="processing",
        version=1,
        chunk_count=0,
        parser="mineru",
        acl_ids=["dept:hr"],
    ),
]

_HITS = [
    (
        "chunk-travel-3-2",
        "差旅报销制度.docx",
        "第三章 差旅制度 > 3.2 住宿标准",
        ["bm25", "dense"],
        0.0321,
        3.8472,
        "一线城市（北京、上海、广州、深圳）住宿标准上限为每晚 600 元，"
        "二线城市 450 元，其他城市 350 元。超标部分需由部门负责人书面说明并审批。",
    ),
    (
        "chunk-employee-7-1",
        "员工手册.pdf",
        "第七章 入职与转正 > 7.1 入职办理",
        ["dense"],
        0.0298,
        2.9134,
        "新员工入职当日需到前台领取工牌、签署保密协议，"
        "并在三个工作日内完成社保与公积金信息登记。",
    ),
    (
        "chunk-contract-2",
        "合同审批流程说明.md",
        "二、审批链路",
        ["bm25"],
        0.0244,
        1.7205,
        "合同金额 10 万元以下由部门负责人审批；10 万元至 100 万元需法务会签；"
        "超过 100 万元须提交管理层办公会审议。",
    ),
    (
        "chunk-travel-3-1",
        "差旅报销制度.docx",
        "第三章 差旅制度 > 3.1 交通标准",
        ["bm25"],
        0.0207,
        1.1022,
        "总监级及以上可乘坐飞机公务舱，其余职级为经济舱；"
        "市内交通凭票据实报销，不设免票额度。",
    ),
    (
        "chunk-employee-appendix",
        "员工手册.pdf",
        "附录 B 常用表单索引",
        ["dense"],
        0.0186,
        0.6417,
        "差旅报销单编号 FR-01；住宿超标说明表编号 FR-02；合同用印申请编号 LG-11。",
    ),
]

_ANSWER = (
    "根据《差旅报销制度》第三章 3.2 条，住宿标准按城市分为三档 [1]："
    "一线城市（北京、上海、广州、深圳）上限每晚 600 元，二线城市 450 元，"
    "其他城市 350 元；超标部分需部门负责人书面说明并审批 [1]。\n\n"
    "交通方面，总监级及以上可乘坐公务舱，其余职级为经济舱，市内交通凭票据实报销 [4]。"
    "配套的住宿超标说明表编号为 FR-02 [5]。"
)


def _trace(**kw) -> RetrievalTrace:
    base = {
        "queries": ["差旅报销的住宿标准是多少", "住宿超标审批流程"],
        "bm25_hits": 50,
        "dense_hits": 50,
        "merged": 73,
        "reranked": 8,
        "context_blocks": 5,
        "timings": {
            "rewrite": 412.0,
            "retrieve": 186.0,
            "rerank": 233.0,
            "generate": 1874.0,
            "retrieve_total": 831.0,
        },
    }
    base.update(kw)
    return RetrievalTrace(**base)


def _citations() -> list[Citation]:
    return [
        Citation(
            index=i + 1,
            chunk_id=h[0],
            doc_id=f"doc-{i}",
            filename=h[1],
            heading_text=h[2],
            page=None,
            snippet=h[6][:200],
        )
        for i, h in enumerate(_HITS[:5])
    ]


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "env": "stub"}


@app.get("/ready")
async def ready() -> dict[str, object]:
    return {
        "ready": True,
        "checks": {
            "llm": "ok",
            "embedding": "ok",
            "qdrant": "ok",
            "elasticsearch": "ok",
            "mineru": "ok",
        },
    }


@app.get("/v1/info")
async def info() -> dict[str, object]:
    return {
        "service": "enterprise-kb",
        "version": "0.0.0-stub",
        "docs": "/docs",
        # 演示桩：界面必须把合成数据标出来，否则会被当成真实制度
        "demo": True,
        "llm_provider": "local",
        "llm_model": "qwen3-32b",
        "embedding_model": "BAAI/bge-m3",
        "rerank_model": "BAAI/bge-reranker-v2-m3",
    }


@app.get("/v1/chunks/{chunk_id}", response_model=ChunkSource)
async def get_chunk_source(chunk_id: str) -> ChunkSource:
    """取 chunk 原文 —— 与真实后端同一份契约。"""
    for h in _HITS:
        if h[0] == chunk_id:
            return ChunkSource(
                chunk_id=h[0],
                doc_id=f"doc-{h[0]}",
                filename=h[1],
                heading_text=h[2],
                page=None,
                text=h[6],
            )
    raise HTTPException(status_code=404, detail="未找到该片段，或你无权查看")


@app.get("/v1/stats")
async def stats() -> dict[str, object]:
    return {
        "documents": 4,
        "chunks": 343,
        "qdrant_points": 343,
        "es_documents": 343,
        "errors": {},
        "config": {
            "llm_provider": "local",
            "llm_model": "qwen3-32b",
            "embedding_model": "BAAI/bge-m3",
            "rerank_model": "BAAI/bge-reranker-v2-m3",
            "retrieval": {"retrieve_top_k": 50, "rerank_top_k": 8, "rrf_k": 60},
            "acl_enabled": True,
        },
    }


@app.get("/v1/documents", response_model=list[DocumentInfo])
async def list_documents() -> list[DocumentInfo]:
    return _DOCS


@app.post("/v1/documents", response_model=IngestResponse)
async def upload_document(
    file: UploadFile = File(...),
    acl_ids: str = Form(default="public"),
    prefer_parser: str | None = Form(default=None),
) -> IngestResponse:
    name = file.filename or "unnamed"
    await file.read()
    doc = DocumentInfo(
        doc_id=f"doc-{name}",
        filename=name,
        title=name,
        status="ready",
        version=1,
        chunk_count=42,
        parser=prefer_parser or "mineru",
        acl_ids=[a.strip() for a in acl_ids.split(",") if a.strip()] or ["public"],
    )
    _DOCS.insert(0, doc)
    return IngestResponse(
        doc_id=doc.doc_id,
        filename=doc.filename,
        status=doc.status,
        chunk_count=doc.chunk_count,
        skipped=False,
        parser=doc.parser,
        error=None,
        timings={"parse": 1420.0, "chunk": 80.0, "embed": 610.0, "index": 330.0},
    )


@app.delete("/v1/documents/{doc_id}")
async def delete_document(doc_id: str) -> dict[str, str]:
    global _DOCS
    _DOCS = [d for d in _DOCS if d.doc_id != doc_id]
    return {"status": "deleted", "doc_id": doc_id}


@app.post("/v1/search", response_model=SearchResponse)
async def search(req: SearchRequest) -> SearchResponse:
    await asyncio.sleep(0.35)
    hits = [
        SearchHit(
            chunk_id=h[0],
            filename=h[1],
            heading_text=h[2],
            rrf_score=h[4],
            rerank_score=h[5],
            sources=h[3],
            snippet=h[6],
        )
        for h in _HITS[: req.top_k]
    ]
    return SearchResponse(
        hits=hits,
        queries=[req.query],
        retrieval=_trace(
            queries=[req.query],
            bm25_hits=50,
            dense_hits=50,
            merged=73,
            reranked=len(hits),
            context_blocks=0,
            timings={"retrieve": 204.0, "rerank": 241.0},
        ),
    )


@app.post("/v1/chat", response_model=ChatResponse)
async def chat(req: ChatRequest) -> ChatResponse:
    await asyncio.sleep(0.6)
    return ChatResponse(
        answer=_ANSWER,
        citations=_citations(),
        retrieval=_trace(),
    )


@app.post("/v1/chat/stream")
async def chat_stream(req: ChatRequest) -> StreamingResponse:
    async def event_source():
        trace = _trace().model_dump()
        yield _sse("trace", {"type": "trace", "stage": "rewrite", "trace": trace})
        await asyncio.sleep(0.25)
        yield _sse(
            "trace",
            {"type": "trace", "stage": "retrieve", "trace": trace},
        )
        await asyncio.sleep(0.3)
        yield _sse("trace", {"type": "trace", "stage": "rerank", "trace": trace})
        await asyncio.sleep(0.25)
        yield _sse("trace", {"type": "trace", "stage": "compose", "trace": trace})
        await asyncio.sleep(0.2)

        for chunk in _ANSWER:
            yield _sse("delta", {"type": "delta", "text": chunk})
            await asyncio.sleep(0.012)

        citations = [c.model_dump() for c in _citations()]
        yield _sse("citations", {"type": "citations", "citations": citations})
        yield _sse("done", {"type": "done", "trace": trace})

    return StreamingResponse(
        event_source(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


# ---------------- 前端静态资源 ----------------

if DIST.is_dir():
    assets = DIST / "assets"
    if assets.is_dir():
        app.mount("/assets", StaticFiles(directory=assets), name="assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    async def spa(full_path: str) -> FileResponse:
        if full_path.startswith(("v1/", "api/")):
            raise HTTPException(status_code=404, detail="接口不存在")
        if full_path:
            candidate = (DIST / full_path).resolve()
            if DIST in candidate.parents and candidate.is_file():
                return FileResponse(candidate)
        return FileResponse(DIST / "index.html")
else:  # pragma: no cover
    print(f"[stub] 未找到 {DIST}，只提供 API —— 先跑 npm run build")
