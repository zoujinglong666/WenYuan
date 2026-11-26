"""Query 改写与扩展。

三条互补的改写路径
------------------
1. **Multi-Query**：生成 2-4 个不同措辞的独立查询，扩大召回面
2. **HyDE**：先生成一段"假设答案"，用它的表达方式去逼近目标文档
   （文档写"差旅住宿标准"，用户问"出差住酒店能报多少"，HyDE 能拉近这个鸿沟）
3. **历史改写**：多轮对话中把"它""这个制度"补全成独立可检索的问题

这些查询会分别走一遍混合检索，最后再做一次 RRF 融合 ——
本质上是把"召回"这一步做得更宽，精度交给后面的 rerank 负责。
"""

from __future__ import annotations

import asyncio
import logging

from app.config import settings
from app.core.llm import get_llm_service
from app.rag.prompts import (
    HISTORY_REWRITE_PROMPT,
    HYDE_SYSTEM_PROMPT,
    REWRITE_SYSTEM_PROMPT,
)

logger = logging.getLogger(__name__)

#: 中文疑问词 —— 去掉它们，剩下的是查询里的实词
_INTERROGATIVES = (
    "请问",
    "请",
    "怎么样",
    "怎么",
    "如何",
    "多少",
    "什么",
    "哪些",
    "哪个",
    "为什么",
    "为何",
    "是否",
    "能否",
    "可以",
    "需要",
    "要求",
    "规定",
    "标准",
    "吗",
    "呢",
    "的",
    "了",
    "是",
    "有",
    "在",
    "和",
    "与",
)


def _keyword_variant(question: str) -> str:
    """去掉疑问词后的实词串 —— 确定性查询扩展，不需要模型。

    这不是 LLM 改写，是**关键词化**：把"差旅报销的住宿标准是怎么规定的"
    变成"差旅 报销 住宿 规定"。
    对关键词通道（BM25）反而更友好 —— 疑问词在 BM25 里是纯噪音，
    它们稀释了 idf。
    """
    text = question
    for word in _INTERROGATIVES:
        text = text.replace(word, " ")
    parts = [p for p in text.replace("，", " ").replace(",", " ").split() if p]
    return " ".join(parts) if parts else question


async def rewrite_queries(question: str, history: list[dict[str, str]] | None = None) -> list[str]:
    """生成多路检索查询。失败时降级为原始问题，绝不让改写成为单点故障。"""
    queries: list[str] = [question]

    # 抽取式模式：没有生成模型可用，改走确定性的关键词扩展。
    # 不假装在做 LLM 改写 —— 只是把实词抽出来，多跑一路关键词召回。
    if settings.llm_provider == "extractive":
        variant = _keyword_variant(question)
        if variant and variant != question:
            queries.append(variant)
        logger.info(
            "抽取式模式：查询扩展降级为关键词化（%d 条查询），不调用生成模型",
            len(queries),
        )
        return _dedupe(queries)

    llm = get_llm_service()

    # Multi-Query / HyDE / 历史改写三路互相独立 —— 并行跑。
    # 串行时三段加起来 5-10s，是回答首字节延迟的大头。
    async def _multi() -> list[str]:
        try:
            payload = await llm.chat_json(
                [
                    {"role": "system", "content": REWRITE_SYSTEM_PROMPT},
                    {"role": "user", "content": f"用户问题：{question}"},
                ],
                schema_hint='{"queries": ["查询1", "查询2"]}',
            )
            return [
                q.strip() for q in payload.get("queries", []) if isinstance(q, str) and q.strip()
            ]
        except Exception as exc:  # noqa: BLE001
            logger.warning("Multi-Query 改写失败，使用原问题: %s", exc)
            return []

    async def _hyde() -> str:
        try:
            return await llm.chat(
                [{"role": "system", "content": HYDE_SYSTEM_PROMPT.format(question=question)}],
                temperature=0.3,
                max_tokens=256,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("HyDE 生成失败: %s", exc)
            return ""

    async def _hist() -> str:
        if not history:
            return ""
        try:
            hist_text = "\n".join(
                f"{m.get('role', 'user')}: {m.get('content', '')}" for m in history[-6:]
            )
            return await llm.chat(
                [
                    {
                        "role": "system",
                        "content": HISTORY_REWRITE_PROMPT.format(
                            history=hist_text, question=question
                        ),
                    }
                ],
                temperature=0.0,
                max_tokens=128,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("历史改写失败: %s", exc)
            return ""

    multi, hyde, hist = await asyncio.gather(_multi(), _hyde(), _hist())

    # 按对召回的重要性排序：原问题 > 历史补全（追问场景的命脉）>
    # multi-query > HyDE（噪音最大），超出上限的直接裁掉 ——
    # 查询越多，RRF 融合里真信号被摊薄得越狠（追问 7 条查询曾把
    # 正确块挤出 top50，rerank 全负 → 误报「未检索到」）
    queries = [question]
    if hist.strip():
        queries.append(hist.strip())
    queries.extend(multi)
    if hyde.strip():
        queries.append(hyde.strip())
    return queries[:5]


def _dedupe(queries: list[str]) -> list[str]:
    seen: set[str] = set()
    unique: list[str] = []
    for q in queries:
        key = q.strip().lower()
        if key and key not in seen:
            seen.add(key)
            unique.append(q.strip())
    return unique
