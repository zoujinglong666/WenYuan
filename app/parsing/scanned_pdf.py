"""扫描件 PDF 解析（PyMuPDF 渲染 + RapidOCR 识别 + 版面还原）。

为什么需要它
------------
企业手册常是**扫描件**（无文本层）。MinerU 能做版面还原，但需要 GPU 与
模型服务；单机内网落地时未必具备。本模块提供一条**纯 CPU、无需模型服务**
的兜底链路：渲染页面 → OCR → 重建段落与标题层级。

它不是 MinerU 的替代品，只是"没有 MinerU 也能把文档读进来"的最后一块拼图。

版面还原的四条规则
------------------
1. **视觉行聚合**：OCR 把一个表格行拆成好几个框（同一 y、不同 x）。
   先按 y 重叠聚成"视觉行"，再把同一行的单元格按 x 拼起来，
   否则表格会变成前后穿插的乱序文字。
2. **页眉页脚按位置 + 重复双重剔除**。位置解决"这一页最上面/最下面"，
   重复解决"每页都有、公司名和文件编号"。
3. **先判标题，再合并段落**。顺序反了标题就会被并进正文：
   "第一章 总则" 后面跟 "第一条 ……"，上一行不以句末标点结尾，
   合并逻辑会把它们连成一段，标题就永远丢了。
4. **目录页整页跳过**。目录是导航不是内容：左边是章节名、右边是页码，
   把它切进索引只会制造一堆"标题 + 页码"的垃圾块，还污染标题路径。

标题层级
--------
层级来自**编号深度**，不靠字号猜（扫描件字号信息不可靠）：

    第X章 / 【xxx】 / 1 / 2      → 一级
    1.1 / 2.5                    → 二级
    2.5.6                        → 三级
    2.5.6.1                      → 四级

编号越浅层级越高，标题栈按此维护：出现更浅的编号时，深的出栈。
"""

from __future__ import annotations

import logging
import re
import statistics
from collections import Counter
from typing import Any

from app.config import settings
from app.parsing.base import ParsedBlock, ParsedDocument

logger = logging.getLogger(__name__)

#: 渲染倍率。扫描件 108dpi 直接识别效果差，放大到 2× 后明显更稳。
DEFAULT_SCALE = 2.0

#: 判定"该页有可用文本层"的最小字符数
_MIN_TEXT_LAYER_CHARS = 40

#: 页眉带：行底在页面顶部 9% 以内
_HEADER_BOTTOM_RATIO = 0.09
#: 页脚带：行顶在页面底部 12% 以后
_FOOTER_TOP_RATIO = 0.88
#: 右侧栏起始位置（相对页宽）—— 目录页码在这一列
_RIGHT_COL_RATIO = 0.85

#: 重复出现达到该次数的短行判为页眉/页脚
_REPEAT_THRESHOLD = 3
#: 参与"重复检测"的行的最大长度（页眉页码都是短行）
_REPEAT_MAX_LEN = 40

#: 列表项前缀 —— 这些行一定是新段落的开始
_LIST_PREFIX = re.compile(r"^(?:[·•\-—]|\(\d{1,2}\)|（\d{1,2}）|\d{1,2}[.、)）]|[（(]\d)")

#: 纯数字/点号的短串 —— 页码
_NUMERIC_ONLY = re.compile(r"^[\d\.\s·—\-]{1,10}$")

#: 句末标点
_SENTENCE_END_CHARS = "。！？!？；;"


class _Line:
    """一行 OCR / 文本层结果。"""

    __slots__ = ("text", "x0", "x1", "y0", "y1", "height", "score")

    def __init__(
        self,
        text: str,
        x0: float = 0.0,
        x1: float = 0.0,
        y0: float = 0.0,
        y1: float = 0.0,
        score: float = 1.0,
    ):
        self.text = text
        self.x0 = x0
        self.x1 = x1
        self.y0 = y0
        self.y1 = y1
        self.height = y1 - y0
        self.score = score


