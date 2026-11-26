"""审计日志仓库（SQLite，append-only）—— 谁在什么时候做了什么。

企业级约定：
- 只提供 INSERT / SELECT 接口，不暴露 UPDATE/DELETE —— 审计记录不可改写；
- 记录 IP 与 User-Agent，便于事后追溯异常来源；
- 保留策略：90 天。单机版不做自动清理，生产部署应由定时任务按 ts 清理
  （例如 APScheduler / crontab: DELETE FROM audit WHERE ts < date('now','-90 day')）。
"""

from __future__ import annotations

import json
import logging
from contextlib import asynccontextmanager
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
CREATE TABLE IF NOT EXISTS audit (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    ts         TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    user_id    TEXT NOT NULL DEFAULT '',
    username   TEXT NOT NULL DEFAULT '',
    action     TEXT NOT NULL,
    target     TEXT NOT NULL DEFAULT '',
    detail     TEXT NOT NULL DEFAULT '',
    ip         TEXT NOT NULL DEFAULT '',
    user_agent TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_audit_ts ON audit (ts DESC);
CREATE INDEX IF NOT EXISTS idx_audit_user ON audit (user_id, ts DESC);
"""


class AuditRepository:
    """append-only 审计仓库。"""

    def __init__(self, path: str | None = None) -> None:
        store_dir = Path(settings.local_store_dir)
        store_dir.mkdir(parents=True, exist_ok=True)
        self.path = Path(path or (store_dir / "audit.db"))

    async def init_schema(self) -> None:
        async with _conn(self.path) as conn:
            await conn.executescript(SCHEMA)
            await conn.execute("PRAGMA journal_mode=WAL")
            await conn.commit()
        logger.info("SQLite 审计表结构已就绪: %s", self.path)

    async def log(
        self,
        *,
        user_id: str = "",
        username: str = "",
        action: str,
        target: str = "",
        detail: dict[str, Any] | str | None = None,
        ip: str = "",
        user_agent: str = "",
    ) -> None:
        """写一条审计记录。失败只记日志不抛 —— 审计不能阻断业务主流程。"""
        if isinstance(detail, dict):
            detail = json.dumps(detail, ensure_ascii=False)
        try:
            async with _conn(self.path) as conn:
                await conn.execute(
                    "INSERT INTO audit (user_id, username, action, target, detail, ip, user_agent) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (user_id, username, action, target or "", detail or "", ip, user_agent),
                )
                await conn.commit()
        except Exception:  # noqa: BLE001
            logger.exception("审计写入失败 action=%s target=%s", action, target)

    async def query(
        self,
        *,
        user_id: str | None = None,
        action: str | None = None,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        limit = max(1, min(limit, 1000))
        sql = "SELECT * FROM audit"
        conds: list[str] = []
        params: list[Any] = []
        if user_id:
            conds.append("(user_id = ? OR username = ?)")
            params.extend([user_id, user_id])
        if action:
            conds.append("action = ?")
            params.append(action)
        if conds:
            sql += " WHERE " + " AND ".join(conds)
        sql += " ORDER BY id DESC LIMIT ?"
        params.append(limit)
        async with _conn(self.path) as conn:
            conn.row_factory = aiosqlite.Row
            cur = await conn.execute(sql, params)
            rows = await cur.fetchall()
        return [dict(r) for r in rows]
