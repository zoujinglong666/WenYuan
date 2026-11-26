"""文档摄入管线：解析 → 切块 → 向量化 → 双引擎入库。

增量索引
--------
- ``doc_id`` 按**文件名**稳定生成：同一份文档更新内容后仍是同一个 doc_id，
  旧 chunk 被删掉重写，版本号 +1。
- ``content_hash`` 按**内容**生成：内容没变就整体跳过，不重复解析与向量化。

这是"刚发的文件当天能被问到"的前提 —— 全量重建索引在真实企业里不可接受。
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass, field

from app.chunking.structure import Chunk, StructureChunker
from app.config import settings
from app.core.embeddings import get_embedding_service
from app.core.timing import elapsed_ms
from app.indexing.factory import get_keyword_store, get_vector_store
from app.parsing.registry import registry
from app.storage.factory import get_document_repo, get_object_store

logger = logging.getLogger(__name__)


@dataclass
class IngestResult:
    doc_id: str
    filename: str
    status: str
    chunk_count: int = 0
    skipped: bool = False
    version: int = 1
    parser: str = ""
    error: str | None = None
    timings: dict[str, float] = field(default_factory=dict)


class IngestionPipeline:
    def __init__(self) -> None:
        self.chunker = StructureChunker()
        self.embeddings = get_embedding_service()
        self.qdrant = get_vector_store()
        self.es = get_keyword_store()
        self.repo = get_document_repo()
        self.objects = get_object_store()

    async def ingest(
        self,
        file_bytes: bytes,
        filename: str,
        *,
        acl_ids: list[str] | None = None,
        prefer_parser: str | None = None,
        force: bool = False,
        extra_metadata: dict | None = None,
    ) -> IngestResult:
        import time

        timings: dict[str, float] = {}
        content_hash = hashlib.sha256(file_bytes).hexdigest()
        doc_id = _doc_id_for(filename)

        # ---- 增量短路：内容未变且已就绪 ----
        if not force:
            existing = await self.repo.get(doc_id)
            if existing and existing.content_hash == content_hash and existing.status == "ready":
                logger.info("文档 %s 内容未变，跳过重解析", filename)
                return IngestResult(
                    doc_id=doc_id,
                    filename=filename,
                    status=existing.status,
                    chunk_count=existing.chunk_count,
                    skipped=True,
                    version=existing.version,
                    parser=existing.parser,
                    timings=timings,
                )

        acl = acl_ids or ["public"]

        # ---- 原文归档 ----
        t0 = time.perf_counter()
        object_key = f"{doc_id}/{content_hash[:12]}/{filename}"
        try:
            await _run_sync(self.objects.put, object_key, file_bytes)
        except Exception as exc:  # noqa: BLE001
            logger.warning("原文归档失败（不阻断索引）: %s", exc)
            object_key = None
        timings["archive"] = elapsed_ms(t0)

        # ---- 解析 ----
        t0 = time.perf_counter()
        try:
            parsed = await registry.parse(
                file_bytes, filename, doc_id=doc_id, prefer=prefer_parser
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception("解析失败: %s", filename)
            await self.repo.upsert(
                doc_id=doc_id,
                filename=filename,
                title=filename,
                object_key=object_key,
                content_hash=content_hash,
                size_bytes=len(file_bytes),
                parser="",
                status="failed",
                error=str(exc)[:2000],
                acl_ids=acl,
            )
            return IngestResult(
                doc_id=doc_id,
                filename=filename,
                status="failed",
                error=str(exc)[:500],
                timings=timings,
            )
        timings["parse"] = elapsed_ms(t0)

        # ---- 切块 ----
        t0 = time.perf_counter()
        chunks = self.chunker.chunk(
            parsed,
            acl_ids=acl,
            extra_metadata={"source": "upload", **(extra_metadata or {})},
        )
        timings["chunk"] = elapsed_ms(t0)

        if not chunks:
            await self.repo.upsert(
                doc_id=doc_id,
                filename=filename,
                title=parsed.title,
                object_key=object_key,
                content_hash=content_hash,
                size_bytes=len(file_bytes),
                parser=parsed.parser,
                status="failed",
                error="解析成功但未产出任何 chunk",
                acl_ids=acl,
            )
            return IngestResult(
                doc_id=doc_id,
                filename=filename,
                status="failed",
                error="解析成功但未产出任何 chunk",
                timings=timings,
            )

        # ---- 向量化 ----
        t0 = time.perf_counter()
        vectors = await self.embeddings.embed_documents([c.text for c in chunks])
        timings["embed"] = elapsed_ms(t0)

        # ---- 双引擎入库 ----
        t0 = time.perf_counter()
        payloads = [c.to_payload() for c in chunks]

        # 先删旧 chunk 再写新的，避免新旧版本混杂
        await self.qdrant.delete_by_doc(doc_id)
        await self.es.delete_by_doc(doc_id)

        await self.qdrant.upsert(payloads, vectors)
        await self.es.bulk_index(payloads)
        await self.es.refresh()
        timings["index"] = elapsed_ms(t0)

        # ---- 登记 ----
        await self.repo.upsert(
            doc_id=doc_id,
            filename=filename,
            title=parsed.title,
            object_key=object_key,
            content_hash=content_hash,
            size_bytes=len(file_bytes),
            parser=parsed.parser,
            status="ready",
            chunk_count=len(chunks),
            acl_ids=acl,
            metadata={
                "heading_count": len({c.heading_text for c in chunks}),
                "table_chunks": sum(1 for c in chunks if c.is_table),
                **(extra_metadata or {}),
            },
        )

        logger.info(
            "摄入完成: %s → %d chunks (解析 %.0fms, 向量化 %.0fms, 入库 %.0fms)",
            filename,
            len(chunks),
            timings.get("parse", 0),
            timings.get("embed", 0),
            timings.get("index", 0),
        )
        return IngestResult(
            doc_id=doc_id,
            filename=filename,
            status="ready",
            chunk_count=len(chunks),
            parser=parsed.parser,
            timings=timings,
        )

    async def delete(self, doc_id: str) -> None:
        await self.qdrant.delete_by_doc(doc_id)
        await self.es.delete_by_doc(doc_id)
        await self.es.refresh()
        await self.repo.delete(doc_id)


def _doc_id_for(filename: str) -> str:
    """doc_id 按文件名稳定生成，保证同一文档更新内容后仍是同一个 id。"""
    return hashlib.sha1(filename.encode("utf-8")).hexdigest()[:16]


async def _run_sync(func, *args, **kwargs):
    """把同步函数丢到线程池跑。

    对象存储（MinIO / 本地文件）是同步客户端，直接在事件循环里调会卡住整个服务。
    """
    import asyncio

    return await asyncio.to_thread(func, *args, **kwargs)