class _Row:
    """一个视觉行 —— 可能由多个 OCR 单元格组成（表格行）。"""

    __slots__ = ("text", "x0", "x1", "y0", "y1", "height", "cells")

    def __init__(self, cells: list[_Line]) -> None:
        self.cells = cells
        self.x0 = min(c.x0 for c in cells)
        self.x1 = max(c.x1 for c in cells)
        self.y0 = min(c.y0 for c in cells)
        self.y1 = max(c.y1 for c in cells)
        self.height = self.y1 - self.y0
        self.text = " | ".join(c.text.strip() for c in cells if c.text.strip())


# ---------------- 标题识别 ----------------

_CHAPTER_RE = re.compile(r"^第[一二三四五六七八九十百零〇\d]+[章节篇]")
_BRACKET_RE = re.compile(r"^[【\[《][^】\]》]{1,30}[】\]》]$")
#: 顶层编号：一到两位数字 + 文字（「1总则」「6劳动合同」）
#: ``(?!\d)`` 是必需的：没有它，"2025年06月发布" 会被 "20" 匹配上，
#: 四位年份就成了「一级标题」。
_TOP_NUM_RE = re.compile(r"^(\d{1,2})(?!\d)\s*\S")
#: 多级编号：1.1 / 2.5.6 / 2.5.6.1
_DEEP_NUM_RE = re.compile(r"^(\d{1,2}(?:\.\d{1,3}){1,3})(?!\d)\s*\S")
_CN_NUM_RE = re.compile(r"^[一二三四五六七八九十]{1,3}[、.]\s*\S")


def _heading_level(text: str) -> int | None:
    """按编号模式判标题层级；识别不出返回 None。

    层级 = 编号深度，而不是字号 —— 扫描件的字号信息不可靠，
    编号才是文字本身的一部分。
    """
    t = text.strip()
    if not t or len(t) > 40:
        return None

    if _CHAPTER_RE.match(t):
        return 1
    if _BRACKET_RE.match(t):
        return 1
    if _CN_NUM_RE.match(t):
        return 1

    deep = _DEEP_NUM_RE.match(t)
    if deep:
        return min(deep.group(1).count(".") + 1, 4)

    # 单数字开头：必须是 1~2 位。四位年份（2013年）不是标题。
    if _TOP_NUM_RE.match(t):
        return 1
    return None


def _clean_toc_entry(text: str) -> str:
    """目录条目清洗：去掉引导点的尾巴（"说明.." → "说明"）。"""
    t = (text or "").strip()
    while t and t[-1] in ".。·… \t":
        t = t[:-1]
    return t.strip()


# ---------------- 页面结构识别 ----------------


def _is_right_numeric(line: _Line, page_width: float) -> bool:
    """右侧栏里的纯数字 —— 目录页码。"""
    if line.x0 < page_width * _RIGHT_COL_RATIO:
        return False
    return bool(_NUMERIC_ONLY.match(line.text.strip()))


def _is_toc_page(lines: list[_Line], page_width: float) -> bool:
    """目录页判定：大量右侧页码 + 没有长正文。"""
    if not lines:
        return False
    numeric = sum(1 for ln in lines if _is_right_numeric(ln, page_width))
    if numeric < 5:
        return False
    short_ratio = sum(1 for ln in lines if len(ln.text.strip()) <= 45) / len(lines)
    return short_ratio >= 0.85


def _build_toc_index(
    toc_lines: list[_Line], page_width: float
) -> dict[str, int]:
    """从目录页抽出「标题 → 层级」索引。

    **目录就是文档自带的标题索引** —— 比任何字号启发式都可靠。
    「董事长致辞」「说明」这类没有编号、也没有方括号的标题，
    靠编号模式永远认不出来，但目录里写得清清楚楚。

    右侧的页码列要排除掉，它们不是标题。
    """
    index: dict[str, int] = {}
    for ln in toc_lines:
        if _is_right_numeric(ln, page_width):
            continue
        entry = _clean_toc_entry(ln.text)
        if len(entry) < 2 or len(entry) > 40:
            continue
        level = _heading_level(entry)
        if level is None:
            # 目录里的标题也可能没有编号（「董事长致辞」「说明」）
            level = 1
        # 同名条目取更浅的层级
        if entry not in index or level < index[entry]:
            index[entry] = level
    return index


