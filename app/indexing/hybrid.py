"""混合检索：BM25 ⊕ 向量 → RRF 融合。

为什么用 RRF 而不是加权归一化
---------------------------
两路引擎的分数尺度完全不同（BM25 是无上界的正数，余弦相似度是 [-1,1]）。
手工加权需要针对每个语料调权重，换个数据集就得重调 —— 这是没人愿意维护的参数。
RRF 只看**排名**：``score(d) = Σ 1 / (k + rank_i(d))``，无需任何调参，
对分数尺度不敏感，是目前最稳的主流融合方式（k 取 60）。

召回段 / 精度段的分工
---------------------
- 召回段（本模块）：两路各取 Top-50，允许噪音，目标"召得回"
- 精度段（reranker）：Cross-Encoder 精排到 Top-8，目标"排得准"
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from app.config import settings
from app.core.embeddings import get_embedding_service
from app.indexing.es_store import ESStore
from app.indexing.factory import get_keyword_store, get_vector_store
from app.indexing.qdrant_store import QdrantStore
from app.indexing.rrf import rrf_merge

logger = logging.getLogger(__name__)

__all__ = ["HybridRetriever", "rrf_merge"]


class HybridRetriever:
    def __init__(
        self,
        qdrant: QdrantStore | None = None,
        es: ESStore | None = None,
    ) -> None:
        self.qdrant = qdrant or get_vector_store()
        self.es = es or get_keyword_store()
        self.embeddings = get_embedding_service()

    async def search(
        self,
        query: str,
        *,
        acl_ids: list[str],
        top_k: int | None = None,
        doc_ids: list[str] | None = None,
        channel_stats: dict[str, int] | None = None,
    ) -> list[dict[str, Any]]:
        """返回按 RRF 分数降序的候选列表。

        每条结果形如::

            {"chunk_id": ..., "rrf_score": ..., "sources": ["bm25", "dense"],
             "payload": {...}}

        ``channel_stats`` 非空时会被就地写入两路通道各自的命中数
        （``bm25`` / ``dense``）—— 调试界面要看到「哪一路召回了多少」，
        而不是只看到一个融合后的总数。
        """
        top_k = top_k or settings.retrieve_top_k

        # 无任何可见主体 → 直接空结果，绝不落到"默认全放行"
        if not acl_ids:
            logger.warning("用户无任何可见主体，按默认拒绝策略返回空结果")
            return []

        vector = await self.embeddings.embed_query(query)

        bm25_hits, dense_hits = await asyncio.gather(
            self.es.search(query, top_k=top_k, acl_ids=acl_ids, doc_ids=doc_ids),
            self.qdrant.search(
                vector, top_k=top_k, acl_ids=acl_ids, doc_ids=doc_ids
            ),
            return_exceptions=True,
        )

        # 单路失败不应让整次检索失败 —— 降级为单路召回
        if isinstance(bm25_hits, BaseException):
            logger.exception("BM25 通道失败，降级为纯向量检索: %s", bm25_hits)
            bm25_hits = []
        if isinstance(dense_hits, BaseException):
            logger.exception("向量通道失败，降级为纯关键词检索: %s", dense_hits)
            dense_hits = []

        if channel_stats is not None:
            channel_stats["bm25"] = len(bm25_hits)
            channel_stats["dense"] = len(dense_hits)

        merged = rrf_merge(
            [bm25_hits, dense_hits],
            source_names=["bm25", "dense"],
            k=settings.rrf_k,
        )
        logger.debug(
            "混合检索: bm25=%d dense=%d 融合后=%d",
            len(bm25_hits),
            len(dense_hits),
            len(merged),
        )
        return merged
