"""MinerU 3.x HTTP 客户端。

MinerU 3.x 提供 RESTful API：
- ``POST /file_parse``  同步解析（单文件快速场景）
- ``POST /tasks``       异步任务（长文档 / 高并发）

本客户端走同步接口；解析结果为 zip（含 ``*_content_list.json`` 与 ``*.md``），
或直接返回 markdown。我们统一抽取 Markdown 正文并还原成块结构。

版本要点（核实于 2026-11-25）：
- 3.1.0 起原生支持 DOCX / PPTX / XLSX，不必先转 PDF
- 3.3 起 VLM 升级为 MinerU2.5-Pro-2605-1.2B，``effort=medium`` 精度仅降 0.13 分但提速 35%~220%
- 许可证已从 AGPLv3 切换为 Apache 2.0 的 MinerU 开源许可证，可商用落地
"""

from __future__ import annotations

import io
import json
import logging
import re
import zipfile

import httpx

from app.config import settings
from app.parsing.base import ParsedBlock, ParsedDocument

logger = logging.getLogger(__name__)

# Markdown 标题
_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$")
_TABLE_ROW_RE = re.compile(r"^\s*\|.*\|\s*$")


class MinerUParser:
    """通过 MinerU 服务解析文档。"""

    name = "mineru"

    async def parse(
        self,
        file_bytes: bytes,
        filename: str,
        *,
        doc_id: str,
    ) -> ParsedDocument:
        async with httpx.AsyncClient(timeout=settings.mineru_timeout) as client:
            resp = await client.post(
                f"{settings.mineru_api_url.rstrip('/')}/file_parse",
                files={"files": (filename, file_bytes)},
                data={
                    "parse_method": settings.mineru_backend,
                    "lang": settings.mineru_lang,
                    "effort": settings.mineru_effort,
                    "return_md": "true",
                    "table_enable": str(settings.parser_keep_table_structure).lower(),
                    "formula_enable": "true",
                },
            )
            resp.raise_for_status()
            content_type = resp.headers.get("content-type", "")

            if "zip" in content_type or resp.content[:2] == b"PK":
                markdown = self._extract_markdown_from_zip(resp.content)
            elif "json" in content_type:
                payload = resp.json()
                markdown = self._markdown_from_payload(payload)
            else:
                markdown = resp.text

        if not markdown.strip():
            raise ValueError(f"MinerU 返回空内容: {filename}")

        return build_document_from_markdown(
            markdown, doc_id=doc_id, filename=filename, parser=self.name
        )

    # ---------------- 结果抽取 ----------------

    @staticmethod
    def _extract_markdown_from_zip(content: bytes) -> str:
        with zipfile.ZipFile(io.BytesIO(content)) as zf:
            names = zf.namelist()
            md_names = [n for n in names if n.endswith(".md")]
            if not md_names:
                raise ValueError("MinerU 返回的 zip 中没有 markdown 文件")
            # 取体积最大的 markdown，通常是主文档
            md_names.sort(key=lambda n: zf.getinfo(n).file_size, reverse=True)
            with zf.open(md_names[0]) as fh:
                return fh.read().decode("utf-8", errors="replace")

    @staticmethod
    def _markdown_from_payload(payload: dict) -> str:
        for key in ("md_content", "markdown", "content", "md"):
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                return value
        # content_list 形式：按顺序拼 text
        content_list = payload.get("content_list") or payload.get("content_list_json")
        if isinstance(content_list, str):
            content_list = json.loads(content_list)
        if isinstance(content_list, list):
            parts = []
            for item in content_list:
                text = item.get("text") or item.get("table_body") or ""
                if text:
                    parts.append(text)
            if parts:
                return "\n\n".join(parts)
        raise ValueError(f"无法从 MinerU 响应中提取 markdown: {list(payload)[:10]}")


def build_document_from_markdown(
    markdown: str,
    *,
    doc_id: str,
    filename: str,
    parser: str,
) -> ParsedDocument:
    """把 Markdown 还原成带标题路径的块结构。

    标题路径（breadcrumb）是检索效果的关键元数据 —— 用户问"住宿标准"时，
    能命中"第三章 差旅制度 > 3.2 住宿标准"的块，比命中裸文本块准得多。
    """
    lines = markdown.splitlines()
    blocks: list[ParsedBlock] = []
    heading_stack: list[tuple[int, str]] = []  # (level, text)
    para_buf: list[str] = []
    table_buf: list[str] = []
    block_index = 0

    def emit(text: str, table: str | None = None) -> None:
        nonlocal block_index
        if not text.strip():
            return
        blocks.append(
            ParsedBlock(
                text=text.strip(),
                index=block_index,
                heading_path=[h for _, h in heading_stack],
                table=table,
            )
        )
        block_index += 1

    def flush_paragraph() -> None:
        """空行 = 段落结束，必须在这里落块。"""
        nonlocal para_buf
        if para_buf:
            emit("\n".join(para_buf))
            para_buf = []

    def flush_table() -> None:
        """表格独立成块 —— 与正文分开，绝不混排。"""
        nonlocal table_buf
        if table_buf:
            table_md = "\n".join(table_buf).strip()
            emit(table_md, table=table_md)
            table_buf = []

    for line in lines:
        heading_match = _HEADING_RE.match(line)
        if heading_match:
            flush_paragraph()
            flush_table()
            level = len(heading_match.group(1))
            title = heading_match.group(2).strip()
            # 弹出比当前层级更浅的标题
            while heading_stack and heading_stack[-1][0] >= level:
                heading_stack.pop()
            heading_stack.append((level, title))
            # 标题本身作为独立短块保留，便于"按标题精确检索"
            emit(title)
            continue

        if _TABLE_ROW_RE.match(line):
            flush_paragraph()
            table_buf.append(line)
            continue

        if table_buf:
            # 表格结束
            flush_table()

        if not line.strip():
            flush_paragraph()
            continue

        para_buf.append(line)

    flush_paragraph()
    flush_table()

    full_text = "\n\n".join(b.text for b in blocks if b.text)
    title = ""
    for block in blocks:
        if block.text and block.heading_path:
            title = block.heading_path[0]
            break

    return ParsedDocument(
        doc_id=doc_id,
        filename=filename,
        text=full_text,
        blocks=blocks,
        metadata={"title": title or filename, "parser": parser},
        parser=parser,
    )
