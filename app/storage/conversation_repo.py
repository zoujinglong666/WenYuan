"""会话持久化仓库（SQLite）—— 聊天记录的服务端存储。

为什么放服务端而不是 localStorage：浏览器端存储清缓存即丢、多端不同步，
企业级主流是前端只持有 conversation_id，历史一律从后端拉取。
接口刻意保持存储无关 —— 生产切 PostgreSQL 时只需换实现，路由层无感。

数据模型与契约见 docs/spec/spec.md RQ-1。
"""

from __future__ import annotations

import json
import logging
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import aiosqlite

from app.config import settings

logger = logging.getLogger(__name__)


@asynccontextmanager
async def _conn(path: Path):
    """短连接 + busy_timeout。

    WAL 在 init_schema 里设置一次（库级持久）；busy_timeout 是连接级的，
    让并发写等锁 5s 而不是直接抛 database is locked。
    """
    async with aiosqlite.connect(path) as conn:
        await conn.execute("PRAGMA busy_timeout=5000")
        yield conn

SCHEMA = """
CREATE TABLE IF NOT EXISTS conversations (
    id         TEXT PRIMARY KEY,
    user_id    TEXT NOT NULL,
    title      TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);
CREATE INDEX IF NOT EXISTS idx_conversations_user ON conversations (user_id, updated_at DESC);

CREATE TABLE IF NOT EXISTS messages (
    seq             INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    role            TEXT NOT NULL,
    content         TEXT NOT NULL,
    citations       TEXT NOT NULL DEFAULT '[]',
    created_at      TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);
CREATE INDEX IF NOT EXISTS idx_messages_conversation ON messages (conversation_id, seq);
"""


class ConversationRepository:
    """SQLite 版会话仓库。单文件、零依赖，与文档登记表分文件互不干扰。"""

    def __init__(self, path: str | None = None) -> None:
        store_dir = Path(settings.local_store_dir)
        store_dir.mkdir(parents=True, exist_ok=True)
        self.path = Path(path or (store_dir / "conversations.db"))

    async def init_schema(self) -> None:
        async with _conn(self.path) as conn:
            await conn.executescript(SCHEMA)
            # WAL：读写不再互斥 —— 聊天写入与历史查询并发时不互相卡死
            await conn.execute("PRAGMA journal_mode=WAL")
            # 一次性迁移：历史行是 UTC（datetime('now')），统一转成本地时间。
            # schema_migrations 里登记过的实例不再重复执行，避免二次 +8h。
            await conn.execute(
                "CREATE TABLE IF NOT EXISTS schema_migrations ("
                "  name TEXT PRIMARY KEY,"
                "  applied_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))"
                ")"
            )
            cur = await conn.execute(
                "INSERT OR IGNORE INTO schema_migrations (name) VALUES ('tz_localtime')"
            )
            if cur.rowcount > 0:
                await conn.execute(
                    "UPDATE conversations SET "
                    "created_at = datetime(created_at, 'localtime'), "
                    "updated_at = datetime(updated_at, 'localtime')"
                )
                await conn.execute(
                    "UPDATE messages SET created_at = datetime(created_at, 'localtime')"
                )
                logger.info("会话时间戳已从 UTC 迁移为本地时间")
            await conn.commit()
        logger.info("SQLite 会话表结构已就绪: %s", self.path)

    async def create(self, *, user_id: str, title: str = "新对话") -> dict[str, Any]:
        conv_id = uuid.uuid4().hex
        async with _conn(self.path) as conn:
            conn.row_factory = aiosqlite.Row
            await conn.execute(
                # 时间显式取本地时间：老库的列默认值仍是 UTC（SQLite 改不了列默认值），
                # 靠默认值会继续写 UTC，必须在这里覆盖
                "INSERT INTO conversations (id, user_id, title, created_at, updated_at) "
                "VALUES (?, ?, ?, datetime('now', 'localtime'), datetime('now', 'localtime'))",
                (conv_id, user_id, title),
            )
            await conn.commit()
            row = await self._fetch(conn, conv_id, user_id)
        return row

    async def list(self, *, user_id: str, limit: int = 50) -> list[dict[str, Any]]:
        limit = max(1, min(limit, 100))
        async with _conn(self.path) as conn:
            conn.row_factory = aiosqlite.Row
            cur = await conn.execute(
                """
                SELECT c.id, c.title, c.created_at, c.updated_at,
                       COUNT(m.seq) AS message_count
                FROM conversations c
                LEFT JOIN messages m ON m.conversation_id = c.id
                WHERE c.user_id = ?
                GROUP BY c.id
                ORDER BY c.updated_at DESC
                LIMIT ?
                """,
                (user_id, limit),
            )
            return [dict(r) for r in await cur.fetchall()]

    async def get(self, conv_id: str, *, user_id: str) -> dict[str, Any] | None:
        async with _conn(self.path) as conn:
            conn.row_factory = aiosqlite.Row
            row = await self._fetch(conn, conv_id, user_id)
            if row is None:
                return None
            cur = await conn.execute(
                "SELECT seq, role, content, citations, created_at "
                "FROM messages WHERE conversation_id = ? ORDER BY seq",
                (conv_id,),
            )
            messages = []
            for r in await cur.fetchall():
                msg = dict(r)
                try:
                    msg["citations"] = json.loads(msg["citations"])
                except (TypeError, ValueError):
                    msg["citations"] = []
                messages.append(msg)
            row["messages"] = messages
            return row

    async def delete(self, conv_id: str, *, user_id: str) -> bool:
        async with _conn(self.path) as conn:
            cur = await conn.execute(
                "DELETE FROM conversations WHERE id = ? AND user_id = ?",
                (conv_id, user_id),
            )
            await conn.commit()
            return cur.rowcount > 0

    async def append_message(
        self,
        conv_id: str,
        *,
        user_id: str,
        role: str,
        content: str,
        citations: list[dict[str, Any]] | None = None,
    ) -> bool:
        """追加一条消息并刷新会话 updated_at。

        首条用户消息顺手补成会话标题 —— 比空泛的「新对话」可辨识得多。
        返回 False 表示会话不存在（或非本人），调用方据此回落新会话。
        """
        async with _conn(self.path) as conn:
            conn.row_factory = aiosqlite.Row
            row = await self._fetch(conn, conv_id, user_id)
            if row is None:
                return False
            await conn.execute(
                "INSERT INTO messages (conversation_id, role, content, citations) "
                "VALUES (?, ?, ?, ?)",
                (conv_id, role, content, json.dumps(citations or [], ensure_ascii=False)),
            )
            if role == "user" and row["title"] == "新对话":
                title = content.strip().replace("\n", " ")[:24]
                await conn.execute(
                    "UPDATE conversations SET title = ?, "
                    "updated_at = datetime('now', 'localtime') WHERE id = ?",
                    (title or "新对话", conv_id),
                )
            else:
                await conn.execute(
                    "UPDATE conversations SET updated_at = datetime('now', 'localtime') WHERE id = ?",
                    (conv_id,),
                )
            await conn.commit()
            return True

    @staticmethod
    async def _fetch(conn: aiosqlite.Connection, conv_id: str, user_id: str) -> dict[str, Any] | None:
        cur = await conn.execute(
            "SELECT id, user_id, title, created_at, updated_at "
            "FROM conversations WHERE id = ? AND user_id = ?",
            (conv_id, user_id),
        )
        row = await cur.fetchone()
        return dict(row) if row else None
