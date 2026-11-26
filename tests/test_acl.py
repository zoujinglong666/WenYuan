"""权限模型单元测试。"""

from __future__ import annotations

from app.retrieval.acl import PUBLIC_PRINCIPAL, PrincipalResolver, UserContext


def test_public_is_always_present():
    resolver = PrincipalResolver()
    principals = resolver.resolve(UserContext(user_id="u1"))
    assert PUBLIC_PRINCIPAL in principals
    assert "user:u1" in principals


def test_department_and_role_principals():
    resolver = PrincipalResolver()
    principals = resolver.resolve(
        UserContext(user_id="u1", departments=["finance"], roles=["manager"])
    )
    assert "dept:finance" in principals
    assert "role:manager" in principals


def test_static_mapping():
    resolver = PrincipalResolver(user_principals={"u2": ["project:apollo"]})
    principals = resolver.resolve(UserContext(user_id="u2"))
    assert "project:apollo" in principals


def test_different_users_get_different_principals():
    resolver = PrincipalResolver()
    a = set(resolver.resolve(UserContext(user_id="alice", departments=["a"])))
    b = set(resolver.resolve(UserContext(user_id="bob", departments=["b"])))
    assert "user:alice" not in b
    assert "dept:a" not in b
