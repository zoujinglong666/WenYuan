"""用户账户仓库（SQLite）—— 本地账号体系的服务端存储。

企业级约定：
- 密码只存 bcrypt 哈希（passlib），永不存明文；
- refresh token 只存 SHA-256 哈希，泄露库文件也无法直接换取访问令牌；
- 角色存 JSON 数组（RBAC 可扩展），而非布尔 is_admin。

接口刻意保持存储无关 —— 生产切 PostgreSQL 时只需换实现，路由层无感。
"""

from __future__ import annotations

import json
import logging
import uuid
from contextlib import asynccontextmanager
from hashlib import sha256
from pathlib import Path
from typing import Any

import aiosqlite

from app.config import settings

logger = logging.getLogger(__name__)


@asynccontextmanager
async def _conn(path: Path):
    async with aiosqlite.connect(path) as conn:
        await conn.execute("PRAGMA busy_timeout=5000")
        yield conn


SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id            TEXT PRIMARY KEY,
    username      TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    display_name  TEXT NOT NULL DEFAULT '',
    departments   TEXT NOT NULL DEFAULT '[]',
    roles         TEXT NOT NULL DEFAULT '["employee"]',
    disabled      INTEGER NOT NULL DEFAULT 0,
    created_at    TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    last_login_at TEXT
);
CREATE TABLE IF NOT EXISTS refresh_tokens (
    token_hash TEXT PRIMARY KEY,
    user_id    TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    exp        TEXT NOT NULL,
    revoked    INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);
CREATE INDEX IF NOT EXISTS idx_refresh_tokens_user ON refresh_tokens (user_id, revoked);
"""


class UserRepository:
    """SQLite 版用户仓库：账户 + refresh token 两张表。"""

    def __init__(self, path: str | None = None) -> None:
        store_dir = Path(settings.local_store_dir)
        store_dir.mkdir(parents=True, exist_ok=True)
        self.path = Path(path or (store_dir / "users.db"))

    async def init_schema(self) -> None:
        async with _conn(self.path) as conn:
            await conn.executescript(SCHEMA)
            await conn.execute("PRAGMA journal_mode=WAL")
            await conn.commit()
        logger.info("SQLite 用户表结构已就绪: %s", self.path)

    # ---------------- 账户 ----------------

    async def create(
        self,
        *,
        username: str,
        password_hash: str,
        display_name: str = "",
        departments: list[str] | None = None,
        roles: list[str] | None = None,
        disabled: bool = False,
    ) -> dict[str, Any]:
        user_id = uuid.uuid4().hex
        async with _conn(self.path) as conn:
            conn.row_factory = aiosqlite.Row
            await conn.execute(
                "INSERT INTO users (id, username, password_hash, display_name, departments, roles, disabled) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    user_id,
                    username,
                    password_hash,
                    display_name or username,
                    json.dumps(departments or [], ensure_ascii=False),
                    json.dumps(roles or ["employee"], ensure_ascii=False),
                    1 if disabled else 0,
                ),
            )
            await conn.commit()
        row = await self.get_by_username(username)
        assert row is not None
        return row

    async def get_by_username(self, username: str) -> dict[str, Any] | None:
        async with _conn(self.path) as conn:
            conn.row_factory = aiosqlite.Row
            cur = await conn.execute("SELECT * FROM users WHERE username = ?", (username,))
            row = await cur.fetchone()
        return self._to_user(dict(row)) if row else None

    async def get(self, user_id: str) -> dict[str, Any] | None:
        async with _conn(self.path) as conn:
            conn.row_factory = aiosqlite.Row
            cur = await conn.execute("SELECT * FROM users WHERE id = ?", (user_id,))
            row = await cur.fetchone()
        return self._to_user(dict(row)) if row else None

    async def list(self) -> list[dict[str, Any]]:
        async with _conn(self.path) as conn:
            conn.row_factory = aiosqlite.Row
            cur = await conn.execute(
                "SELECT * FROM users ORDER BY created_at DESC"
            )
            rows = await cur.fetchall()
        return [self._to_user(dict(r)) for r in rows]

    async def set_disabled(self, user_id: str, disabled: bool) -> None:
        async with _conn(self.path) as conn:
            await conn.execute(
                "UPDATE users SET disabled = ? WHERE id = ?", (int(disabled), user_id)
            )
            await conn.commit()

    async def touch_last_login(self, user_id: str) -> None:
        async with _conn(self.path) as conn:
            await conn.execute(
                "UPDATE users SET last_login_at = datetime('now', 'localtime') WHERE id = ?",
                (user_id,),
            )
            await conn.commit()

    async def count(self) -> int:
        async with _conn(self.path) as conn:
            cur = await conn.execute("SELECT COUNT(*) FROM users")
            (n,) = await cur.fetchone()
        return int(n)

    # ---------------- refresh token ----------------

    @staticmethod
    def hash_token(token: str) -> str:
        return sha256(token.encode("utf-8")).hexdigest()

    async def save_refresh_token(self, *, token: str, user_id: str, exp: str) -> None:
        """只存哈希：库文件泄露也换不出可用 token。"""
        async with _conn(self.path) as conn:
            await conn.execute(
                "INSERT INTO refresh_tokens (token_hash, user_id, exp) VALUES (?, ?, ?)",
                (self.hash_token(token), user_id, exp),
            )
            await conn.commit()

    async def get_refresh_token(self, token: str) -> dict[str, Any] | None:
        async with _conn(self.path) as conn:
            conn.row_factory = aiosqlite.Row
            cur = await conn.execute(
                "SELECT * FROM refresh_tokens WHERE token_hash = ?", (self.hash_token(token),)
            )
            row = await cur.fetchone()
        return dict(row) if row else None

    async def revoke_refresh_token(self, token: str) -> None:
        async with _conn(self.path) as conn:
            await conn.execute(
                "UPDATE refresh_tokens SET revoked = 1 WHERE token_hash = ?",
                (self.hash_token(token),),
            )
            await conn.commit()

    async def revoke_all_user_tokens(self, user_id: str) -> None:
        """禁用账号时统一撤销其全部 refresh token。"""
        async with _conn(self.path) as conn:
            await conn.execute(
                "UPDATE refresh_tokens SET revoked = 1 WHERE user_id = ?", (user_id,)
            )
            await conn.commit()

    @staticmethod
    def _to_user(row: dict[str, Any]) -> dict[str, Any]:
        row["departments"] = json.loads(row.get("departments") or "[]")
        row["roles"] = json.loads(row.get("roles") or "[]")
        row["disabled"] = bool(row.get("disabled"))
        return row
