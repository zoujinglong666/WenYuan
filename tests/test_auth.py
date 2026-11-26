"""鉴权与权限测试：登录、token 生命周期、RBAC、审计。"""

from __future__ import annotations

import asyncio

from fastapi.testclient import TestClient

from app.config import settings
from tests.conftest import auth_header, login


# ---------------- 登录 ----------------

def test_login_success_and_me(client, admin_token):
    r = client.get("/v1/auth/me", headers=auth_header(admin_token))
    assert r.status_code == 200
    me = r.json()
    assert me["roles"] == ["admin"]
    assert "user:manage" in me["permissions"]


def test_login_wrong_password(client):
    r = client.post("/v1/auth/login", json={"username": "admin", "password": "wrong-pass"})
    assert r.status_code == 401
    # 防枚举：错误信息不区分账号是否存在
    r2 = client.post("/v1/auth/login", json={"username": "no-such-user", "password": "x"})
    assert r2.status_code == 401
    assert r.json()["detail"] == r2.json()["detail"]


def test_login_lock_after_max_failures(client):
    for _ in range(settings.login_max_failures):
        client.post("/v1/auth/login", json={"username": "admin", "password": "wrong"})
    r = client.post(
        "/v1/auth/login",
        json={"username": "admin", "password": settings.admin_initial_password},
    )
    assert r.status_code == 429


# ---------------- 无凭证 / 过期 / 越权 ----------------

def test_protected_route_requires_token(client):
    r = client.get("/v1/conversations")
    assert r.status_code == 401
    assert r.headers.get("www-authenticate") == "Bearer"


def test_invalid_token_rejected(client):
    r = client.get("/v1/conversations", headers=auth_header("not-a-jwt"))
    assert r.status_code == 401


def test_expired_token_rejected(client, app, admin_token, monkeypatch):
    # 把 TTL 调成负值签一个立即过期的 token
    from app.core import auth as auth_mod

    monkeypatch.setattr(settings, "auth_access_token_ttl_minutes", -1)
    from app.storage.user_repo import UserRepository

    user = asyncio.run(UserRepository().get_by_username("admin"))
    expired = auth_mod.create_access_token(user)
    monkeypatch.setattr(settings, "auth_access_token_ttl_minutes", 30)
    r = client.get("/v1/conversations", headers=auth_header(expired))
    assert r.status_code == 401


def test_employee_cannot_access_admin_routes(client, employee_token):
    for method, url in (
        ("get", "/v1/auth/users"),
        ("get", "/v1/audit"),
        ("get", "/v1/stats"),
        ("post", "/v1/auth/users"),
    ):
        if method == "post":
            r = client.post(
                url,
                json={"username": "x2", "password": "abcd1234"},
                headers=auth_header(employee_token),
            )
        else:
            r = client.get(url, headers=auth_header(employee_token))
        assert r.status_code == 403, f"{method} {url} -> {r.status_code}"


def test_employee_can_ask_and_list_conversations(client, employee_token):
    h = auth_header(employee_token)
    assert client.get("/v1/conversations", headers=h).status_code == 200
    assert client.get("/v1/documents", headers=h).status_code == 200


# ---------------- refresh 轮换与撤销 ----------------

def test_refresh_rotates_and_revokes_old(client):
    login_r = client.post(
        "/v1/auth/login", json={"username": "admin", "password": settings.admin_initial_password}
    )
    old_refresh = login_r.json()["refresh_token"]

    r1 = client.post("/v1/auth/refresh", json={"refresh_token": old_refresh})
    assert r1.status_code == 200

    # 旧 refresh 已被轮换作废
    r2 = client.post("/v1/auth/refresh", json={"refresh_token": old_refresh})
    assert r2.status_code == 401


def test_logout_revokes_refresh(client):
    login_r = client.post(
        "/v1/auth/login", json={"username": "admin", "password": settings.admin_initial_password}
    )
    refresh = login_r.json()["refresh_token"]
    assert client.post("/v1/auth/logout", json={"refresh_token": refresh}).status_code == 200
    assert client.post("/v1/auth/refresh", json={"refresh_token": refresh}).status_code == 401


def test_disable_user_revokes_tokens(client, admin_token, employee_token):
    # 找到员工 id
    users = client.get("/v1/auth/users", headers=auth_header(admin_token)).json()
    emp = next(u for u in users if u["username"] == "emp01")

    # 禁用后：refresh 全撤销、access 立即失效（依赖层回查账号状态）
    r = client.patch(f"/v1/auth/users/{emp['id']}", json={"disabled": True}, headers=auth_header(admin_token))
    assert r.status_code == 200

    h = auth_header(employee_token)
    assert client.get("/v1/conversations", headers=h).status_code == 401

    # 不能禁用自己
    admin_me = client.get("/v1/auth/me", headers=auth_header(admin_token)).json()
    r = client.patch(
        f"/v1/auth/users/{admin_me['user_id']}", json={"disabled": True}, headers=auth_header(admin_token)
    )
    assert r.status_code == 422


# ---------------- 审计 ----------------

def test_audit_records_ask_and_login(client, admin_token):
    # admin 登录本身已落审计（login fixture）；登录失败也落
    client.post("/v1/auth/login", json={"username": "ghost", "password": "x"})
    rows = client.get("/v1/audit?limit=50", headers=auth_header(admin_token)).json()
    actions = {r["action"] for r in rows}
    assert "login" in actions
    assert "login_failed" in actions


def test_audit_query_filters(client, admin_token):
    # 本测试独立建库，先造一条 login_failed 记录再按动作筛选
    client.post("/v1/auth/login", json={"username": "ghost", "password": "x"})
    rows = client.get(
        "/v1/audit?action=login_failed&limit=10", headers=auth_header(admin_token)
    ).json()
    assert rows and all(r["action"] == "login_failed" for r in rows)


def test_password_policy(client, admin_token):
    r = client.post(
        "/v1/auth/users",
        json={"username": "weakpw", "password": "12345678"},
        headers=auth_header(admin_token),
    )
    assert r.status_code == 422  # 纯数字
    r = client.post(
        "/v1/auth/users",
        json={"username": "weakpw", "password": "abcdefgh"},
        headers=auth_header(admin_token),
    )
    assert r.status_code == 422  # 纯字母
    r = client.post(
        "/v1/auth/users",
        json={"username": "weakpw", "password": "ab12"},
        headers=auth_header(admin_token),
    )
    assert r.status_code == 422  # 太短
