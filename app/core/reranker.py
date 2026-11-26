"""Rerank 服务（Cross-Encoder 精排）。

内网默认走 TEI 的 /rerank 端点；local 后端用 FlagEmbedding 在进程内加载。

分数约定：**统一 sigmoid 到 [0,1] 的相关概率**。jina/bge 系 reranker
原始输出是未归一化 logit（负值完全正常），直接拿去和阈值比较会全量误杀。
"""

from __future__ import annotations

import asyncio
import logging
import math
from functools import lru_cache

import httpx

from app.config import settings

logger = logging.getLogger(__name__)


def _sigmoid(x: float) -> float:
    return 1.0 / (1.0 + math.exp(-x))


class RerankService:
    async def rerank(
        self, query: str, documents: list[str], top_n: int | None = None
    ) -> list[tuple[int, float]]:
        """返回 [(原始下标, 分数)]，按分数降序。

        分数为 raw logit（未归一化），仅用于相对排序，不要当作绝对置信度。
        """
        if not documents:
            return []
        top_n = top_n or settings.rerank_top_k
        if settings.rerank_backend == "onnx":
            ranked = await asyncio.to_thread(self._rerank_onnx, query, documents, top_n)
        elif settings.rerank_backend == "local":
            ranked = await asyncio.to_thread(self._rerank_local, query, documents, top_n)
        else:
            ranked = await self._rerank_tei(query, documents, top_n)
        # logit → 相关概率（单调变换不影响相对排序，但让阈值有统一语义）
        return [(i, _sigmoid(s)) for i, s in ranked]

    # ---------------- TEI ----------------

    async def _rerank_tei(
        self, query: str, documents: list[str], top_n: int
    ) -> list[tuple[int, float]]:
        async with httpx.AsyncClient(
            base_url=settings.rerank_base_url.rstrip("/"),
            timeout=settings.rerank_timeout,
        ) as client:
            payload = {"model": settings.rerank_model, "query": query, "texts": documents}
            resp = await client.post("/rerank", json=payload)
            resp.raise_for_status()
            data = resp.json()

        # TEI 返回 [{"index": i, "score": s}]，已按分数降序
        results = data if isinstance(data, list) else data.get("results", [])
        return [(int(r["index"]), float(r["score"])) for r in results[:top_n]]

    # ---------------- local ----------------

    def _rerank_onnx(
        self, query: str, documents: list[str], top_n: int
    ) -> list[tuple[int, float]]:
        """ONNX Runtime（int8 量化，进程内 CPU 推理）。"""
        from app.core.onnx_backend import get_onnx_reranker

        scores = get_onnx_reranker().compute(query, documents)
        ranked = sorted(enumerate(scores), key=lambda x: x[1], reverse=True)
        return [(int(i), float(s)) for i, s in ranked[:top_n]]

    def _rerank_local(
        self, query: str, documents: list[str], top_n: int
    ) -> list[tuple[int, float]]:
        model = self._get_local_model()
        scores = model.compute_score([[query, doc] for doc in documents], normalize=False)
        scores = scores.tolist() if hasattr(scores, "tolist") else list(scores)
        ranked = sorted(enumerate(scores), key=lambda x: x[1], reverse=True)
        return [(int(i), float(s)) for i, s in ranked[:top_n]]

    def _get_local_model(self):
        if not hasattr(self, "_local_model") or self._local_model is None:
            from FlagEmbedding import FlagReranker

            logger.info("加载本地 rerank 模型: %s", settings.rerank_model)
            self._local_model = FlagReranker(settings.rerank_model, use_fp16=True)
        return self._local_model


@lru_cache
def get_rerank_service() -> RerankService:
    return RerankService()
