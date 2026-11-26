"""抽取式回答（无生成模型时的诚实降级）。

什么时候用
----------
内网没有 GPU、拿不到 vLLM，也没有合规审批走 DeepSeek API ——
但检索链路本身是可以真实跑起来的。这时候**不生成**比**乱生成**重要：
模型凭题面编出来的"差旅住宿标准 500 元/晚"会被当成公司制度，
那是知识库最危险的一种错。

所以这里做的是**抽取式问答**：从检索到的原文里挑出与问题最相关的句子，
**原样引用**，标注 [n] 出处。不改写、不合并、不补全。

    检索到以下原文依据（抽取式回答，未使用生成模型）：

    · 出差结束后 30 日内提交报销单。 [1]
    · 住宿费按城市分三档，一线城市上限 600 元/晚。 [2]

号 [n] 与前端引用列表一一对应 —— 引用编号来自 ``context_blocks`` 的顺序，
与 :func:`app.rag.graph._build_citations` 完全一致。

相关度打分用的是 **BM25 的 CJK 二元组分词**（与关键词通道同一套切分），
不是向量相似度：抽取要在几十个句子里做精确的字面匹配，
向量相似度会把"意思相近但答案不同"的句子也拉进来。
"""

from __future__ import annotations

import logging
import re
from collections.abc import AsyncIterator
from typing import Any

from app.config import settings

logger = logging.getLogger(__name__)

#: 中文句末标点 + 分号（分号也常用于并列条款，切开能提高抽取精度）
_SENTENCE_END = re.compile(r"(?<=[。！？；!?;])")

#: 从渲染后的上下文里反解出块（当调用方没直接传 blocks 时用）
_BLOCK_HEAD = re.compile(r"^\[(\d+)\]\s*来源：", re.MULTILINE)


def _split_sentences(text: str) -> list[str]:
    """中文断句。保留标点，过短的碎片丢掉。"""
    parts: list[str] = []
    for raw in _SENTENCE_END.split(text or ""):
        s = (raw or "").strip()
        if len(s) >= 8:
            parts.append(s)
    if not parts:
        stripped = (text or "").strip()
        if stripped:
            parts = [stripped]
    return parts


def _token_weights(token: str) -> float:
    """token 权重：越具体越重。

    CJK 二元组（「住宿」「标准」）比单字具体得多；
    拉丁词按长度给权 —— ``HT-2024-0873`` 这种编号必须重，
    否则一句"编号见附件"会和普通句子得分一样高。
    """
    if re.fullmatch(r"[A-Za-z0-9]+", token):
        return 1.6 if len(token) >= 6 else 1.0
    return 1.0 if len(token) >= 2 else 0.35


def _score(
    sentence: str,
    query_tokens: set[str],
    heading_tokens: set[str],
) -> float:
    """句子相关度：命中查询词的加权和，按查询词数做温和归一。"""
    from app.indexing.local_bm25_store import tokenize

    sent_tokens = set(tokenize(sentence))
    if not sent_tokens:
        return 0.0
    hits = query_tokens & sent_tokens
    if not hits:
        return 0.0
    weight = sum(_token_weights(t) for t in hits)
    # 章节标题命中额外加分 —— 很多问题的答案就在标题所指的那一节
    weight += 0.8 * sum(_token_weights(t) for t in (query_tokens & heading_tokens))
    # 极短的句子多半是标题残片，信息量低
    if len(sentence) < 15:
        weight *= 0.6
    return weight / (len(query_tokens) ** 0.5)


def _blocks_from_messages(messages: list[dict[str, str]]) -> list[dict[str, Any]]:
    """从渲染好的 user 消息里反解出上下文块。

    正常路径下 graph 会直接把 blocks 传进来；这里是兜底 ——
    让抽取式引擎在任何调用方式下都能拿到原文，而不是静默返回空答案。
    """
    for msg in reversed(messages or []):
        content = msg.get("content") or ""
        if "[1] 来源：" not in content:
            continue
        heads = list(_BLOCK_HEAD.finditer(content))
        blocks: list[dict[str, Any]] = []
        for i, m in enumerate(heads):
            start = m.end()
            end = heads[i + 1].start() if i + 1 < len(heads) else len(content)
            body = content[start:end].strip()
            # 去掉 user 消息尾部的 "---\n问题：..." 分隔
            body = re.split(r"\n\s*---\s*\n", body)[0].strip()
            blocks.append({"text": body})
        if blocks:
            return blocks
    return []


