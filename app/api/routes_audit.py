"""审计日志查询路由（管理员）。"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from app.api.deps import (
    PERM_AUDIT_READ,
    AuthedUser,
    LazyAudit,
    get_current_user,
    require_permission,
)
from app.storage.audit_repo import AuditRepository

router = APIRouter(prefix="/v1/audit", tags=["audit"])

_audit = LazyAudit()


@router.get("", dependencies=[Depends(require_permission(PERM_AUDIT_READ))])
async def query_audit(
    user_id: str | None = None,
    action: str | None = None,
    limit: int = 200,
    _: AuthedUser = Depends(require_permission(PERM_AUDIT_READ)),
) -> list[dict]:
    return await _audit.query(user_id=user_id, action=action, limit=limit)


@router.get("/actions", dependencies=[Depends(require_permission(PERM_AUDIT_READ))])
async def list_actions(_: AuthedUser = Depends(get_current_user)) -> dict:
    """前端筛选项：系统内会落审计的动作枚举。"""
    return {
        "actions": [
            "login", "login_failed", "login_locked", "login_disabled", "logout", "refresh",
            "ask", "search", "view_chunk", "upload", "delete_document",
            "create_user", "user_disabled", "user_enabled",
        ]
    }
