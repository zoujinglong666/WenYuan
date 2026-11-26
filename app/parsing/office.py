"""Office 原生解析器（DOCX / PPTX / XLSX）。

与 MinerU 的关系
--------------
MinerU 3.1+ 已原生支持 Office 三件套，所以正常路径下由 MinerU 统一处理。
但本解析器仍有两个不可替代的价值：

1. **无需 GPU 服务**：MinerU 不可用时的兜底，保证核心链路不中断。
2. **标题层级更可靠**：Word 的内置 Heading 样式携带真实层级，
   比从渲染后的 Markdown 里用 ``#`` 反推层级准确得多。
   企业制度文档大量使用 Word Heading 样式，这一条对检索质量影响明显。
"""

from __future__ import annotations

import io
import logging

from app.parsing.base import ParsedBlock, ParsedDocument

logger = logging.getLogger(__name__)


class OfficeParser:
    """按扩展名分派到具体格式的解析实现。"""

    name = "office"

    async def parse(
        self, file_bytes: bytes, filename: str, *, doc_id: str
    ) -> ParsedDocument:
        ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
        if ext == "docx":
            return self._parse_docx(file_bytes, filename, doc_id)
        if ext == "pptx":
            return self._parse_pptx(file_bytes, filename, doc_id)
        if ext == "xlsx":
            return self._parse_xlsx(file_bytes, filename, doc_id)
        raise ValueError(f"OfficeParser 不支持的文件类型: {ext}")

    # ---------------- DOCX ----------------

    def _parse_docx(self, file_bytes: bytes, filename: str, doc_id: str) -> ParsedDocument:
        from docx import Document
        from docx.table import Table
        from docx.text.paragraph import Paragraph

        doc = Document(io.BytesIO(file_bytes))
        blocks: list[ParsedBlock] = []
        heading_stack: list[tuple[int, str]] = []
        idx = 0

        def push(text: str, level: int | None = None) -> None:
            nonlocal idx
            if level is not None:
                while heading_stack and heading_stack[-1][0] >= level:
                    heading_stack.pop()
                heading_stack.append((level, text))
                blocks.append(
                    ParsedBlock(text=text, index=idx, heading_path=[h for _, h in heading_stack])
                )
                idx += 1
                return
            if not text.strip():
                return
            blocks.append(
                ParsedBlock(text=text, index=idx, heading_path=[h for _, h in heading_stack])
            )
            idx += 1

        for element in doc.element.body.iterchildren():
            tag = element.tag.split("}")[-1]
            if tag == "p":
                para = Paragraph(element, doc)
                style_name = (para.style.name or "").lower() if para.style else ""
                text = para.text.strip()
                if not text:
                    continue
                level = None
                if style_name.startswith("heading"):
                    digits = "".join(ch for ch in style_name if ch.isdigit())
                    level = int(digits) if digits else 1
                elif style_name == "title":
                    level = 0
                push(text, level)
            elif tag == "tbl":
                table = Table(element, doc)
                rows = []
                for row in table.rows:
                    cells = [c.text.strip().replace("|", "\\|") for c in row.cells]
                    rows.append("| " + " | ".join(cells) + " |")
                if rows:
                    header = rows[0]
                    sep = "| " + " | ".join(["---"] * (header.count("|") - 1)) + " |"
                    body = "\n".join(rows)
                    push(body if not sep else f"{header}\n{sep}\n" + "\n".join(rows[1:]))

        return self._finalize(doc_id, filename, blocks)

    # ---------------- PPTX ----------------

    def _parse_pptx(self, file_bytes: bytes, filename: str, doc_id: str) -> ParsedDocument:
        from pptx import Presentation

        prs = Presentation(io.BytesIO(file_bytes))
        blocks: list[ParsedBlock] = []
        idx = 0
        for slide_no, slide in enumerate(prs.slides, start=1):
            title = ""
            lines: list[str] = []
            for shape in slide.shapes:
                if shape.has_text_frame:
                    text = shape.text_frame.text.strip()
                    if not text:
                        continue
                    if shape == slide.shapes.title:
                        title = text
                        continue
                    lines.append(text)
                if getattr(shape, "has_table", False):
                    rows = []
                    for row in shape.table.rows:
                        rows.append(
                            "| " + " | ".join(c.text.strip().replace("|", "\\|") for c in row.cells) + " |"
                        )
                    if rows:
                        lines.append("\n".join(rows))

            if title:
                blocks.append(
                    ParsedBlock(
                        text=title,
                        index=idx,
                        heading_path=[f"幻灯片 {slide_no}", title],
                        page=slide_no,
                    )
                )
                idx += 1
            body = "\n\n".join(lines)
            if body:
                blocks.append(
                    ParsedBlock(
                        text=body,
                        index=idx,
                        heading_path=[f"幻灯片 {slide_no}"] + ([title] if title else []),
                        page=slide_no,
                    )
                )
                idx += 1

        return self._finalize(doc_id, filename, blocks)

    # ---------------- XLSX ----------------

    def _parse_xlsx(self, file_bytes: bytes, filename: str, doc_id: str) -> ParsedDocument:
        from openpyxl import load_workbook

        wb = load_workbook(io.BytesIO(file_bytes), data_only=True, read_only=True)
        blocks: list[ParsedBlock] = []
        idx = 0
        for ws in wb.worksheets:
            rows: list[str] = []
            for row in ws.iter_rows(values_only=True):
                cells = ["" if c is None else str(c).replace("|", "\\|") for c in row]
                if not any(cells):
                    continue
                rows.append("| " + " | ".join(cells) + " |")
            if not rows:
                continue
            sep = "| " + " | ".join(["---"] * (rows[0].count("|") - 1)) + " |"
            table_md = f"{rows[0]}\n{sep}\n" + "\n".join(rows[1:])
            blocks.append(
                ParsedBlock(
                    text=table_md,
                    index=idx,
                    heading_path=[ws.title],
                    table=table_md,
                )
            )
            idx += 1
        wb.close()

        return self._finalize(doc_id, filename, blocks)

    # ---------------- 通用收尾 ----------------

    @staticmethod
    def _finalize(doc_id: str, filename: str, blocks: list[ParsedBlock]) -> ParsedDocument:
        full_text = "\n\n".join(b.text for b in blocks if b.text)
        title = blocks[0].heading_path[0] if blocks and blocks[0].heading_path else filename
        return ParsedDocument(
            doc_id=doc_id,
            filename=filename,
            text=full_text,
            blocks=blocks,
            metadata={"title": title, "parser": OfficeParser.name},
            parser=OfficeParser.name,
        )
