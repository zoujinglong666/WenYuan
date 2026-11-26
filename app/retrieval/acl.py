"""权限（ACL）解析。

模型
----
- **主体（principal）**：用户所属的可见主体，如 ``user:1001``、``dept:finance``、
  ``role:manager``、``public``。所有用户都隐含拥有 ``public``。
- **文档侧**：每条 chunk 携带 ``acl_ids``，即该文档对哪些主体可见。
- **可见性**：两者有交集即可见。

为什么必须在检索期过滤
--------------------
先全库召回 Top-50 再在后置过滤里剔除无权文档，被剔除的片段**已经进了 prompt**，
大模型仍可能引用它 —— 这等于数据泄露。正确做法是把 ACL 作为 filter 下推到
Qdrant / ES 的查询条件，无权文档根本不进入候选集。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

# 所有用户都隐含可见的主体
PUBLIC_PRINCIPAL = "public"


@dataclass
class PrincipalResolver:
    """主体解析器。

    生产环境应接入企业 IAM（LDAP / AD / 飞书 / 钉钉）。
    这里给出可替换的接口 + 一个基于用户 profile 的默认实现。
    """

    #: 静态映射表：user_id → 附加主体。接入 IAM 前用这个跑通链路。
    user_principals: dict[str, list[str]] = field(default_factory=dict)
    #: 全局可见主体（全部人可见的密级档）
    global_principals: list[str] = field(default_factory=list)

    def resolve(self, user: UserContext) -> list[str]:
        """解析用户可见主体集合。"""
        principals: set[str] = {PUBLIC_PRINCIPAL}
        principals.update(self.global_principals)
        # 注意：必须用 add 而不是 update —— update 传字符串会按字符逐个加入
        principals.add(f"user:{user.user_id}")

        for principal in self.user_principals.get(user.user_id, []):
            principals.add(principal)

        # 用户档案里带的部门 / 角色
        for dept in user.departments:
            principals.add(f"dept:{dept}")
        for role in user.roles:
            principals.add(f"role:{role}")

        return sorted(principals)


@dataclass
class UserContext:
    """发起检索的用户上下文。"""

    user_id: str
    departments: list[str] = field(default_factory=list)
    roles: list[str] = field(default_factory=list)
    #: 会话内的历史消息
    history: list[dict[str, str]] = field(default_factory=list)


_resolver = PrincipalResolver()


def get_resolver() -> PrincipalResolver:
    return _resolver


def resolve_principals(user: UserContext) -> list[str]:
    return _resolver.resolve(user)


def build_acl_ids(user: UserContext) -> list[str]:
    """返回可直接下推给检索引擎的 acl_ids 列表。"""
    return resolve_principals(user)
