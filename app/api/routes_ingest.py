"""文档摄入与检索调试接口。"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile

from app.api.deps import (
    LazyAudit,
    PERM_AUDIT_READ,
    PERM_DOC_WRITE,
    AuthedUser,
    get_current_user,
    require_permission,
)
from app.api.schemas import (
    ChunkSource,
    DocumentInfo,
    IngestResponse,
    RetrievalTrace,
    SearchHit,
    SearchRequest,
    SearchResponse,
)
from app.config import settings
from app.core.timing import elapsed_ms
from app.ingestion.pipeline import IngestionPipeline
from app.retrieval.acl import build_acl_ids
from app.storage.audit_repo import AuditRepository
from app.storage.factory import get_document_repo

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/v1", tags=["documents"])

_audit = LazyAudit()

_pipeline: IngestionPipeline | None = None


def get_pipeline() -> IngestionPipeline:
    global _pipeline
    if _pipeline is None:
        _pipeline = IngestionPipeline()
    return _pipeline


@router.post("/documents", response_model=IngestResponse)
async def upload_document(
    file: UploadFile = File(...),
    acl_ids: str = Form(default="public"),
    prefer_parser: str | None = Form(default=None),
    force: bool = Form(default=False),
    user: AuthedUser = Depends(require_permission(PERM_DOC_WRITE)),
) -> IngestResponse:
    """上传文档并建立索引（需要 doc:write 权限）。

    ``acl_ids`` 逗号分隔，控制该文档对哪些主体可见。
    """
    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="空文件")

    acl = [a.strip() for a in acl_ids.split(",") if a.strip()] or ["public"]

    try:
        result = await get_pipeline().ingest(
            data,
            file.filename or "unnamed",
            acl_ids=acl,
            prefer_parser=prefer_parser,
            force=force,
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("摄入失败")
        raise HTTPException(status_code=500, detail=f"摄入失败: {exc}") from exc

    await _audit.log(
        user_id=user.user_id, username=user.username, action="upload",
        target=result.doc_id, detail={"filename": result.filename, "chunks": result.chunk_count},
    )
    return IngestResponse(
        doc_id=result.doc_id,
        filename=result.filename,
        status=result.status,
        chunk_count=result.chunk_count,
        skipped=result.skipped,
        parser=result.parser,
        error=result.error,
        timings=result.timings,
    )


@router.get("/documents", response_model=list[DocumentInfo])
async def list_documents(limit: int = 100) -> list[DocumentInfo]:
    try:
        records = await get_document_repo().list_documents(limit=limit)
    except Exception as exc:  # noqa: BLE001
        # 文档库不可达时要说清楚是哪个依赖，而不是甩一个 500 Internal Server Error
        logger.exception("读取文档列表失败")
        raise HTTPException(
            status_code=503,
            detail=f"无法读取文档库（PostgreSQL）：{exc}",
        ) from exc
    return [
        DocumentInfo(
            doc_id=r.doc_id,
            filename=r.filename,
            title=r.title,
            status=r.status,
            version=r.version,
            chunk_count=r.chunk_count,
            parser=r.parser,
            acl_ids=r.acl_ids,
        )
        for r in records
    ]


@router.delete("/documents/{doc_id}")
async def delete_document(
    doc_id: str,
    user: AuthedUser = Depends(require_permission(PERM_DOC_WRITE)),
) -> dict[str, str]:
    await get_pipeline().delete(doc_id)
    await _audit.log(
        user_id=user.user_id, username=user.username, action="delete_document", target=doc_id
    )
    return {"status": "deleted", "doc_id": doc_id}


@router.get("/chunks/{chunk_id}", response_model=ChunkSource)
async def get_chunk_source(
    chunk_id: str,
    user: AuthedUser = Depends(get_current_user),
) -> ChunkSource:
    """取 chunk 原文 —— 引用要能回到出处，而不只是回到一句摘要。

    权限与检索期完全一致：ACL 下推给 Qdrant，
    越权访问与不存在返回同一个 404（403 会泄露 chunk 是否存在）。
    """
    from app.indexing.factory import get_vector_store

    acl_ids = build_acl_ids(user)

    try:
        found = await get_vector_store().get_chunk(chunk_id, acl_ids=acl_ids)
    except Exception as exc:  # noqa: BLE001
        logger.exception("读取 chunk 原文失败")
        raise HTTPException(
            status_code=503,
            detail=f"无法读取向量库（Qdrant）：{exc}",
        ) from exc

    if not found:
        raise HTTPException(status_code=404, detail="未找到该片段，或你无权查看")

    payload = found.get("payload") or {}
    await _audit.log(
        user_id=user.user_id, username=user.username, action="view_chunk", target=chunk_id
    )
    return ChunkSource(
        chunk_id=chunk_id,
        doc_id=payload.get("doc_id", ""),
        filename=payload.get("filename", ""),
        heading_text=payload.get("heading_text", ""),
        page=payload.get("page"),
        text=payload.get("text", ""),
    )


@router.post("/search", response_model=SearchResponse)
async def search(
    req: SearchRequest,
    user: AuthedUser = Depends(require_permission(PERM_AUDIT_READ)),
) -> SearchResponse:
    """只检索不生成 —— 调参、评估、排障用（需 audit:read 权限，即管理员）。

    v2.0 起身份来自 token；SearchRequest 里的 user 字段已废弃删除。
    """
    import time

    from app.core.reranker import get_rerank_service
    from app.indexing.hybrid import HybridRetriever

    acl_ids = build_acl_ids(user)

    retriever = HybridRetriever()
    channel_stats: dict[str, int] = {}

    t0 = time.perf_counter()
    candidates = await retriever.search(
        req.query,
        acl_ids=acl_ids,
        doc_ids=req.doc_ids,
        channel_stats=channel_stats,
    )
    retrieve_ms = elapsed_ms(t0)

    if not candidates:
        return SearchResponse(
            hits=[],
            queries=[req.query],
            retrieval=RetrievalTrace(
                bm25_hits=channel_stats.get("bm25", 0),
                dense_hits=channel_stats.get("dense", 0),
                merged=0,
                timings={"retrieve": retrieve_ms},
            ),
        )

    payloads = [c.get("payload") or {} for c in candidates]
    documents = [
        (f"{p.get('heading_text', '')}\n{p.get('text', '')}").strip() for p in payloads
    ]

    t1 = time.perf_counter()
    ranked = await get_rerank_service().rerank(req.query, documents, top_n=req.top_k)
    rerank_ms = elapsed_ms(t1)

    hits: list[SearchHit] = []
    for idx, score in ranked:
        item = candidates[idx]
        payload = item.get("payload") or {}
        hits.append(
            SearchHit(
                chunk_id=item.get("chunk_id", ""),
                filename=payload.get("filename", ""),
                heading_text=payload.get("heading_text", ""),
                rrf_score=item.get("rrf_score", 0.0),
                rerank_score=score,
                sources=item.get("sources", []),
                snippet=(payload.get("text") or "")[:300],
            )
        )

    return SearchResponse(
        hits=hits,
        queries=[req.query],
        retrieval=RetrievalTrace(
            bm25_hits=channel_stats.get("bm25", 0),
            dense_hits=channel_stats.get("dense", 0),
            merged=len(candidates),
            reranked=len(hits),
            timings={"retrieve": retrieve_ms, "rerank": rerank_ms},
        ),
    )
