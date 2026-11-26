"""文档解析层。

统一出口是 :func:`parse_document`，所有解析器都产出 :class:`ParsedDocument`，
下游切分与索引只认这一个数据结构。
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class ParsedBlock:
    """文档中的一个连续文本块（段落级）。"""

    text: str
    #: 该块在文档中的序号
    index: int = 0
    #: 标题层级路径，如 ["第三章 差旅制度", "3.2 住宿标准"]
    heading_path: list[str] = field(default_factory=list)
    #: 表格内容（HTML 或 Markdown 表格），非表格为空
    table: str | None = None
    #: 该块在原始文档中的页码（1 起），无则为空
    page: int | None = None


@dataclass
class ParsedDocument:
    """一份解析后的文档。"""

    doc_id: str
    filename: str
    #: 全文（按块顺序拼接）
    text: str
    blocks: list[ParsedBlock] = field(default_factory=list)
    #: 文档级元数据
    metadata: dict[str, object] = field(default_factory=dict)
    #: 使用的解析器名
    parser: str = ""

    @property
    def title(self) -> str:
        return str(self.metadata.get("title") or self.filename)
