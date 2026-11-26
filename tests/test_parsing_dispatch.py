"""解析器分派回归测试 —— 重点锁住「MinerU 禁用后 .md 上传整条链失败」的坑。

.md 同时出现在 MINERU_EXTENSIONS 与 PLAIN_EXTENSIONS 两个集合里。
一旦分派顺序把 mineru 分支排在前面，MINERU_ENABLED=false 时
降级链会被过滤成空列表，所有 .md 上传直接失败 —— 这类"配置一关就断"的
问题必须用不依赖外部服务的单测钉住。
"""

from __future__ import annotations

import pytest

from app.config import settings
from app.parsing.registry import registry


@pytest.mark.asyncio
async def test_markdown_parses_when_mineru_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "mineru_enabled", False)
    doc = await registry.parse(
        "# 员工手册\n\n第一章 总则".encode("utf-8"),
        "demo.md",
        doc_id="test-doc",
    )
    assert doc.parser == "plain"
    assert "员工手册" in doc.text


@pytest.mark.asyncio
async def test_plain_text_formats_always_go_plain(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "mineru_enabled", True)  # 即使 MinerU 开着，纯文本也不必绕路
    for name in ("a.md", "b.txt", "c.csv", "d.json", "e.html"):
        doc = await registry.parse(b"content", name, doc_id="t")
        assert doc.parser == "plain", f"{name} 应走 plain，实际 {doc.parser}"