def _match_toc(row_text: str, toc_index: dict[str, int]) -> tuple[str, int, str] | None:
    """行文本是否命中目录里的某个标题。

    返回 ``(标题文本, 层级, 行内剩余内容)``。剩余内容是
    「标题 | 正文」同一视觉行的情况 —— 标题单独成块，正文继续当正文。
    """
    if not toc_index:
        return None
    if row_text in toc_index:
        return row_text, toc_index[row_text], ""

    # 同一视觉行里先标题后正文："董事长致辞 | 亲爱的同仁：……"
    for entry, level in toc_index.items():
        if row_text.startswith(entry + " | "):
            return entry, level, row_text[len(entry) + 3 :].strip()
    return None


def _find_repeated_lines(page_lines: list[list[_Line]]) -> set[str]:
    """找出在多页重复出现的短行 —— 页眉 / 页脚。

    判据是**重复**而不是位置：同一个文字在 3 页以上出现，就不是正文。
    公司标识、文件编号、页眉标语都会命中；正文里的短句不会在多页一字不差地重复。
    """
    counter: Counter[str] = Counter()
    for lines in page_lines:
        seen: set[str] = set()
        for ln in lines:
            t = ln.text.strip()
            if not t or len(t) > _REPEAT_MAX_LEN:
                continue
            if t not in seen:
                seen.add(t)
                counter[t] += 1

    threshold = max(2, min(settings.parser_header_footer_threshold, _REPEAT_THRESHOLD))
    return {t for t, c in counter.items() if c >= threshold}


def _group_rows(lines: list[_Line]) -> list[_Row]:
    """把 OCR 单元格聚成视觉行。

    同一行的单元格 y 区间重叠；表格行就是几个 x 不同、y 相同的单元格。
    """
    rows: list[_Row] = []
    current: list[_Line] = []
    bottom = 0.0

    for ln in lines:
        if current and ln.y0 < bottom - 0.4 * ln.height:
            current.append(ln)
            bottom = max(bottom, ln.y1)
            continue
        if current:
            rows.append(_Row(current))
        current = [ln]
        bottom = ln.y1

    if current:
        rows.append(_Row(current))
    return rows


# ---------------- 主解析器 ----------------


