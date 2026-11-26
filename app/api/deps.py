"""鉴权依赖 —— FastAPI 路由取身份与权限检查的统一入口。

身份单一来源 = Bearer access token（标准 claims），绝不信任客户端自报的
user_id/departments/roles。每次请求回查用户表确认账号未被禁用 —— token
短命（默认 30 分钟）+ 服务端状态校验，双保险。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordBearer

from app.core.auth import decode_token
from app.retrieval.acl import UserContext
from app.storage.audit_repo import AuditRepository
from app.storage.user_repo import UserRepository

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/v1/auth/login", auto_error=False)


# ---------------- 权限常量（RBAC） ----------------

PERM_DOC_WRITE = "doc:write"        # 文档上传/删除/重建索引
PERM_AUDIT_READ = "audit:read"      # 审计日志查看
PERM_USER_MANAGE = "user:manage"    # 用户创建/禁用

#: 角色 → 权限映射。管理员全含；后续新增角色在此登记，路由层只引用常量。
ROLE_PERMISSIONS: dict[str, set[str]] = {
    "admin": {PERM_DOC_WRITE, PERM_AUDIT_READ, PERM_USER_MANAGE},
    "employee": set(),
}


def user_permissions(roles: list[str]) -> set[str]:
    perms: set[str] = set()
    for role in roles:
        perms |= ROLE_PERMISSIONS.get(role, set())
    return perms


@dataclass
class AuthedUser(UserContext):
    """带鉴权信息的用户上下文（UserContext 供 ACL 使用）。"""

    username: str = ""
    display_name: str = ""
    is_admin: bool = False
    permissions: set[str] = field(default_factory=set)


async def get_user_repo() -> UserRepository:
    """按请求构造：仓库对象无状态（连接在方法内短开），路径读当前配置 ——
    避免 import 时绑定存储路径导致测试隔离失效（模块级单例的教训）。"""
    return UserRepository()


async def get_audit_repo() -> AuditRepository:
    return AuditRepository()


class LazyAudit:
    """审计仓库惰性代理：属性访问时按当前配置构造（构造只是拼路径，零成本）。

    用于路由模块级 ``_audit`` —— 避免在 import 时绑定存储路径，
    测试 monkeypatch settings 后依然隔离正确。
    """

    def __getattr__(self, name):
        return getattr(AuditRepository(), name)


async def get_current_user(request: Request) -> AuthedUser:
    """从 Bearer token 解析当前用户。无效/过期/被禁用一律 401。"""
    token = await oauth2_scheme(request)
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="未登录或凭证缺失",
            headers={"WWW-Authenticate": "Bearer"},
        )
    user = await _load_user(token)
    return user


async def _load_user(token: str) -> AuthedUser:
    payload = decode_token(token, expected_kind="access")
    if not payload:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="登录已过期，请重新登录",
            headers={"WWW-Authenticate": "Bearer"},
        )
    repo = UserRepository()
    user = await repo.get(payload["sub"])
    if user is None or user["disabled"]:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="账号不存在或已被禁用",
            headers={"WWW-Authenticate": "Bearer"},
        )
    perms = user_permissions(user["roles"])
    return AuthedUser(
        user_id=user["id"],
        username=user["username"],
        display_name=user.get("display_name") or user["username"],
        departments=user["departments"],
        roles=user["roles"],
        is_admin="admin" in user["roles"],
        permissions=perms,
    )


def require_permission(perm: str):
    """权限检查依赖工厂：require_permission(PERM_DOC_WRITE)。"""

    async def _check(user: AuthedUser = Depends(get_current_user)) -> AuthedUser:
        if perm not in user.permissions:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="没有执行该操作的权限",
            )
        return user

    return _check


def client_meta(request: Request) -> dict[str, str]:
    """审计埋点用的客户端元信息。"""
    return {
        "ip": request.client.host if request.client else "",
        "user_agent": request.headers.get("user-agent", ""),
    }


def to_user_dict(user: dict[str, Any]) -> dict[str, Any]:
    """对外输出的用户信息（去密码哈希）。"""
    return {
        "id": user["id"],
        "username": user["username"],
        "display_name": user.get("display_name") or user["username"],
        "departments": user.get("departments", []),
        "roles": user.get("roles", []),
        "disabled": user.get("disabled", False),
        "created_at": user.get("created_at"),
        "last_login_at": user.get("last_login_at"),
    }
