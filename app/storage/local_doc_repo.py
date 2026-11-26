"""本地文档登记表（SQLite）—— 没有 PostgreSQL 时的离线替身。

只存**文档级**的事实：它是什么、解析成功没有、内容 hash、当前版本。
正文与向量分别在本地关键词库 / 本地向量库里。

接口与 :class:`~app.storage.doc_repo.DocumentRepository` 一致，
通过 ``DOC_BACKEND=local`` 启用。生产仍然是 PostgreSQL。
"""

from __future__ import annotations

import json
import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import aiosqlite

from app.config import settings
from app.storage.doc_repo import DocumentRecord

logger = logging.getLogger(__name__)


@asynccontextmanager
async def _conn(path: Path):
    """短连接 + busy_timeout（WAL 在 init_schema 设置一次，库级持久）。"""
    async with aiosqlite.connect(path) as conn:
        await conn.execute("PRAGMA busy_timeout=5000")
        yield conn

SCHEMA = """
CREATE TABLE IF NOT EXISTS documents (
    doc_id        TEXT PRIMARY KEY,
    filename      TEXT NOT NULL,
    title         TEXT,
    object_key    TEXT,
    content_hash  TEXT NOT NULL,
    size_bytes    INTEGER,
    parser        TEXT,
    version       INTEGER NOT NULL DEFAULT 1,
    status        TEXT NOT NULL DEFAULT 'pending',
    error         TEXT,
    chunk_count   INTEGER NOT NULL DEFAULT 0,
    acl_ids       TEXT NOT NULL DEFAULT '[]',
    metadata      TEXT NOT NULL DEFAULT '{}',
    created_at    TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at    TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_documents_hash ON documents (content_hash);
CREATE INDEX IF NOT EXISTS idx_documents_status ON documents (status);
"""


class LocalDocumentRepository:
    """SQLite 版文档登记表。单文件、零依赖，适合单机内网落地。"""

    def __init__(self, path: str | None = None) -> None:
        store_dir = Path(settings.local_store_dir)
        store_dir.mkdir(parents=True, exist_ok=True)
        self.path = Path(path or (store_dir / "documents.db"))

    async def init_schema(self) -> None:
        async with _conn(self.path) as conn:
            conn.row_factory = aiosqlite.Row
            await conn.executescript(SCHEMA)
            # WAL：解析写入与查询并发时不互斥（会话库同理）
            await conn.execute("PRAGMA journal_mode=WAL")
            await conn.commit()
        logger.info("SQLite 文档表结构已就绪: %s", self.path)

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
        async with _conn(self.path) as conn:
            conn.row_factory = aiosqlite.Row
            await conn.execute(
                """
                INSERT INTO documents (
                    doc_id, filename, title, object_key, content_hash, size_bytes,
                    parser, version, status, error, chunk_count, acl_ids, metadata, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?, ?, ?, datetime('now'))
                ON CONFLICT(doc_id) DO UPDATE SET
                    filename = excluded.filename,
                    title = excluded.title,
                    object_key = excluded.object_key,
                    content_hash = excluded.content_hash,
                    size_bytes = excluded.size_bytes,
                    parser = excluded.parser,
                    version = documents.version + 1,
                    status = excluded.status,
                    error = excluded.error,
                    chunk_count = excluded.chunk_count,
                    acl_ids = excluded.acl_ids,
                    metadata = excluded.metadata,
                    updated_at = datetime('now')
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
                    json.dumps(acl_ids or [], ensure_ascii=False),
                    json.dumps(metadata or {}, ensure_ascii=False),
                ),
            )
            await conn.commit()

    async def get(self, doc_id: str) -> DocumentRecord | None:
        async with _conn(self.path) as conn:
            conn.row_factory = aiosqlite.Row
            cur = await conn.execute("SELECT * FROM documents WHERE doc_id = ?", (doc_id,))
            row = await cur.fetchone()
        return _to_record(row) if row else None

    async def get_by_hash(self, content_hash: str) -> DocumentRecord | None:
        async with _conn(self.path) as conn:
            conn.row_factory = aiosqlite.Row
            cur = await conn.execute(
                "SELECT * FROM documents WHERE content_hash = ? AND status = 'ready' LIMIT 1",
                (content_hash,),
            )
            row = await cur.fetchone()
        return _to_record(row) if row else None

    async def list_documents(self, limit: int = 100) -> list[DocumentRecord]:
        async with _conn(self.path) as conn:
            conn.row_factory = aiosqlite.Row
            cur = await conn.execute(
                "SELECT * FROM documents ORDER BY updated_at DESC LIMIT ?", (limit,)
            )
            rows = await cur.fetchall()
        return [_to_record(r) for r in rows]

    async def ping(self) -> None:
        """存活探针：本地 SQLite 能打开就活着。"""
        async with _conn(self.path) as conn:
            conn.row_factory = aiosqlite.Row
            await conn.execute("SELECT 1")

    async def delete(self, doc_id: str) -> None:
        async with _conn(self.path) as conn:
            conn.row_factory = aiosqlite.Row
            await conn.execute("DELETE FROM documents WHERE doc_id = ?", (doc_id,))
            await conn.commit()


def _to_record(row: Any) -> DocumentRecord:
    return DocumentRecord(
        doc_id=row["doc_id"],
        filename=row["filename"],
        title=row["title"] or "",
        object_key=row["object_key"],
        content_hash=row["content_hash"],
        size_bytes=row["size_bytes"] or 0,
        parser=row["parser"] or "",
        version=row["version"],
        status=row["status"],
        chunk_count=row["chunk_count"],
        acl_ids=json.loads(row["acl_ids"] or "[]"),
        metadata=json.loads(row["metadata"] or "{}"),
    )
