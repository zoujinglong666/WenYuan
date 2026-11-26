"""鉴权服务 —— 密码哈希、JWT 双 token、登录防爆破、初始管理员引导。

企业级约定（不自造密码学）：
- 密码：passlib[bcrypt]，自带 salt 与 cost；
- token：PyJWT HS256，标准 claims（iss/aud/sub/iat/exp/jti）；
- access token 短命（默认 30 分钟），refresh token 长命（默认 7 天）但
  服务端落库（只存 SHA-256 哈希）可撤销、轮换制 —— 账号禁用立即断活。
"""

from __future__ import annotations

import logging
import secrets
import time
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import jwt
from passlib.context import CryptContext

from app.config import settings

logger = logging.getLogger(__name__)

JWT_ISSUER = "wenyuan-kb"
JWT_AUDIENCE = "wenyuan-web"

_pwd_ctx = CryptContext(schemes=["bcrypt"], deprecated="auto")


# ---------------- 密码 ----------------

def hash_password(password: str) -> str:
    return _pwd_ctx.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return _pwd_ctx.verify(password, password_hash)
    except ValueError:
        return False


def password_strength_error(password: str) -> str | None:
    """密码强度策略：≥8 位且同时含字母与数字。返回错误文案，None 表示通过。"""
    if len(password) < 8:
        return "密码长度至少 8 位"
    if not any(c.isalpha() for c in password) or not any(c.isdigit() for c in password):
        return "密码必须同时包含字母和数字"
    return None


# ---------------- secret ----------------

def get_auth_secret() -> str:
    """读取 JWT 密钥；未配置则生成并持久化，避免每次重启换钥踢掉所有人。"""
    if settings.auth_secret:
        return settings.auth_secret
    secret_file = Path(settings.local_store_dir) / "auth_secret.txt"
    if secret_file.is_file():
        return secret_file.read_text(encoding="utf-8").strip()
    secret_file.parent.mkdir(parents=True, exist_ok=True)
    secret = secrets.token_hex(32)
    secret_file.write_text(secret, encoding="utf-8")
    logger.warning(
        "AUTH_SECRET 未配置，已自动生成并保存到 %s —— 多实例部署必须在 .env 显式设置同一个值",
        secret_file,
    )
    return secret


# ---------------- token ----------------

def _create_token(
    *,
    user: dict[str, Any],
    kind: str,
    ttl: timedelta,
) -> tuple[str, str]:
    """签发 JWT。返回 (token, jti)。"""
    now = datetime.utcnow()
    jti = uuid.uuid4().hex
    payload = {
        "iss": JWT_ISSUER,
        "aud": JWT_AUDIENCE,
        "sub": user["id"],
        "username": user["username"],
        "display_name": user.get("display_name") or user["username"],
        "departments": user.get("departments", []),
        "roles": user.get("roles", []),
        "typ": kind,
        "iat": now,
        "exp": now + ttl,
        "jti": jti,
    }
    token = jwt.encode(payload, get_auth_secret(), algorithm="HS256")
    return token, jti


def create_access_token(user: dict[str, Any]) -> str:
    token, _ = _create_token(
        user=user, kind="access", ttl=timedelta(minutes=settings.auth_access_token_ttl_minutes)
    )
    return token


def create_refresh_token(user: dict[str, Any]) -> str:
    token, _ = _create_token(
        user=user, kind="refresh", ttl=timedelta(days=settings.auth_refresh_token_ttl_days)
    )
    return token


def decode_token(token: str, expected_kind: str = "access") -> dict[str, Any] | None:
    """验签 + 验过期 + 验类型。失败返回 None（调用方转 401）。"""
    try:
        payload = jwt.decode(
            token,
            get_auth_secret(),
            algorithms=["HS256"],
            issuer=JWT_ISSUER,
            audience=JWT_AUDIENCE,
        )
    except jwt.InvalidTokenError:
        return None
    if payload.get("typ") != expected_kind:
        return None
    return payload


def refresh_exp_iso() -> str:
    """refresh token 的过期时间（本地时间 ISO 串），落库用。"""
    exp = datetime.now() + timedelta(days=settings.auth_refresh_token_ttl_days)
    return exp.strftime("%Y-%m-%d %H:%M:%S")


# ---------------- 登录防爆破 ----------------

class LoginGuard:
    """同账号连续失败 N 次锁定 M 分钟。

    内存实现，单实例够用；生产多实例部署应换 Redis（键: username，TTL: 锁定期）。
    """

    def __init__(self, max_failures: int, lock_minutes: int) -> None:
        self.max_failures = max_failures
        self.lock_seconds = lock_minutes * 60
        # username -> [失败时间戳列表]
        self._failures: dict[str, list[float]] = {}

    def is_locked(self, username: str) -> bool:
        fails = self._failures.get(username, [])
        if len(fails) < self.max_failures:
            return False
        # 只统计锁定期内的失败
        cutoff = time.monotonic() - self.lock_seconds
        recent = [t for t in fails if t > cutoff]
        self._failures[username] = recent
        return len(recent) >= self.max_failures

    def record_failure(self, username: str) -> None:
        cutoff = time.monotonic() - self.lock_seconds
        recent = [t for t in self._failures.get(username, []) if t > cutoff]
        recent.append(time.monotonic())
        self._failures[username] = recent

    def reset(self, username: str) -> None:
        self._failures.pop(username, None)


login_guard = LoginGuard(settings.login_max_failures, settings.login_lock_minutes)


# ---------------- 初始管理员引导 ----------------

async def ensure_bootstrap_admin(user_repo) -> None:
    """users 表为空时创建初始 admin。

    密码来源：ADMIN_INITIAL_PASSWORD；未配置则生成随机密码打印到日志
    （只打一次）。绝不做弱默认（admin123 之类）—— 弱默认等于没锁门。
    """
    if await user_repo.count() > 0:
        return
    if settings.admin_initial_password:
        password = settings.admin_initial_password
        err = password_strength_error(password)
        if err:
            raise RuntimeError(f"ADMIN_INITIAL_PASSWORD 不满足强度策略: {err}")
    else:
        password = secrets.token_urlsafe(12)
        logger.warning(
            "未设置 ADMIN_INITIAL_PASSWORD，已为初始管理员生成随机密码（仅本次显示）:\n"
            "    用户名: admin\n    密码: %s\n    请立即登录并修改，或写入 .env",
            password,
        )
    await user_repo.create(
        username="admin",
        password_hash=hash_password(password),
        display_name="管理员",
        departments=[],
        roles=["admin"],
    )
    logger.info("初始管理员已创建: admin")