class ScannedPDFParser:
    """扫描件 PDF 解析器：渲染 → OCR → 段落/标题重建。

    对有文本层的 PDF 同样适用 —— 逐页判断，有文本层直接用，
    没有才 OCR。这让它成为一个通用的 PDF 兜底解析器。
    """

    name = "ocr"

    def __init__(self) -> None:
        self._engine: Any = None

    def _get_engine(self):
        if self._engine is None:
            try:
                from rapidocr_onnxruntime import RapidOCR
            except ImportError as exc:  # pragma: no cover
                raise RuntimeError(
                    "扫描件解析需要 rapidocr-onnxruntime："
                    "pip install rapidocr-onnxruntime"
                ) from exc
            # 线程数必须压住：onnxruntime 每个线程都要预分配大缓冲区，
            # 16 核机器上不限制会在别的服务占内存时直接 OOM。
            threads = max(1, settings.onnx_num_threads or 2)
            logger.info("加载本地 OCR 引擎 (RapidOCR, %d 线程)", threads)
            self._engine = RapidOCR(
                intra_op_num_threads=threads,
                inter_op_num_threads=1,
            )
        return self._engine

    async def parse(
        self,
        file_bytes: bytes,
        filename: str,
        doc_id: str,
        *,
        max_pages: int | None = None,
    ) -> ParsedDocument:
        import asyncio

        return await asyncio.to_thread(
            self._parse_sync, file_bytes, filename, doc_id, max_pages
        )

    def _parse_sync(
        self,
        file_bytes: bytes,
        filename: str,
        doc_id: str,
        max_pages: int | None = None,
    ) -> ParsedDocument:
        import pymupdf

        scale = settings.ocr_render_scale or DEFAULT_SCALE

        doc = pymupdf.open(stream=file_bytes, filetype="pdf")
        total_pages = doc.page_count
        page_count = min(total_pages, max_pages) if max_pages else total_pages

        # ---- 第一遍：逐页取行 ----
        page_lines: list[list[_Line]] = []
        page_sizes: list[tuple[int, int]] = []
        failed_pages: list[int] = []
        ocr_pages = 0
        for page_no in range(page_count):
            page = doc[page_no]
            text_layer = (page.get_text("text") or "").strip()
            if len(text_layer) >= _MIN_TEXT_LAYER_CHARS:
                page_lines.append(_lines_from_text(text_layer))
                page_sizes.append((page.rect.width, page.rect.height))
                continue
            ocr_pages += 1
            pix = page.get_pixmap(
                matrix=pymupdf.Matrix(scale, scale), colorspace=pymupdf.csRGB
            )
            lines, failed = _safe_ocr(
                self._get_engine(), _pixmap_to_array(pix), page_no + 1
            )
            if failed:
                failed_pages.append(page_no + 1)
            page_lines.append(lines)
            page_sizes.append((pix.width, pix.height))

        doc.close()

        # ---- 跨页重复的页眉页脚 ----
        repeated = _find_repeated_lines(page_lines)

        # ---- 目录页 → 标题索引 ----
        toc_index: dict[str, int] = {}
        toc_page_set: set[int] = set()
        for page_no, lines in enumerate(page_lines):
            page_w, page_h = page_sizes[page_no]
            kept = [
                ln
                for ln in lines
                if ln.text.strip() not in repeated
                and ln.y1 > page_h * _HEADER_BOTTOM_RATIO
                and ln.y0 < page_h * _FOOTER_TOP_RATIO
            ]
            if _is_toc_page(kept, page_w):
                toc_page_set.add(page_no)
                toc_index.update(_build_toc_index(kept, page_w))
        if toc_index:
            logger.info("从目录页抽出 %d 个标题条目用于正文标题识别", len(toc_index))

        # ---- 第二遍：重建结构 ----
        blocks: list[ParsedBlock] = []
        full_parts: list[str] = []
        heading_stack: list[tuple[int, str]] = []
        title = filename
        toc_pages = len(toc_page_set)

        for page_no, lines in enumerate(page_lines):
            if page_no in toc_page_set:
                continue

            page_w, page_h = page_sizes[page_no]

            # 页眉页脚：位置 + 重复双重剔除
            kept = [
                ln
                for ln in lines
                if ln.text.strip() not in repeated
                and ln.y1 > page_h * _HEADER_BOTTOM_RATIO
                and ln.y0 < page_h * _FOOTER_TOP_RATIO
            ]

            if not kept:
                continue

            if page_no == 0:
                title = _guess_title(kept) or filename

            rows = _group_rows(kept)
            body_rows = [
                r for r in rows if _match_toc(r.text.strip(), toc_index) is None
            ]
            margin_x = _margin_x(body_rows)

            for row in rows:
                text = row.text.strip()
                if not text:
                    continue

                # 标题识别：目录命中 > 编号模式 > 行高显著更大
                toc_hit = _match_toc(text, toc_index)
                level: int | None = None
                heading_text = text
                remainder = ""

                if toc_hit is not None:
                    heading_text, level, remainder = toc_hit
                else:
                    level = _heading_level(text)
                    if level is not None and len(text) > 30:
                        level = None
                    elif level is None and _is_big_heading(row, rows):
                        level = 1

                if level is not None:
                    # 标题：更新标题栈，单独成块
                    while heading_stack and heading_stack[-1][0] >= level:
                        heading_stack.pop()
                    heading_stack.append((level, heading_text))
                    blocks.append(
                        ParsedBlock(
                            text=heading_text,
                            index=len(blocks),
                            heading_path=[h for _, h in heading_stack],
                            page=page_no + 1,
                        )
                    )
                    full_parts.append(heading_text)
                    if remainder:
                        _append_body(
                            blocks,
                            full_parts,
                            remainder,
                            row.x0,
                            heading_stack,
                            page_no + 1,
                            margin_x,
                        )
                    continue

                _append_body(
                    blocks,
                    full_parts,
                    text,
                    row.x0,
                    heading_stack,
                    page_no + 1,
                    margin_x,
                )

        return ParsedDocument(
            doc_id=doc_id,
            filename=filename,
            text="\n".join(full_parts),
            blocks=blocks,
            metadata={
                "title": title,
                "parser": self.name,
                "pages": total_pages,
                "ocr_pages": ocr_pages,
                "toc_pages": toc_pages,
                "failed_pages": failed_pages,
                "blocks": len(blocks),
            },
            parser=self.name,
        )


