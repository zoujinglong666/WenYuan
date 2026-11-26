"""结构感知 + 父子块切分（Small-to-Big）。

为什么用父子块
--------------
纯固定长度切分有两个硬伤：语义被拦腰截断、chunk 缺少上下文。
父子块是当前工业界最稳的做法：

- **子块（300~500 token）** 送去向量化 —— 短、语义集中，召回精度高
- **父块（章节级，1000~2000 token）** 送去喂 LLM —— 上下文完整，答案连贯

命中子块 → 回填父块 → 进 LLM。检索用小的，生成用大的。

本模块的三条硬规则
-----------------
1. **表格不切碎**：表格一旦被拦腰截断，跨行对比关系就丢了，检索效果直接归零。
   整张表作为一个独立 chunk 保留。
2. **标题路径全程携带**：``["第三章 差旅制度", "3.2 住宿标准"]`` 随 chunk 一起进索引。
   用户问"住宿标准"时，能命中带完整路径的块，比命中裸文本块准得多。
3. **token 估算偏保守**：内网环境不便下载 tiktoken 的 BPE 词表，
   这里用 CJK 感知的启发式估算，取偏保守的上界，宁可块小一点也不让块超长。
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field

from app.config import settings
from app.parsing.base import ParsedDocument

# 中日韩统一表意文字 + 日文假名 + 韩文
_CJK_RE = re.compile(
    r"[\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff\uac00-\ud7af]"
)
# 中英文句末标点，用于子块断句
_SENTENCE_END = re.compile(r"(?<=[。！？；.!?;])\s*")


def count_tokens(text: str) -> int:
    """CJK 感知的 token 估算（保守上界）。

    实测 bge / Qwen 系分词器：中文约 1 token / 1~1.5 字，英文约 4 字符 / token。
    这里按 1 token / 1 个 CJK 字符计算，刻意高估，保证实际块长不超目标。
    """
    if not text:
        return 0
    cjk = len(_CJK_RE.findall(text))
    other = len(text) - cjk
    return int(cjk + other / 4)


@dataclass
class Chunk:
    """一条可索引的 chunk。"""

    chunk_id: str
    doc_id: str
    text: str
    parent_id: str
    parent_text: str
    filename: str
    title: str
    heading_path: list[str] = field(default_factory=list)
    page: int | None = None
    acl_ids: list[str] = field(default_factory=list)
    is_table: bool = False
    chunk_index: int = 0
    parent_index: int = 0
    metadata: dict[str, object] = field(default_factory=dict)

    @property
    def heading_text(self) -> str:
        return " > ".join(self.heading_path)

    def to_payload(self) -> dict[str, object]:
        """写入向量库 / 搜索引擎的载荷。"""
        return {
            "chunk_id": self.chunk_id,
            "doc_id": self.doc_id,
            "parent_id": self.parent_id,
            "parent_text": self.parent_text,
            "text": self.text,
            "filename": self.filename,
            "title": self.title,
            "heading_path": self.heading_path,
            "heading_text": self.heading_text,
            "page": self.page,
            "acl_ids": self.acl_ids,
            "is_table": self.is_table,
            "chunk_index": self.chunk_index,
            "parent_index": self.parent_index,
            **self.metadata,
        }


@dataclass
class _Parent:
    """父块（章节级上下文单元）。"""

    parent_id: str
    text: str
    heading_path: list[str]
    page: int | None
    is_table: bool = False
    blocks: list[str] = field(default_factory=list)


class StructureChunker:
    """按文档结构切分，产出父子两层 chunk。"""

    def chunk(
        self,
        doc: ParsedDocument,
        *,
        acl_ids: list[str] | None = None,
        extra_metadata: dict[str, object] | None = None,
    ) -> list[Chunk]:
        acl = acl_ids or []
        extra = extra_metadata or {}

        parents = self._build_parents(doc)
        chunks: list[Chunk] = []
        global_index = 0

        for parent_index, parent in enumerate(parents):
            children = self._split_children(parent)
            for child_text, child_index in children:
                chunk_id = _make_id(doc.doc_id, parent.parent_id, child_index)
                chunks.append(
                    Chunk(
                        chunk_id=chunk_id,
                        doc_id=doc.doc_id,
                        text=child_text,
                        parent_id=parent.parent_id,
                        parent_text=parent.text,
                        filename=doc.filename,
                        title=doc.title,
                        heading_path=list(parent.heading_path),
                        page=parent.page,
                        acl_ids=acl,
                        is_table=parent.is_table,
                        chunk_index=child_index,
                        parent_index=parent_index,
                        metadata=dict(extra),
                    )
                )
                global_index += 1

        return chunks

    # ---------------- 父块构建 ----------------

    def _build_parents(self, doc: ParsedDocument) -> list[_Parent]:
        """按标题路径把块聚合成父块；超长的父块再按段落切开。"""
        parents: list[_Parent] = []
        current_path: tuple[str, ...] | None = None
        buffer: list[str] = []
        buffer_page: int | None = None

        def flush() -> None:
            nonlocal buffer, buffer_page
            if not buffer:
                buffer = []
                buffer_page = None
                return
            text = "\n\n".join(buffer).strip()
            if text:
                parents.append(
                    _Parent(
                        parent_id=_make_parent_id(doc.doc_id, len(parents)),
                        text=text,
                        heading_path=list(current_path or []),
                        page=buffer_page,
                    )
                )
            buffer = []
            buffer_page = None

        for block in doc.blocks:
            # 表格独立成块，绝不与正文混排、绝不切碎
            if block.table:
                flush()
                parents.append(
                    _Parent(
                        parent_id=_make_parent_id(doc.doc_id, len(parents)),
                        text=block.table,
                        heading_path=list(block.heading_path),
                        page=block.page,
                        is_table=True,
                        blocks=[block.table],
                    )
                )
                continue

            if not block.text.strip():
                continue

            path = tuple(block.heading_path)
            # 标题本身作为独立短块单独保留，保证"按标题精确检索"可用
            if len(path) == 1 and block.text.strip() == path[0]:
                flush()
                parents.append(
                    _Parent(
                        parent_id=_make_parent_id(doc.doc_id, len(parents)),
                        text=block.text.strip(),
                        heading_path=list(path),
                        page=block.page,
                        blocks=[block.text.strip()],
                    )
                )
                continue

            if current_path is not None and path != current_path:
                flush()

            current_path = path
            if buffer_page is None:
                buffer_page = block.page
            buffer.append(block.text.strip())

        flush()

        # 超长父块按段落再切
        return self._split_oversized_parents(parents, doc.doc_id)

    def _split_oversized_parents(
        self, parents: list[_Parent], doc_id: str
    ) -> list[_Parent]:
        limit = settings.parent_chunk_tokens
        result: list[_Parent] = []
        for parent in parents:
            if count_tokens(parent.text) <= limit or parent.is_table:
                result.append(parent)
                continue
            # 先按空行（段落）切；单段仍超限则再按句子切
            for piece in self._split_paragraphs(parent.text, limit):
                result.append(
                    _Parent(
                        parent_id=_make_parent_id(doc_id, len(result)),
                        text=piece,
                        heading_path=list(parent.heading_path),
                        page=parent.page,
                        is_table=parent.is_table,
                    )
                )
        return result

    def _split_paragraphs(self, text: str, limit: int) -> list[str]:
        """把超长文本切成不超过 limit 的片段。

        两级策略：先按空行分段落；某个段落本身仍超限（常见于没有空行的
        连续文本、OCR 出来的长行）时，再按句子边界切。
        """
        pieces: list[str] = []
        buf: list[str] = []
        for para in text.split("\n\n"):
            if buf and count_tokens("\n\n".join(buf + [para])) > limit:
                pieces.append("\n\n".join(buf))
                buf = [para]
            else:
                buf.append(para)
        if buf:
            pieces.append("\n\n".join(buf))

        final: list[str] = []
        for piece in pieces:
            if count_tokens(piece) <= limit:
                final.append(piece)
                continue
            final.extend(self._split_sentences(piece, limit))
        return final

    @staticmethod
    def _split_sentences(text: str, limit: int) -> list[str]:
        sentences = [s for s in _SENTENCE_END.split(text) if s.strip()]
        out: list[str] = []
        buf: list[str] = []
        for sentence in sentences:
            if buf and count_tokens("".join(buf) + sentence) > limit:
                out.append("".join(buf).strip())
                buf = [sentence]
            else:
                buf.append(sentence)
        if buf:
            out.append("".join(buf).strip())

        # 单句本身超长（无标点的长行）时按字符硬切
        final: list[str] = []
        for piece in out:
            if count_tokens(piece) > limit * 1.5:
                final.extend(_hard_split(piece, limit))
            else:
                final.append(piece)
        return final

    # ---------------- 子块构建 ----------------

    def _split_children(self, parent: _Parent) -> list[tuple[str, int]]:
        """把父块切成检索用的子块。表格保持完整不切。"""
        text = parent.text.strip()
        if not text:
            return []

        if parent.is_table:
            return [(text, 0)]

        limit = settings.child_chunk_tokens
        overlap = settings.child_chunk_overlap

        if count_tokens(text) <= limit:
            return [(text, 0)]

        # 优先按句子边界切
        sentences = [s for s in _SENTENCE_END.split(text) if s.strip()]
        children: list[str] = []
        buf: list[str] = []
        for sentence in sentences:
            candidate = "".join(buf) + sentence
            if buf and count_tokens(candidate) > limit:
                children.append("".join(buf).strip())
                # 回退 overlap 个 token 作为下一块开头，避免语义断裂
                buf = _tail_by_tokens(buf, overlap) + [sentence]
            else:
                buf.append(sentence)
        if buf:
            children.append("".join(buf).strip())

        # 单句超长（罕见，如无标点的长表格说明）时按字符硬切
        expanded: list[str] = []
        for child in children:
            if count_tokens(child) > limit * 1.5:
                expanded.extend(_hard_split(child, limit))
            else:
                expanded.append(child)

        return [(c, i) for i, c in enumerate(expanded) if c.strip()]


def _tail_by_tokens(parts: list[str], token_budget: int) -> list[str]:
    """取列表末尾若干句，总 token 数不超过预算。"""
    if token_budget <= 0:
        return []
    out: list[str] = []
    total = 0
    for part in reversed(parts):
        t = count_tokens(part)
        if total + t > token_budget:
            break
        out.insert(0, part)
        total += t
    return out


def _hard_split(text: str, limit: int) -> list[str]:
    """无标点时的字符级兜底切分。"""
    # 约 1.6 字符/token，中文偏多，给个保守换算
    max_chars = max(64, int(limit * 1.4))
    return [text[i : i + max_chars] for i in range(0, len(text), max_chars)]


def _make_id(doc_id: str, parent_id: str, index: int) -> str:
    raw = f"{doc_id}:{parent_id}:{index}".encode()
    return hashlib.sha1(raw).hexdigest()[:24]


def _make_parent_id(doc_id: str, index: int) -> str:
    raw = f"{doc_id}:parent:{index}".encode()
    return hashlib.sha1(raw).hexdigest()[:16]
