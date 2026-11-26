"""切分层单元测试。

这些测试不依赖任何外部服务，跑得飞快 —— 这是能进 CI 的前提。
"""

from __future__ import annotations

from app.chunking.structure import StructureChunker, count_tokens
from app.parsing.base import ParsedBlock, ParsedDocument


def _doc(blocks: list[ParsedBlock]) -> ParsedDocument:
    return ParsedDocument(
        doc_id="doc-test",
        filename="test.md",
        text="\n\n".join(b.text for b in blocks),
        blocks=blocks,
        metadata={"title": "测试文档"},
    )


def test_count_tokens_cjk():
    # 100 个汉字应估算为 100 token（保守上界）
    assert count_tokens("测" * 100) == 100
    # 英文约 4 字符 / token
    assert count_tokens("a" * 40) == 10
    assert count_tokens("") == 0


def test_chunking_preserves_heading_path():
    blocks = [
        ParsedBlock(text="第三章 差旅制度", index=0, heading_path=["第三章 差旅制度"]),
        ParsedBlock(
            text="住宿标准为一线城市每晚 600 元。",
            index=1,
            heading_path=["第三章 差旅制度", "3.2 住宿标准"],
        ),
    ]
    chunks = StructureChunker().chunk(_doc(blocks))
    assert len(chunks) == 2
    child = chunks[1]
    assert child.heading_path == ["第三章 差旅制度", "3.2 住宿标准"]
    assert child.heading_text == "第三章 差旅制度 > 3.2 住宿标准"


def test_table_is_never_split():
    table = "| 项目 | 标准 |\n| --- | --- |\n" + "\n".join(
        f"| 项目{i} | {i}00 元 |" for i in range(30)
    )
    blocks = [
        ParsedBlock(text="费用标准", index=0, heading_path=["费用标准"]),
        ParsedBlock(text=table, index=1, heading_path=["费用标准"], table=table),
    ]
    chunks = StructureChunker().chunk(_doc(blocks))
    table_chunks = [c for c in chunks if c.is_table]
    assert len(table_chunks) == 1
    # 整表保持完整
    assert table_chunks[0].text.count("|") == table.count("|")


def test_parent_child_relationship():
    # 用非周期文本，避免重复内容造成假阳性
    long_text = "".join(
        f"第{i}条规定了具体的执行标准与例外情形{i}。" for i in range(1, 120)
    )
    blocks = [
        ParsedBlock(text="长章节", index=0, heading_path=["长章节"]),
        ParsedBlock(text=long_text, index=1, heading_path=["长章节"]),
    ]
    chunks = StructureChunker().chunk(_doc(blocks))

    assert len(chunks) > 1, "长文本应被切成多个子块"

    # 每个子块都必须能回填到自己的父块全文（Small-to-Big 的核心不变量）
    for chunk in chunks:
        assert chunk.parent_text
        assert chunk.text in chunk.parent_text

    # 至少存在一个父块被切出多个子块 —— 证明父子两层真的在起作用
    by_parent: dict[str, list] = {}
    for chunk in chunks:
        by_parent.setdefault(chunk.parent_id, []).append(chunk)
    assert any(len(group) > 1 for group in by_parent.values())

    # 不同父块之间不应出现完全相同的子块
    texts = [c.text for c in chunks]
    assert len(texts) == len(set(texts))


def test_child_chunk_respects_limit():
    long_text = "句子内容在这里。" * 500
    blocks = [ParsedBlock(text=long_text, index=0, heading_path=["章节"])]
    chunks = StructureChunker().chunk(_doc(blocks))
    for chunk in chunks:
        assert count_tokens(chunk.text) <= 400 * 1.6, "子块应接近设定的 token 上限"


def test_acl_ids_propagate():
    blocks = [ParsedBlock(text="内容", index=0, heading_path=["标题"])]
    chunks = StructureChunker().chunk(_doc(blocks), acl_ids=["public", "dept:finance"])
    assert chunks[0].acl_ids == ["public", "dept:finance"]
    assert chunks[0].to_payload()["acl_ids"] == ["public", "dept:finance"]
