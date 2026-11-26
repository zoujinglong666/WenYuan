"""认证与用户管理路由。

企业级约定：
- 登录失败不做账号是否存在区分提示（防枚举），统一「用户名或密码错误」；
- 登录防爆破（LoginGuard）；
- refresh token 轮换制：每次刷新旧 token 作废，泄露窗口最小化；
- 全部事件（登录成败/刷新/登出/用户管理）落审计。
"""

from __future__ import annotations

import logging
import time

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field

from app.api.deps import (
    LazyAudit,
    PERM_USER_MANAGE,
    AuthedUser,
    client_meta,
    get_audit_repo,
    get_current_user,
    get_user_repo,
    require_permission,
    to_user_dict,
)
from app.core.auth import (
    create_access_token,
    create_refresh_token,
    decode_token,
    hash_password,
    login_guard,
    password_strength_error,
    refresh_exp_iso,
    verify_password,
)
from app.storage.audit_repo import AuditRepository
from app.storage.user_repo import UserRepository

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/v1/auth", tags=["auth"])

_audit = LazyAudit()


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=128)


class TokenPair(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"


class RefreshRequest(BaseModel):
    refresh_token: str


class LogoutRequest(BaseModel):
    refresh_token: str


class CreateUserRequest(BaseModel):
    username: str = Field(min_length=2, max_length=64, pattern=r"^[A-Za-z0-9_.-]+$")
    password: str = Field(min_length=1, max_length=128)
    display_name: str = Field(default="", max_length=64)
    departments: list[str] = Field(default_factory=list)
    roles: list[str] = Field(default_factory=lambda: ["employee"])


class RegisterRequest(BaseModel):
    """自助注册：账号创建后默认禁用，需管理员审批（启用）后才能登录。"""

    username: str = Field(min_length=2, max_length=64, pattern=r"^[A-Za-z0-9_.-]+$")
    password: str = Field(min_length=1, max_length=128)
    display_name: str = Field(default="", max_length=64)


class PatchUserRequest(BaseModel):
    disabled: bool | None = None


VALID_ROLES = {"admin", "employee"}

# ---------------- 自助注册防滥用 ----------------
# 开放端点直接写用户表，必须限速：单 IP 每小时最多 5 次（含失败尝试）。
_REGISTER_IP_HITS: dict[str, list[float]] = {}
REGISTER_MAX_PER_HOUR = 5
_REGISTER_WINDOW_SECONDS = 3600


def _register_rate_limited(ip: str) -> bool:
    now = time.monotonic()
    hits = [t for t in _REGISTER_IP_HITS.get(ip, []) if now - t < _REGISTER_WINDOW_SECONDS]
    limited = len(hits) >= REGISTER_MAX_PER_HOUR
    hits.append(now)
    _REGISTER_IP_HITS[ip] = hits
    return limited


@router.post("/login")
async def login(body: LoginRequest, request: Request, repo: UserRepository = Depends(get_user_repo)) -> dict:
    meta = client_meta(request)

    if login_guard.is_locked(body.username):
        await _audit.log(
            username=body.username, action="login_locked", ip=meta["ip"], user_agent=meta["user_agent"]
        )
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="失败次数过多，账号已临时锁定，请稍后再试",
        )

    user = await repo.get_by_username(body.username)
    if user is None or not verify_password(body.password, user["password_hash"]):
        login_guard.record_failure(body.username)
        await _audit.log(
            username=body.username, action="login_failed", ip=meta["ip"], user_agent=meta["user_agent"]
        )
        # 防枚举：不区分「用户不存在」与「密码错误」
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="用户名或密码错误")

    if user["disabled"]:
        await _audit.log(
            user_id=user["id"], username=user["username"], action="login_disabled",
            ip=meta["ip"], user_agent=meta["user_agent"],
        )
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="账号已被禁用，请联系管理员")

    login_guard.reset(body.username)
    refresh_token = create_refresh_token(user)
    await repo.save_refresh_token(token=refresh_token, user_id=user["id"], exp=refresh_exp_iso())
    await repo.touch_last_login(user["id"])
    await _audit.log(
        user_id=user["id"], username=user["username"], action="login",
        ip=meta["ip"], user_agent=meta["user_agent"],
    )
    return {
        **TokenPair(access_token=create_access_token(user), refresh_token=refresh_token).model_dump(),
        "user": to_user_dict(user),
    }


