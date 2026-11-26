"""LangGraph 编排：完整的 RAG 检索问答链路。

    改写 → 混合召回 → RRF → Rerank → 父块去重 → 生成

各节点职责严格分离，这是"召回段"和"精度段"的边界所在：

- 改写 / 召回 / 融合：负责**宽召回**，允许多余
- Rerank：负责**排精度**，把噪音砍掉
- 父块去重：多个子块命中同一父块时只保留一次，避免上下文重复占位
- 生成：只喂去重后的父块，答案带 [n] 引用

失败降级策略：改写失败 → 用原问题；单路召回失败 → 另一路顶上；
无任何结果 → 不调 LLM，直接返回"未检索到相关资料"（省成本 + 防幻觉）。
"""

from __future__ import annotations

import logging
import time
from collections.abc import AsyncIterator
from typing import Annotated, Any, TypedDict

from langgraph.graph import END, START, StateGraph

from app.config import settings
from app.core.llm import get_llm_service
from app.core.reranker import get_rerank_service
from app.core.timing import elapsed_ms
from app.indexing.hybrid import HybridRetriever, rrf_merge
from app.rag.prompts import build_answer_messages, render_context
from app.retrieval.rewrite import rewrite_queries

logger = logging.getLogger(__name__)

#: 最终喂给 LLM 的父块数量上限
MAX_CONTEXT_PARENTS = 6

#: 检索节点 → 界面上的轨道站点
_NODE_STAGE = {
    "rewrite": "rewrite",
    "retrieve": "retrieve",
    "rerank": "rerank",
    "compose": "compose",
}


class GraphState(TypedDict, total=False):
    question: str
    history: list[dict[str, str]]
    acl_ids: list[str]
    doc_ids: list[str] | None

    queries: list[str]
    candidates: list[dict[str, Any]]
    reranked: list[dict[str, Any]]
    context_blocks: list[dict[str, Any]]
    context_text: str
    answer: str
    citations: list[dict[str, Any]]
    timings: dict[str, float]

    # 检索轨迹计数：每站真实召回多少，用于把过程暴露给界面
    bm25_hits: int
    dense_hits: int
    merged: int
    reranked_count: int
    context_count: int


def _merge_lists(left: list, right: list) -> list:
    """LangGraph 的 reducer：多个并行分支的结果合并。"""
    return left + right


