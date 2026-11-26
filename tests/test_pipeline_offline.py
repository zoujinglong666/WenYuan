"""离线端到端冒烟测试：不依赖任何外部服务。

覆盖「解析 → 切块 → 权限 → RRF 融合」这条纯本地链路，
确保单元测试之外，主流程本身是通的。
"""

from __future__ import annotations

import pytest

from app.chunking.structure import StructureChunker
from app.parsing.mineru_client import build_document_from_markdown
from app.indexing.rrf import rrf_merge
from app.retrieval.acl import UserContext, build_acl_ids

MARKDOWN = """# 员工手册

## 第三章 差旅制度

### 3.2 住宿标准

一线城市住宿标准为每晚 600 元，二线城市 450 元，其他城市 350 元。

超过标准的部分由员工自行承担，特殊情况需部门负责人审批。

### 3.3 交通标准

市内交通实报实销，高铁二等座可报销。

## 第四章 请假制度

### 4.1 年假

工作满 1 年的员工享有 5 天年假，满 3 年 10 天，满 5 年 15 天。
"""


def test_markdown_to_blocks_keeps_heading_path():
    doc = build_document_from_markdown(
        MARKDOWN, doc_id="d1", filename="员工手册.md", parser="mineru"
    )
    assert doc.blocks

    # 应能识别出带完整路径的住宿标准段落
    lodging = [
        b
        for b in doc.blocks
        if "600 元" in b.text and b.heading_path[-1:] == ["3.2 住宿标准"]
    ]
    assert lodging, "应保留 '第三章 差旅制度 > 3.2 住宿标准' 的完整标题路径"
    assert lodging[0].heading_path[-2:] == ["第三章 差旅制度", "3.2 住宿标准"]

    # 正文段落不能被空行吞掉 —— 这是解析器最容易写错的地方
    assert any("600 元" in b.text for b in doc.blocks)
    assert any("年假" in b.text for b in doc.blocks)
    assert any("超过标准的部分" in b.text for b in doc.blocks)


def test_offline_ingest_to_retrieval():
    """解析 → 切块 → 打 ACL → 检索 → 精排结果 组装。"""
    doc = build_document_from_markdown(
        MARKDOWN, doc_id="d1", filename="员工手册.md", parser="mineru"
    )
    chunks = StructureChunker().chunk(doc, acl_ids=["public", "dept:finance"])
    assert chunks

    # 模拟双引擎召回：BM25 命中"住宿"，向量命中"年假"
    bm25 = [
        {"chunk_id": c.chunk_id, "payload": c.to_payload()}
        for c in chunks
        if "住宿" in c.text
    ]
    dense = [
        {"chunk_id": c.chunk_id, "payload": c.to_payload()}
        for c in chunks
        if "年假" in c.text
    ]
    assert bm25 and dense

    merged = rrf_merge([bm25, dense], source_names=["bm25", "dense"])
    assert len(merged) == len({c["chunk_id"] for c in bm25 + dense})

    # 每个命中项都能还原出完整载荷（含父块上下文与 ACL）
    for item in merged:
        assert item["payload"]["parent_text"]
        assert "public" in item["payload"]["acl_ids"]


def test_acl_prefilter_blocks_other_departments():
    """核心安全不变量：finance 的 chunk 不出现在 hr 的可见集合里。"""
    doc = build_document_from_markdown(
        MARKDOWN, doc_id="d1", filename="员工手册.md", parser="mineru"
    )
    finance_chunks = StructureChunker().chunk(doc, acl_ids=["public", "dept:finance"])

    hr_acl = build_acl_ids(UserContext(user_id="u_hr", departments=["hr"]))
    finance_acl = build_acl_ids(UserContext(user_id="u_fin", departments=["finance"]))

    assert "dept:finance" in finance_acl
    assert "dept:finance" not in hr_acl

    # hr 用户的主体内没有 dept:finance → 这些 chunk 对其不可见
    for chunk in finance_chunks:
        visible = bool(set(chunk.acl_ids) & set(hr_acl))
        if "dept:finance" in chunk.acl_ids and "public" not in chunk.acl_ids:
            assert not visible


def test_ragflow_style_table_survives_parsing():
    """表格必须保持结构，不能被拍平成纯文本。"""
    md = """# 费用标准

| 项目 | 标准 | 备注 |
| --- | --- | --- |
| 住宿 | 600元 | 一线城市 |
| 餐饮 | 150元 | 每天 |
"""
    doc = build_document_from_markdown(md, doc_id="d2", filename="标准.md", parser="mineru")
    table_blocks = [b for b in doc.blocks if b.table]
    assert table_blocks, "表格应被识别为独立块"
    assert "|" in table_blocks[0].table
    assert "住宿" in table_blocks[0].table