@router.post("/register")
async def register(
    body: RegisterRequest, request: Request, repo: UserRepository = Depends(get_user_repo)
) -> dict:
    meta = client_meta(request)

    if _register_rate_limited(meta["ip"]):
        await _audit.log(
            username=body.username, action="register_limited", ip=meta["ip"], user_agent=meta["user_agent"]
        )
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="注册尝试过于频繁，请稍后再试",
        )

    err = password_strength_error(body.password)
    if err:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=err)
    if await repo.get_by_username(body.username):
        # 防枚举与重名同义：自助注册场景直接告知重名（用户名非敏感凭据）
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="用户名已存在，请换一个")

    user = await repo.create(
        username=body.username,
        password_hash=hash_password(body.password),
        display_name=body.display_name,
        roles=["employee"],
        disabled=True,  # 审批制：管理员在「审计与用户」页启用后才能登录
    )
    await _audit.log(
        user_id=user["id"], username=user["username"], action="register",
        ip=meta["ip"], user_agent=meta["user_agent"],
    )
    return {"ok": True, "message": "注册成功，账号待管理员审批，审批通过后即可登录"}


@router.post("/refresh")
async def refresh_tokens(
    body: RefreshRequest, request: Request, repo: UserRepository = Depends(get_user_repo)
) -> dict:
    payload = decode_token(body.refresh_token, expected_kind="refresh")
    if not payload:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="刷新凭证无效或已过期")

    row = await repo.get_refresh_token(body.refresh_token)
    if row is None or row["revoked"]:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="刷新凭证已撤销，请重新登录")

    user = await repo.get(payload["sub"])
    if user is None or user["disabled"]:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="账号不存在或已被禁用")

    # 轮换：旧的立即作废
    await repo.revoke_refresh_token(body.refresh_token)
    new_refresh = create_refresh_token(user)
    await repo.save_refresh_token(token=new_refresh, user_id=user["id"], exp=refresh_exp_iso())
    await _audit.log(
        user_id=user["id"], username=user["username"], action="refresh",
        ip=client_meta(request)["ip"], user_agent=client_meta(request)["user_agent"],
    )
    return {
        **TokenPair(access_token=create_access_token(user), refresh_token=new_refresh).model_dump(),
        "user": to_user_dict(user),
    }


@router.post("/logout")
async def logout(
    body: LogoutRequest, request: Request, repo: UserRepository = Depends(get_user_repo)
) -> dict:
    payload = decode_token(body.refresh_token, expected_kind="refresh")
    await repo.revoke_refresh_token(body.refresh_token)
    if payload:
        meta = client_meta(request)
        await _audit.log(
            user_id=payload["sub"], username=payload.get("username", ""), action="logout",
            ip=meta["ip"], user_agent=meta["user_agent"],
        )
    return {"ok": True}


@router.get("/me")
async def me(user: AuthedUser = Depends(get_current_user)) -> dict:
    return {
        "user_id": user.user_id,
        "username": user.username,
        "display_name": user.display_name,
        "departments": user.departments,
        "roles": user.roles,
        "permissions": sorted(user.permissions),
    }


@router.post("/users", dependencies=[Depends(require_permission(PERM_USER_MANAGE))])
async def create_user(
    body: CreateUserRequest,
    request: Request,
    admin: AuthedUser = Depends(require_permission(PERM_USER_MANAGE)),
    repo: UserRepository = Depends(get_user_repo),
) -> dict:
    err = password_strength_error(body.password)
    if err:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=err)
    bad_roles = set(body.roles) - VALID_ROLES
    if bad_roles:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=f"未知角色: {sorted(bad_roles)}")
    if await repo.get_by_username(body.username):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="用户名已存在")

    user = await repo.create(
        username=body.username,
        password_hash=hash_password(body.password),
        display_name=body.display_name,
        departments=body.departments,
        roles=body.roles,
    )
    meta = client_meta(request)
    await _audit.log(
        user_id=admin.user_id, username=admin.username,
        action="create_user", target=body.username,
        detail={"roles": body.roles, "departments": body.departments},
        ip=meta["ip"], user_agent=meta["user_agent"],
    )
    return to_user_dict(user)


@router.get("/users", dependencies=[Depends(require_permission(PERM_USER_MANAGE))])
async def list_users(repo: UserRepository = Depends(get_user_repo)) -> list[dict]:
    return [to_user_dict(u) for u in await repo.list()]


@router.patch("/users/{user_id}", dependencies=[Depends(require_permission(PERM_USER_MANAGE))])
async def patch_user(
    user_id: str,
    body: PatchUserRequest,
    request: Request,
    admin: AuthedUser = Depends(require_permission(PERM_USER_MANAGE)),
    repo: UserRepository = Depends(get_user_repo),
) -> dict:
    user = await repo.get(user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="用户不存在")
    if user["id"] == admin.user_id and body.disabled:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="不能禁用自己的账号")

    if body.disabled is not None:
        await repo.set_disabled(user_id, body.disabled)
        if body.disabled:
            # 禁用即断活：撤销其全部 refresh token
            await repo.revoke_all_user_tokens(user_id)
        meta = client_meta(request)
        await _audit.log(
            user_id=admin.user_id, username=admin.username,
            action="user_disabled" if body.disabled else "user_enabled", target=user["username"],
            ip=meta["ip"], user_agent=meta["user_agent"],
        )
    updated = await repo.get(user_id)
    assert updated is not None
    return to_user_dict(updated)