def _build_citations(blocks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """把上下文块转成前端可用的引用条目。"""
    return [
        {
            "index": i,
            "chunk_id": b.get("chunk_id", ""),
            "doc_id": b.get("doc_id", ""),
            "filename": b.get("filename", ""),
            "heading_text": b.get("heading_text", ""),
            "page": b.get("page"),
            "snippet": (b.get("text") or "")[:200],
        }
        for i, b in enumerate(blocks, start=1)
    ]


def _build_trace(state: dict[str, Any]) -> dict[str, Any]:
    """检索链路的真实计数与耗时。

    这一层存在的意义：界面要能回答「答案为什么可信」——
    两路各召回了多少、融合后剩多少、精排留下多少、最终喂给模型几块，
    全部来自真实执行，而不是估算。
    """
    return {
        "queries": list(state.get("queries", [])),
        "bm25_hits": state.get("bm25_hits", 0),
        "dense_hits": state.get("dense_hits", 0),
        "merged": state.get("merged", 0),
        "reranked": state.get("reranked_count", 0),
        "context_blocks": state.get("context_count", 0),
        "timings": dict(state.get("timings", {})),
    }


class RAGGraph:
    def __init__(self, retriever: HybridRetriever | None = None) -> None:
        self.retriever = retriever or HybridRetriever()
        self.reranker = get_rerank_service()
        self.llm = get_llm_service()
        self._retrieval_graph = self._build_retrieval()
        self._full_graph = self._build_full()

    # ---------------- 图构建 ----------------

    def _build_retrieval(self):
        """检索图：改写 → 召回 → 精排 → 组装上下文，到此结束。"""
        g = StateGraph(GraphState)
        g.add_node("rewrite", self._node_rewrite)
        g.add_node("retrieve", self._node_retrieve)
        g.add_node("rerank", self._node_rerank)
        g.add_node("compose", self._node_compose)

        g.add_edge(START, "rewrite")
        g.add_edge("rewrite", "retrieve")
        g.add_edge("retrieve", "rerank")
        g.add_edge("rerank", "compose")
        g.add_edge("compose", END)
        return g.compile()

    def _build_full(self):
        """完整图：在检索链路之后追加生成节点。

        两个图共用同一批节点函数 —— 检索逻辑只有一份，不会出现
        「流式和非流式召回结果不一致」这种极难排查的问题。
        """
        g = StateGraph(GraphState)
        g.add_node("rewrite", self._node_rewrite)
        g.add_node("retrieve", self._node_retrieve)
        g.add_node("rerank", self._node_rerank)
        g.add_node("compose", self._node_compose)
        g.add_node("generate", self._node_generate)

        g.add_edge(START, "rewrite")
        g.add_edge("rewrite", "retrieve")
        g.add_edge("retrieve", "rerank")
        g.add_edge("rerank", "compose")
        g.add_edge("compose", "generate")
        g.add_edge("generate", END)
        return g.compile()

    async def run(
        self,
        question: str,
        *,
        acl_ids: list[str],
        history: list[dict[str, str]] | None = None,
        doc_ids: list[str] | None = None,
    ) -> dict[str, Any]:
        initial: GraphState = {
            "question": question,
            "history": history or [],
            "acl_ids": acl_ids,
            "doc_ids": doc_ids,
            "timings": {},
        }
        result = await self._full_graph.ainvoke(initial)
        result["retrieval"] = _build_trace(result)
        return result

    async def run_stream(
        self,
        question: str,
        *,
        acl_ids: list[str],
        history: list[dict[str, str]] | None = None,
        doc_ids: list[str] | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        """流式执行，产出事件字典。

        事件类型：
          ``{"type": "trace",     "stage": ..., "trace": {...}}``  每个节点跑完就报一次
          ``{"type": "delta",     "text": "..."}``                 答案增量
          ``{"type": "citations", "citations": [...]}``            引用来源
          ``{"type": "done",      "trace": {...}}``                完成与最终耗时

        检索链路用 ``stream_mode="updates"`` 逐节点推进，
        这样界面上的轨道能一站一站点亮，而不是等整段检索跑完才一起亮。
        """
        initial: GraphState = {
            "question": question,
            "history": history or [],
            "acl_ids": acl_ids,
            "doc_ids": doc_ids,
            "timings": {},
        }

        t0 = time.perf_counter()
        merged: dict[str, Any] = {}
        async for update in self._retrieval_graph.astream(
            initial, stream_mode="updates"
        ):
            for node, delta in update.items():
                merged.update(delta)
                stage = _NODE_STAGE.get(node)
                if stage is None:
                    continue
                yield {
                    "type": "trace",
                    "stage": stage,
                    "trace": _build_trace(merged),
                }

        timings = dict(merged.get("timings", {}))
        timings["retrieve_total"] = elapsed_ms(t0)
        trace = _build_trace({**merged, "timings": timings})

        blocks = merged.get("context_blocks") or []
        if not blocks:
            # 无检索结果 → 不调 LLM。既省成本，也避免模型凭题面瞎编
            yield {
                "type": "delta",
                "text": "未在知识库中检索到与该问题相关的资料。"
                "建议：换用更具体的关键词，或确认相关文档是否已同步。",
            }
            yield {"type": "citations", "citations": []}
            yield {"type": "done", "trace": trace}
            return

        # question 取 initial 而不是 merged：stream_mode="updates" 只累积
        # 各节点的**返回值**，不含初始 state —— 没有节点会返回 question，
        # 从 merged 取必然 KeyError（前端表现为流式中断在 error 事件上）
        history = (initial.get("history") or [])[-settings.llm_history_turns * 2 :]
        messages = build_answer_messages(initial["question"], merged["context_text"], history)
        t_gen = time.perf_counter()
        async for delta in self.llm.chat_stream(messages, blocks=blocks):
            yield {"type": "delta", "text": delta}
        timings["generate"] = elapsed_ms(t_gen)
        trace["timings"] = timings

        yield {"type": "citations", "citations": _build_citations(blocks)}
        yield {"type": "done", "trace": trace}

    # ---------------- 节点 ----------------

    async def _node_rewrite(self, state: GraphState) -> dict[str, Any]:
        t0 = time.perf_counter()
        if settings.enable_query_rewrite:
            queries = await rewrite_queries(state["question"], state.get("history"))
        else:
            queries = [state["question"]]
        timings = dict(state.get("timings", {}))
        timings["rewrite"] = elapsed_ms(t0)
        return {"queries": queries, "timings": timings}

    async def _node_retrieve(self, state: GraphState) -> dict[str, Any]:
        import asyncio

        t0 = time.perf_counter()
        queries = state.get("queries") or [state["question"]]

        async def _search_one(q: str) -> tuple[list[dict[str, Any]], dict[str, int]]:
            """单条查询的召回 + 该次查询的两路通道命中数。"""
            stats: dict[str, int] = {}
            hits = await self.retriever.search(
                q,
                acl_ids=state["acl_ids"],
                doc_ids=state.get("doc_ids"),
                channel_stats=stats,
            )
            return hits, stats

        results = await asyncio.gather(
            *[_search_one(q) for q in queries],
            return_exceptions=True,
        )

        # 每条改写查询内部已经做过一次 RRF；这里对多路查询的结果再融合一次
        # 原问题权重加倍：改写查询是「放大器」也可能是「噪音源」，
        # 用户真实问句的命中在融合里必须有稳定话语权 —— 它的 hits
        # 放两份参与 RRF，等价于 weight=2
        ranked_lists: list[list[dict[str, Any]]] = []
        bm25_total = 0
        dense_total = 0
        for i, r in enumerate(results):
            if isinstance(r, BaseException):
                logger.warning("查询召回失败: %s", r)
                continue
            hits, stats = r
            ranked_lists.append(hits)
            if i == 0:
                ranked_lists.append(hits)
            bm25_total += stats.get("bm25", 0)
            dense_total += stats.get("dense", 0)

        candidates = rrf_merge(
            ranked_lists,
            source_names=[f"q{i}" for i in range(len(ranked_lists))],
            k=settings.rrf_k,
            top_n=settings.retrieve_top_k,
        )
        timings = dict(state.get("timings", {}))
        timings["retrieve"] = elapsed_ms(t0)
        logger.info(
            "召回完成: %d 条查询 → bm25 %d / dense %d → 候选 %d",
            len(queries),
            bm25_total,
            dense_total,
            len(candidates),
        )
        return {
            "candidates": candidates,
            "timings": timings,
            "bm25_hits": bm25_total,
            "dense_hits": dense_total,
            "merged": len(candidates),
        }

    async def _node_rerank(self, state: GraphState) -> dict[str, Any]:
        t0 = time.perf_counter()
        candidates = state.get("candidates") or []
        if not candidates:
            return {
                "reranked": [],
                "timings": state.get("timings", {}),
                "reranked_count": 0,
            }

        # 粗筛：RRF 融合分前段已含绝大多数真命中，只把池内前 N 条送精排。
        # cross-encoder 在 CPU 上约 0.12s/条，50 条全量送要 6s —— 是全链路最慢一步
        pool = candidates[: settings.rerank_pool_size]
        if len(pool) < len(candidates):
            logger.info("精排池粗筛: %d → %d", len(candidates), len(pool))

        # 构造给 reranker 的文本：标题路径 + 正文，让精排能利用标题信号
        documents: list[str] = []
        for item in pool:
            payload = item.get("payload") or {}
            heading = payload.get("heading_text") or ""
            text = payload.get("text") or ""
            documents.append(f"{heading}\n{text}".strip() if heading else text)

        ranked = await self.reranker.rerank(
            state["question"], documents, top_n=settings.rerank_top_k
        )

        reranked: list[dict[str, Any]] = []
        for idx, score in ranked:
            item = pool[idx]
            reranked.append({**item, "rerank_score": score})

        timings = dict(state.get("timings", {}))
        timings["rerank"] = elapsed_ms(t0)
        logger.info("Rerank 完成: %d → %d", len(pool), len(reranked))
        return {"reranked": reranked, "timings": timings, "reranked_count": len(reranked)}

    async def _node_compose(self, state: GraphState) -> dict[str, Any]:
        """把子块映射回父块并去重 —— 多个子块命中同一章节时只保留一次。

        同时做两件控制幻觉的事：
        1. 丢掉精排分数低于阈值的块 —— 低相关块会诱导模型从无关条款里编答案
        2. 截断超长父块 —— 稀释关键信息还烧 token
        """
        reranked = state.get("reranked") or []
        seen_parents: set[str] = set()
        blocks: list[dict[str, Any]] = []
        dropped_low = 0

        # 相对阈值：分数分布随查询漂移，绝对阈值会把正确块误杀 ——
        # 「低于最佳分 keep_ratio」的块才是真噪音（负分块自然落网）
        scores = [item.get("rerank_score") or 0.0 for item in reranked]
        best_score = max(scores) if scores else 0.0
        cutoff = best_score * settings.rerank_keep_ratio

        for item in reranked:
            score = item.get("rerank_score") or 0.0
            if score < settings.rerank_min_score or score < cutoff:
                dropped_low += 1
                continue
            payload = item.get("payload") or {}
            parent_id = payload.get("parent_id") or payload.get("chunk_id")
            if parent_id in seen_parents:
                continue
            seen_parents.add(parent_id)
            text = payload.get("parent_text") or payload.get("text") or ""
            blocks.append(
                {
                    "text": text[: settings.context_block_max_chars],
                    "filename": payload.get("filename", ""),
                    "heading_text": payload.get("heading_text", ""),
                    "page": payload.get("page"),
                    "doc_id": payload.get("doc_id", ""),
                    "chunk_id": payload.get("chunk_id", ""),
                    "parent_id": parent_id,
                    "rrf_score": item.get("rrf_score", 0.0),
                    "rerank_score": score,
                }
            )
            if len(blocks) >= MAX_CONTEXT_PARENTS:
                break

        if dropped_low:
            logger.info(
                "精排低分过滤: 丢弃 %d 块（cutoff %.3f，最佳 %.3f）",
                dropped_low,
                cutoff,
                best_score,
            )

        context_text = render_context(blocks)
        return {
            "context_blocks": blocks,
            "context_text": context_text,
            "context_count": len(blocks),
        }

    async def _node_generate(self, state: GraphState) -> dict[str, Any]:
        t0 = time.perf_counter()
        blocks = state.get("context_blocks") or []

        # 无检索结果 → 不调 LLM。既省成本，也避免模型凭题面瞎编
        if not blocks:
            answer = (
                "未在知识库中检索到与该问题相关的资料。"
                "建议：换用更具体的关键词，或确认相关文档是否已同步。"
            )
            citations: list[dict[str, Any]] = []
        else:
            # 最近几轮对话并进提问 —— 追问「那第2条呢」需要上文才能答对
            history = (state.get("history") or [])[-settings.llm_history_turns * 2 :]
            messages = build_answer_messages(state["question"], state["context_text"], history)
            answer = await self.llm.chat(messages, blocks=blocks)
            citations = _build_citations(blocks)

        timings = dict(state.get("timings", {}))
        timings["generate"] = elapsed_ms(t0)
        return {"answer": answer, "citations": citations, "timings": timings}


#: 全局单例
_rag_graph: RAGGraph | None = None


def get_rag_graph() -> RAGGraph:
    global _rag_graph
    if _rag_graph is None:
        _rag_graph = RAGGraph()
    return _rag_graph
