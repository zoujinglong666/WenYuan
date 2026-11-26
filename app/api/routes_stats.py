"""知识库统计接口：给控制台首页与状态页提供真实数据。"""

from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter

from app.config import settings

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/v1", tags=["stats"])


async def _probe(coro, timeout: float):
    """限时探针：拿不到就记为 None + 错误原因，不拖住整个响应。"""
    try:
        return await asyncio.wait_for(coro, timeout=timeout), None
    except Exception as exc:  # noqa: BLE001
        return None, str(exc)[:200]


@router.get("/stats")
async def stats() -> dict[str, object]:
    """文档数、chunk 数与索引规模。

    任一依赖不可用时返回该部分的错误说明而不是整体 500 ——
    控制台要在下游部分故障时仍然显示它知道的东西。

    三个探针并发跑且各自限时：控制台首屏要的是「马上有反应」，
    而不是排队等三个后端依次超时。
    """
    result: dict[str, object] = {
        "documents": None,
        "chunks": None,
        "qdrant_points": None,
        "es_documents": None,
        "errors": {},
    }
    errors: dict[str, str] = {}
    timeout = settings.readiness_probe_timeout

    async def _postgres() -> tuple[int, int]:
        from app.storage.factory import get_document_repo

        records = await get_document_repo().list_documents(limit=1000)
        return len(records), sum(r.chunk_count for r in records)

    async def _qdrant() -> int:
        from app.indexing.factory import get_vector_store

        return await get_vector_store().count()

    async def _es() -> int:
        from app.indexing.factory import get_keyword_store

        return await get_keyword_store().count()

    results = await asyncio.gather(
        _probe(_postgres(), timeout),
        _probe(_qdrant(), timeout),
        _probe(_es(), timeout),
    )
    pg, pg_err = results[0]
    qd, qd_err = results[1]
    es, es_err = results[2]

    if pg is not None:
        result["documents"], result["chunks"] = pg
    else:
        errors["postgres"] = pg_err or "unknown"

    result["qdrant_points"] = qd
    if qd is None:
        errors["qdrant"] = qd_err or "unknown"

    result["es_documents"] = es
    if es is None:
        errors["elasticsearch"] = es_err or "unknown"

    result["errors"] = errors
    result["config"] = {
        "llm_provider": settings.llm_provider,
        "llm_model": settings.llm_model,
        "embedding_model": settings.embedding_model,
        "rerank_model": settings.rerank_model,
        "retrieval": {
            "retrieve_top_k": settings.retrieve_top_k,
            "rerank_top_k": settings.rerank_top_k,
            "rrf_k": settings.rrf_k,
        },
        "acl_enabled": settings.acl_enabled,
    }
    return result