def _question_from_messages(messages: list[dict[str, str]]) -> str:
    for msg in reversed(messages or []):
        content = msg.get("content") or ""
        m = re.search(r"问题：(.+)$", content, re.DOTALL)
        if m:
            return m.group(1).strip()
    return ""


def compose_extractive_answer(
    question: str,
    blocks: list[dict[str, Any]],
) -> str:
    """从上下文块里抽取与问题最相关的句子，拼成带 [n] 引用的答案。"""
    if not blocks:
        return "未在知识库中检索到与该问题相关的资料。"

    from app.indexing.local_bm25_store import tokenize

    query_tokens = set(tokenize(question or ""))
    if not query_tokens:
        return "未在知识库中检索到与该问题相关的资料。"

    candidates: list[tuple[float, int, int, str]] = []
    for bi, block in enumerate(blocks):
        heading = block.get("heading_text") or ""
        heading_tokens = set(tokenize(heading))
        text = block.get("text") or ""
        for si, sentence in enumerate(_split_sentences(text)):
            s = _score(sentence, query_tokens, heading_tokens)
            if s > 0:
                candidates.append((s, bi, si, sentence))

    if not candidates:
        return (
            "已在知识库中检索到相关章节，但没有找到直接回答该问题的原文句。"
            "建议换用更具体的关键词，或查看下方引用来源中的相关章节。"
        )

    max_n = max(1, settings.extractive_max_sentences)
    candidates.sort(key=lambda x: (-x[0], x[1], x[2]))
    picked = candidates[:max_n]
    # 输出按文档顺序读起来才像人话
    picked.sort(key=lambda x: (x[1], x[2]))

    max_chars = max(40, settings.extractive_max_sentence_chars)
    lines = [
        "检索到以下原文依据（抽取式回答，未使用生成模型，句子为原文摘录）：",
        "",
    ]
    for _, bi, _, sentence in picked:
        text = sentence if len(sentence) <= max_chars else sentence[:max_chars] + "…"
        lines.append(f"· {text} [{bi + 1}]")

    docs = sorted({(b.get("filename") or "").strip() for b in blocks if b.get("filename")})
    if docs:
        lines.append("")
        lines.append("来源：" + "、".join(docs[:3]))
    return "\n".join(lines)


class ExtractiveLLM:
    """与 :class:`app.core.llm.LLMService` 同接口的抽取式实现。

    接口对齐意味着上层（LangGraph 节点、路由层）完全不需要知道自己
    跑的是真模型还是抽取式 —— 换 provider 不用改调用点。
    """

    async def chat(
        self,
        messages: list[dict[str, str]],
        *,
        blocks: list[dict[str, Any]] | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        spec: Any = None,
        thinking: bool | None = None,
    ) -> str:
        resolved = blocks if blocks is not None else _blocks_from_messages(messages)
        question = _question_from_messages(messages)
        if not question and resolved:
            # 没有问题文本时用第一块的标题兜底
            question = resolved[0].get("heading_text") or ""
        return compose_extractive_answer(question, resolved)

    async def chat_stream(
        self,
        messages: list[dict[str, str]],
        *,
        blocks: list[dict[str, Any]] | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        spec: Any = None,
    ) -> AsyncIterator[str]:
        answer = await self.chat(messages, blocks=blocks)
        # 切成小段吐出去，前端的打字机效果才有东西可打
        for i in range(0, len(answer), 24):
            yield answer[i : i + 24]

    async def chat_json(
        self,
        messages: list[dict[str, str]],
        *,
        temperature: float | None = None,
        schema_hint: str = "",
        spec: Any = None,
    ) -> dict[str, Any]:
        """抽取式引擎产不出 JSON。

        明确抛错（而不是返回空 dict）—— 让上层的降级分支真的走降级路径，
        而不是拿到一个空结果还当成"模型说没有"。
        """
        raise RuntimeError("抽取式回答引擎不支持 JSON 输出")
