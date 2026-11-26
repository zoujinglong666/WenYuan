"""pytest 共享 fixture：FastAPI TestClient + 登录助手。

独立 users/audit 库（tmp_path）避免污染真实数据；图形索引初始化在
lifespan 里跑，但 TestClient 触发 lifespan 时向量库是 local 实现不依赖
外部服务，可以安全启动。
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.config import settings


@pytest.fixture()
def app(tmp_path, monkeypatch):
    """隔离存储路径的应用实例。

    ADMIN_INITIAL_PASSWORD 在 lifespan（bootstrap admin）之前固定，
    测试不依赖随机密码。"""
    monkeypatch.setattr(settings, "local_store_dir", str(tmp_path / "store"))
    monkeypatch.setattr(settings, "admin_initial_password", "test-admin-pw1")
    from app.main import app as fastapi_app

    return fastapi_app


@pytest.fixture()
def client(app):
    with TestClient(app) as c:
        yield c


def login(client: TestClient, username: str = "admin", password: str | None = None) -> str:
    """登录拿 access token。"""
    pwd = password or settings.admin_initial_password
    r = client.post("/v1/auth/login", json={"username": username, "password": pwd})
    assert r.status_code == 200, r.text
    return r.json()["access_token"]


def auth_header(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture(autouse=True)
def reset_login_guard():
    """LoginGuard 是模块级单例，清掉测试间的失败计数避免互相锁定。"""
    from app.core.auth import login_guard

    login_guard._failures.clear()
    yield
    login_guard._failures.clear()


@pytest.fixture()
def admin_token(client) -> str:
    return login(client)


@pytest.fixture()
def employee_token(client, admin_token) -> str:
    """管理员创建一个员工账号并登录。"""
    r = client.post(
        "/v1/auth/users",
        json={
            "username": "emp01",
            "password": "emp-pass-01",
            "display_name": "普通员工",
            "departments": ["人力资源部"],
            "roles": ["employee"],
        },
        headers=auth_header(admin_token),
    )
    assert r.status_code == 200, r.text
    return login(client, "emp01", "emp-pass-01")
