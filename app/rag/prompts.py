"""提示词模板。

**前缀缓存友好性**（DeepSeek 前缀缓存命中可使输入成本降低约 98%）：
消息顺序必须是「稳定前缀在前，变量在后」。

    [0] system  ← 固定不变，命中缓存
    [1] user    ← 检索上下文 + 问题，每次都变，缓存失效点在这里

如果把问题放在上下文前面，缓存就永远命中不了。这是 RAG 场景最容易踩的
成本坑 —— 每次请求都把整个系统提示词按全价重新计费。
"""

from __future__ import annotations

ANSWER_SYSTEM_PROMPT = """你是企业内部知识库助手。你的职责是基于提供的参考资料回答用户问题。

规则：
1. 只依据参考资料作答，绝不编造资料中不存在的数字、日期、金额或条款编号。
2. 判断相关性按内容而非按措辞：只要某条资料包含与问题实质相关的信息（即使措辞不同、
   只是部分覆盖），就必须基于它作答。只有当所有资料都与问题无关时，才能回答"资料中未提及"；
   部分相关时，先回答已确认的部分，再说明资料未覆盖的部分。
3. 每个关键结论后面用 [数字] 标注引用来源，数字对应参考资料编号。
4. 用简体中文回答，简洁、结构化，不要复述问题。
"""

REWRITE_SYSTEM_PROMPT = """你是一个检索查询改写器。用户会问企业内部知识库的问题。
请生成多个不同措辞的检索查询，覆盖该问题的不同表达方式。

要求：
- 输出 2 到 4 个查询，每个都是可以直接用于向量/关键词检索的独立短语
- 保留原文中的专业术语、编号、缩写，不要泛化
- 不同查询之间要有真实的视角差异（口语化、关键词化、完整句式），不要简单同义重复
- 只输出 JSON，不要解释
"""

HYDE_SYSTEM_PROMPT = """请针对下面的问题，写一段假设性的、包含答案要点的参考答案。
这段文字仅用于检索，不要求事实准确，用于逼近目标文档的表达方式。
直接输出这段参考文字，不要任何解释或前缀。

问题：{question}
"""

HISTORY_REWRITE_PROMPT = """你是查询理解助手。结合对话历史，把用户的最新问题改写成一个可独立检索的完整问题。
只输出改写后的问题本身，不要解释。

对话历史：
{history}

用户最新问题：{question}
"""

# 上下文块渲染模板。编号即引用编号。
# 注意用单花括号 —— .format() 里双花括号会被转义成字面量占位符，导致模型收到空壳上下文
CONTEXT_BLOCK_TEMPLATE = """[{index}] 来源：{filename}{heading}
{text}"""


def render_context(blocks: list[dict]) -> str:
    """把检索到的父块渲染成带编号的参考资料文本。

    Args:
        blocks: 每项至少含 ``text``、``filename``，可选 ``heading_text``、``page``。
    """
    parts: list[str] = []
    for i, block in enumerate(blocks, start=1):
        heading = block.get("heading_text") or ""
        page = block.get("page")
        meta = f" · 第 {page} 页" if page else ""
        heading_part = f" · {heading}" if heading else ""
        parts.append(
            CONTEXT_BLOCK_TEMPLATE.format(
                index=i,
                filename=block.get("filename", ""),
                heading=heading_part + meta,
                text=block.get("text", ""),
            )
        )
    return "\n\n".join(parts)


def build_answer_messages(
    question: str, context: str, history: list[dict[str, str]] | None = None
) -> list[dict[str, str]]:
    """组装回答用的消息列表（稳定前缀在前）。

    ``history`` 是最近几轮对话 —— 只在问题涉及指代（「那第2条呢」）时
    有用，拼进提问开头而不是作为独立消息，保持消息结构 [system, user]
    不变（部分 provider 对多消息结构的缓存行为不一致）。
    """
    history_part = ""
    if history:
        lines = [
            f"{'用户' if m.get('role') == 'user' else '助手'}：{m.get('content', '')[:500]}"
            for m in history
        ]
        history_part = "最近对话（仅用于理解本次提问中的指代，不要据此作答）：\n" + "\n".join(lines) + "\n\n---\n"
    user_content = f"{history_part}参考资料：\n\n{context}\n\n---\n问题：{question}"
    return [
        {"role": "system", "content": ANSWER_SYSTEM_PROMPT},
        {"role": "user", "content": user_content},
    ]
