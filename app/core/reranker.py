"""Rerank 服务（Cross-Encoder 精排）。

内网默认走 TEI 的 /rerank 端点；local 后端用 FlagEmbedding 在进程内加载。

分数约定：**统一 sigmoid 到 [0,1] 的相关概率**。jina/bge 系 reranker
原始输出是未归一化 logit（负值完全正常），直接拿去和阈值比较会全量误杀。
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import math
import re
import time
from collections import OrderedDict
from functools import lru_cache

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

#: 精排结果缓存：key = (规范化查询, 文本哈希)。内部知识库重复提问率高
#: （「报销流程」「年假怎么请」），命中即跳过最慢的一次模型推理。
#: 容量与 TTL 常量化 —— 不是调参热区，进 config 徒增心智负担。
_RERANK_CACHE_MAX = 2048
_RERANK_CACHE_TTL = 3600.0


def _sigmoid(x: float) -> float:
    return 1.0 / (1.0 + math.exp(-x))


def _norm_query(q: str) -> str:
    """查询规范化：空白差异不应导致缓存分裂。"""
    return re.sub(r"\s+", " ", q.strip().lower())


class RerankService:
    def __init__(self) -> None:
        # OrderedDict 实现 LRU：尾端最新，超容从头部淘汰
        self._cache: OrderedDict[tuple[str, str], tuple[float, float]] = OrderedDict()

    def _cache_get(self, key: tuple[str, str]) -> float | None:
        entry = self._cache.get(key)
        if entry is None:
            return None
        score, ts = entry
        if time.monotonic() - ts > _RERANK_CACHE_TTL:
            del self._cache[key]
            return None
        self._cache.move_to_end(key)
        return score

    def _cache_put(self, key: tuple[str, str], score: float) -> None:
        self._cache[key] = (score, time.monotonic())
        self._cache.move_to_end(key)
        while len(self._cache) > _RERANK_CACHE_MAX:
            self._cache.popitem(last=False)

    async def rerank(
        self, query: str, documents: list[str], top_n: int | None = None
    ) -> list[tuple[int, float]]:
        """返回 [(原始下标, 分数)]，按分数降序。

        分数为 sigmoid 后的相关概率 [0,1]，可跨请求比较、可入缓存。
        相同 (query, text) 二次请求直接读缓存，不再过模型。
        """
        if not documents:
            return []
        top_n = top_n or settings.rerank_top_k
        qkey = _norm_query(query)

        # 按缓存命中拆分：命中的直接取分，未命中的批量过模型
        doc_keys = [
            (qkey, hashlib.sha1(doc.encode("utf-8")).hexdigest()) for doc in documents
        ]
        scores: dict[int, float] = {}
        missing: list[int] = []
        for i, key in enumerate(doc_keys):
            hit = self._cache_get(key)
            if hit is None:
                missing.append(i)
            else:
                scores[i] = hit

        if missing:
            miss_docs = [documents[i] for i in missing]
            if settings.rerank_backend == "onnx":
                raw = await asyncio.to_thread(self._rerank_onnx, query, miss_docs, len(miss_docs))
            elif settings.rerank_backend == "local":
                raw = await asyncio.to_thread(self._rerank_local, query, miss_docs, len(miss_docs))
            else:
                raw = await self._rerank_tei(query, miss_docs, len(miss_docs))
            # raw 是 [(相对下标, logit)]；sigmoid 后入缓存，供不同 top_n 复用
            for rel_idx, logit in raw:
                i = missing[rel_idx]
                prob = _sigmoid(logit)
                scores[i] = prob
                self._cache_put(doc_keys[i], prob)
            if len(missing) < len(documents):
                logger.info("精排缓存: %d/%d 命中", len(documents) - len(missing), len(documents))

        ranked = sorted(scores.items(), key=lambda x: x[1], reverse=True)
        return ranked[:top_n]

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
