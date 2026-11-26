"""解析器注册表：按扩展名分派 + 降级链。

降级顺序刻意如此：

    MinerU（版面还原最强） → 扫描件 OCR（纯 CPU 兜底） → Office 原生解析 → 纯文本兜底

MinerU 服务挂掉时不能整条导入链路停摆 —— 企业知识库的核心价值是
"随时能问"，而不是"解析必须用最好的引擎"。

扫描件 OCR 之所以排第二，是因为它同时吃**两种**输入：
有文本层的 PDF 直接走文本层（快且准），没有文本层的才真去渲染 + OCR。
所以它是"什么 PDF 都能读进来"的通用兜底，而不是只能处理扫描件。
"""

from __future__ import annotations

import csv
import io
import json
import logging

from app.config import settings
from app.parsing.base import ParsedBlock, ParsedDocument
from app.parsing.mineru_client import MinerUParser
from app.parsing.office import OfficeParser
from app.parsing.scanned_pdf import ScannedPDFParser

logger = logging.getLogger(__name__)

MINERU_EXTENSIONS = {".pdf", ".docx", ".pptx", ".xlsx", ".md", ".png", ".jpg", ".jpeg"}
OFFICE_EXTENSIONS = {".docx", ".pptx", ".xlsx"}
PLAIN_EXTENSIONS = {".txt", ".md", ".csv", ".json", ".html", ".htm"}
PDF_EXTENSIONS = {".pdf"}


class ParserRegistry:
    def __init__(self) -> None:
        self._mineru = MinerUParser()
        self._office = OfficeParser()
        self._scanned = ScannedPDFParser()

    async def parse(
        self,
        file_bytes: bytes,
        filename: str,
        *,
        doc_id: str,
        prefer: str | None = None,
    ) -> ParsedDocument:
        ext = _ext(filename)
        mineru_on = settings.mineru_enabled

        # 显式指定解析器
        if prefer == "mineru":
            return await self._mineru.parse(file_bytes, filename, doc_id=doc_id)
        if prefer == "office":
            return await self._office.parse(file_bytes, filename, doc_id=doc_id)
        if prefer in {"scanned", "ocr"}:
            return await self._scanned.parse(file_bytes, filename, doc_id=doc_id)

        # 自动分派
        if ext in OFFICE_EXTENSIONS:
            order = ["mineru", "office"]
        elif ext in PDF_EXTENSIONS:
            # PDF：MinerU 优先；没有版面还原服务时由本地 OCR/文本层兜底
            order = ["mineru", "scanned"]
        elif ext in PLAIN_EXTENSIONS:
            # 纯文本类不需要版面还原。**必须先于 MINERU_EXTENSIONS 判断**：
            # .md 同时挂在两个集合里，若先进 mineru 分支，MINERU_ENABLED=false
            # 时降级链被过滤成空列表，.md 上传会直接失败
            order = ["plain"]
        elif ext in MINERU_EXTENSIONS:
            order = ["mineru"]
        else:
            order = ["mineru", "scanned", "plain"]

        if not mineru_on:
            order = [e for e in order if e != "mineru"]

        last_exc: Exception | None = None
        for engine in order:
            try:
                if engine == "mineru":
                    return await self._mineru.parse(file_bytes, filename, doc_id=doc_id)
                if engine == "office":
                    return await self._office.parse(file_bytes, filename, doc_id=doc_id)
                if engine == "scanned":
                    return await self._scanned.parse(file_bytes, filename, doc_id=doc_id)
                return _parse_plain(file_bytes, filename, doc_id)
            except Exception as exc:  # noqa: BLE001
                last_exc = exc
                logger.warning(
                    "解析器 %s 处理 %s 失败，尝试下一个: %s", engine, filename, exc
                )
        raise RuntimeError(f"所有解析器均失败: {filename}: {last_exc}")


def _ext(filename: str) -> str:
    return f".{filename.rsplit('.', 1)[-1].lower()}" if "." in filename else ""


def _parse_plain(file_bytes: bytes, filename: str, doc_id: str) -> ParsedDocument:
    """纯文本兜底：txt / md / csv / json / html。"""
    text = file_bytes.decode("utf-8", errors="replace")
    ext = _ext(filename)

    if ext in {".html", ".htm"}:
        text = _strip_html(text)
    elif ext == ".json":
        try:
            text = json.dumps(json.loads(text), ensure_ascii=False, indent=2)
        except json.JSONDecodeError:
            pass
    elif ext == ".csv":
        text = _csv_to_markdown(text)

    blocks = [
        ParsedBlock(text=para, index=i)
        for i, para in enumerate(p for p in text.split("\n\n") if p.strip())
    ]
    return ParsedDocument(
        doc_id=doc_id,
        filename=filename,
        text=text,
        blocks=blocks,
        metadata={"title": filename, "parser": "plain"},
        parser="plain",
    )


def _strip_html(text: str) -> str:
    import re

    text = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", text)
    text = re.sub(r"(?i)</(p|div|h[1-6]|li|tr)>", "\n\n", text)
    text = re.sub(r"(?i)<br\s*/?>", "\n", text)
    text = re.sub(r"(?i)</t[dh]>", " | ", text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = (
        text.replace("&nbsp;", " ")
        .replace("&amp;", "&")
        .replace("&lt;", "<")
        .replace("&gt;", ">")
        .replace("&quot;", '"')
    )
    return re.sub(r"[ \t]{2,}", " ", text).strip()


def _csv_to_markdown(text: str) -> str:
    reader = csv.reader(io.StringIO(text))
    rows = [r for r in reader if r]
    if not rows:
        return ""
    header = rows[0]
    sep = "| " + " | ".join(["---"] * len(header)) + " |"
    body = "\n".join("| " + " | ".join(c.replace("|", "\\|") for c in r) + " |" for r in rows[1:])
    return f"| " + " | ".join(header) + " |\n" + sep + "\n" + body


registry = ParserRegistry()