# ---------------- 工具函数 ----------------


def _append_body(
    blocks: list[ParsedBlock],
    full_parts: list[str],
    text: str,
    x0: float,
    heading_stack: list[tuple[int, str]],
    page: int,
    margin_x: float,
) -> None:
    """把一行正文并入段落。

    新段落的三个信号：
    - 缩进（首行缩进两字）—— 扫描件里最可靠的段落边界
    - 上一段以句末标点结束
    - 本行以列表项符号开头
    """
    if not text:
        return
    prev = full_parts[-1] if full_parts else ""
    starts_new = (
        not prev
        or x0 >= margin_x + 15
        or prev[-1] in _SENTENCE_END_CHARS
        or bool(_LIST_PREFIX.match(text))
    )
    if starts_new:
        blocks.append(
            ParsedBlock(
                text=text,
                index=len(blocks),
                heading_path=[h for _, h in heading_stack],
                page=page,
            )
        )
        full_parts.append(text)
    else:
        blocks[-1].text += text
        full_parts[-1] += text


def _is_big_heading(row: _Row, rows: list[_Row]) -> bool:
    """行高显著高于本页中位 → 标题（封面大标题、多级标题无编号时用）。"""
    if len(row.text.strip()) > 18:
        return False
    heights = sorted(r.height for r in rows if r.height > 0)
    if len(heights) < 4:
        return False
    median = statistics.median(heights)
    return median > 0 and row.height >= median * 1.3


def _margin_x(body_rows: list[_Row]) -> float:
    """正文左边界 —— 用 10 百分位而不是最小值，避免个别越界行把基准拉偏。"""
    if not body_rows:
        return 0.0
    xs = sorted(r.x0 for r in body_rows)
    idx = max(0, int(len(xs) * 0.1))
    return xs[idx]


def _pixmap_to_array(pix: Any):
    import numpy as np

    return np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, 3)


def _safe_ocr(engine: Any, img: Any, page_no: int) -> tuple[list[_Line], bool]:
    """跑一页 OCR；失败就当空白页，**不中断整份文档**。

    理由很实际：一份 46 页的手册，因为第 23 页的一个畸形检测框就整份
    解析失败，用户拿到的是"上传失败"，而不是"这页没识别出来"。
    前者没法用，后者至少其余 45 页是好的。

    返回 ``(行, 是否失败)`` —— 失败页会记进日志与文档元数据，不藏着。
    """
    try:
        result, _ = engine(img)
        return _to_lines(result), False
    except Exception as exc:  # noqa: BLE001
        logger.warning("第 %d 页 OCR 失败，按空白页处理: %s", page_no, exc)
        return [], True


def _to_lines(result: Any) -> list[_Line]:
    lines: list[_Line] = []
    for item in result or []:
        box, text, score = item[0], item[1], item[2]
        xs = [pt[0] for pt in box]
        ys = [pt[1] for pt in box]
        lines.append(
            _Line(
                text=str(text),
                x0=min(xs),
                x1=max(xs),
                y0=min(ys),
                y1=max(ys),
                score=float(score),
            )
        )
    lines.sort(key=lambda ln: (ln.y0, ln.x0))
    return lines


def _lines_from_text(text: str) -> list[_Line]:
    """文本层 → 行。没有坐标（x 一律 0），标题判定只走编号模式。"""
    return [_Line(text=raw.strip()) for raw in text.splitlines() if raw.strip()]


def _guess_title(lines: list[_Line]) -> str | None:
    """首页最大的一行字通常是文档标题。

    先比**行高**再比长度：封面上的公司标识往往又高又长
    （"CTONE创通新科集团"），但它同时出现在每一页的页眉上，
    会被重复检测剔除；剩下的里最高的那个才是真标题
    （"员工手册"）。
    """
    if not lines:
        return None
    best = max(lines, key=lambda ln: (ln.height, len(ln.text)))
    if best.height <= 0:
        best = max(lines, key=lambda ln: len(ln.text))
        if len(best.text.strip()) < 4:
            return None
    elif best.height < 18:
        return None
    return best.text.strip() or None
