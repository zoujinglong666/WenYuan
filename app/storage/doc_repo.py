"""PostgreSQL 文档登记表：记录文档元数据、解析状态与版本。

向量与正文分别在 Qdrant / ES，这里只存"文档级"的事实：
它是什么、解析成功没有、内容 hash 是多少、当前有效版本是哪个。

内容 hash 是增量索引的基础 —— hash 没变就跳过重解析，
这是"刚发的文件当天能被问到"的前提。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import psycopg
from psycopg.rows import dict_row

from app.config import settings

logger = logging.getLogger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS documents (
    doc_id        TEXT PRIMARY KEY,
    filename      TEXT NOT NULL,
    title         TEXT,
    object_key    TEXT,
    content_hash  TEXT NOT NULL,
    size_bytes    BIGINT,
    parser        TEXT,
    version       INTEGER NOT NULL DEFAULT 1,
    status        TEXT NOT NULL DEFAULT 'pending',
    error         TEXT,
    chunk_count   INTEGER NOT NULL DEFAULT 0,
    acl_ids       JSONB NOT NULL DEFAULT '[]'::jsonb,
    metadata      JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_documents_hash ON documents (content_hash);
CREATE INDEX IF NOT EXISTS idx_documents_status ON documents (status);
"""


@dataclass
class DocumentRecord:
    doc_id: str
    filename: str
    title: str
    object_key: str | None
    content_hash: str
    size_bytes: int
    parser: str
    version: int
    status: str
    chunk_count: int
    acl_ids: list[str]
    metadata: dict


class DocumentRepository:
    def __init__(self) -> None:
        self.dsn = settings.postgres_dsn

    async def _connect(self):
        return await psycopg.AsyncConnection.connect(self.dsn, row_factory=dict_row)

    async def init_schema(self) -> None:
        async with await self._connect() as conn:
            await conn.execute(SCHEMA)
            await conn.commit()
        logger.info("PostgreSQL 表结构已就绪")

    async def upsert(
        self,
        *,
        doc_id: str,
        filename: str,
        title: str,
        object_key: str | None,
        content_hash: str,
        size_bytes: int,
        parser: str,
        status: str,
        chunk_count: int = 0,
        acl_ids: list[str] | None = None,
        metadata: dict | None = None,
        error: str | None = None,
    ) -> None:
        async with await self._connect() as conn:
            await conn.execute(
                """
                INSERT INTO documents (
                    doc_id, filename, title, object_key, content_hash, size_bytes,
                    parser, version, status, error, chunk_count, acl_ids, metadata, updated_at
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, 1, %s, %s, %s, %s, %s, now())
                ON CONFLICT (doc_id) DO UPDATE SET
                    filename = EXCLUDED.filename,
                    title = EXCLUDED.title,
                    object_key = EXCLUDED.object_key,
                    content_hash = EXCLUDED.content_hash,
                    size_bytes = EXCLUDED.size_bytes,
                    parser = EXCLUDED.parser,
                    version = documents.version + 1,
                    status = EXCLUDED.status,
                    error = EXCLUDED.error,
                    chunk_count = EXCLUDED.chunk_count,
                    acl_ids = EXCLUDED.acl_ids,
                    metadata = EXCLUDED.metadata,
                    updated_at = now()
                """,
                (
                    doc_id,
                    filename,
                    title,
                    object_key,
                    content_hash,
                    size_bytes,
                    parser,
                    status,
                    error,
                    chunk_count,
                    _to_json(acl_ids or []),
                    _to_json(metadata or {}),
                ),
            )
            await conn.commit()

    async def get(self, doc_id: str) -> DocumentRecord | None:
        async with await self._connect() as conn:
            row = await (
                await conn.execute(
                    "SELECT * FROM documents WHERE doc_id = %s", (doc_id,)
                )
            ).fetchone()
        return _to_record(row) if row else None

    async def get_by_hash(self, content_hash: str) -> DocumentRecord | None:
        async with await self._connect() as conn:
            cur = await conn.execute(
                "SELECT * FROM documents WHERE content_hash = %s AND status = 'ready' LIMIT 1",
                (content_hash,),
            )
            row = await cur.fetchone()
        return _to_record(row) if row else None

    async def list_documents(self, limit: int = 100) -> list[DocumentRecord]:
        async with await self._connect() as conn:
            cur = await conn.execute(
                "SELECT * FROM documents ORDER BY updated_at DESC LIMIT %s", (limit,)
            )
            rows = await cur.fetchall()
        return [_to_record(r) for r in rows]

    async def ping(self) -> None:
        """存活探针：连不上就抛异常，由 /ready 记成失败原因。"""
        async with await self._connect() as conn:
            await conn.execute("SELECT 1")

    async def delete(self, doc_id: str) -> None:
        async with await self._connect() as conn:
            await conn.execute("DELETE FROM documents WHERE doc_id = %s", (doc_id,))
            await conn.commit()


def _to_json(value):
    import json

    return json.dumps(value, ensure_ascii=False)


def _to_record(row: dict) -> DocumentRecord:
    import json

    return DocumentRecord(
        doc_id=row["doc_id"],
        filename=row["filename"],
        title=row["title"] or row["filename"],
        object_key=row["object_key"],
        content_hash=row["content_hash"],
        size_bytes=row["size_bytes"] or 0,
        parser=row["parser"] or "",
        version=row["version"],
        status=row["status"],
        chunk_count=row["chunk_count"],
        acl_ids=json.loads(row["acl_ids"]) if row["acl_ids"] else [],
        metadata=json.loads(row["metadata"]) if row["metadata"] else {},
    )
