"""LLM 调用封装（LiteLLM + 多 provider 路由）。

设计要点
--------
1. **provider 可插拔**：私有化内网默认 ``local``（vLLM 自托管，air-gap 可行）；
   需合规审批后可切 ``deepseek`` 走官方 API。上层调用方无感知。
2. **模型名以官方为准**（核实于 2026-11-25）：
   - ``deepseek-flash``  = DeepSeek-V4.1-Flash-1017 ✅ 当前主力
   - ``deepseek-v4-pro`` = DeepSeek-V4-Pro-0813（最难推理，已逐步下线）
   - ``deepseek-v4-flash`` 是**已退役别名**，会被静默路由到 V4.1-Flash，不要直接用
   - ``deepseek-chat`` / ``deepseek-reasoner`` 已于 2026-07-24 下线
3. **前缀缓存**：DeepSeek 前缀缓存命中可使输入成本降低约 98%。
   因此 prompt 组装顺序必须是「稳定前缀（system + few-shot）在前，检索上下文在后」，
   见 app/rag/prompts.py。
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

import litellm
from openai import AsyncOpenAI

from app.config import settings

logger = logging.getLogger(__name__)

litellm.suppress_debug_info = True
litellm.drop_params = True


@dataclass(frozen=True)
class ModelSpec:
    """一次模型调用的完整寻址信息。"""

    litellm_model: str
    api_base: str | None
    api_key: str | None
    provider: str


def resolve_spec(
    provider: str,
    base_url: str,
    model: str,
    api_key: str,
    *,
    thinking: bool = False,
) -> ModelSpec:
    """把 (provider, base_url, model) 翻译成 LiteLLM 可用的寻址参数。"""
    provider = provider.lower()

    if provider == "deepseek":
        spec = ModelSpec(
            litellm_model=f"deepseek/{model}",
            api_base=(base_url or settings.deepseek_base_url).rstrip("/"),
            api_key=api_key or settings.deepseek_api_key,
            provider="deepseek",
        )
        return spec

    if provider == "openai":
        return ModelSpec(
            litellm_model=f"openai/{model}",
            api_base=(base_url or "https://api.openai.com/v1").rstrip("/"),
            api_key=api_key,
            provider="openai",
        )

    # local: vLLM / SGLang 等 OpenAI 兼容服务
    return ModelSpec(
        litellm_model=f"openai/{model}",
        api_base=base_url.rstrip("/") if base_url else None,
        api_key=api_key or "EMPTY",
        provider="local",
    )


def _thinking_params(spec: ModelSpec, thinking: bool) -> dict[str, Any]:
    """不同 provider 的思考模式开关方式不同。"""
    if not thinking:
        return {}
    if spec.provider == "deepseek":
        # DeepSeek 官方：thinking 开启后 reasoning_content 必须原样回传后续请求
        return {"thinking": {"type": "enabled"}}
    return {"chat_template_kwargs": {"enable_thinking": True}}


class LLMService:
    """统一的对话/结构化输出入口，内含降级与 JSON 兜底解析。"""

    def __init__(self) -> None:
        self._primary = resolve_spec(
            settings.llm_provider,
            settings.llm_base_url,
            settings.llm_model,
            settings.llm_api_key,
        )
        self._fallback: ModelSpec | None = None
        if settings.has_fallback_model:
            self._fallback = resolve_spec(
                settings.llm_fallback_provider,
                settings.llm_fallback_base_url,
                settings.llm_fallback_model,
                settings.llm_fallback_api_key,
                thinking=True,
            )

    # ---------------- 公开接口 ----------------

    async def chat(
        self,
        messages: list[dict[str, str]],
        *,
        blocks: list[dict[str, Any]] | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        spec: ModelSpec | None = None,
        thinking: bool | None = None,
    ) -> str:
        """多轮对话。失败时若配置了升级模型则自动降级重试。

        ``blocks`` 是检索到的上下文块 —— 只有抽取式后端会用到它
        （见 :class:`app.core.extractive.ExtractiveLLM`）。
        生成式后端从 prompt 里读上下文，不关心这个参数。
        """
        target = spec or self._primary
        try:
            return await self._complete(
                target,
                messages,
                temperature=temperature,
                max_tokens=max_tokens,
                thinking=thinking,
            )
        except Exception as exc:  # noqa: BLE001
            if self._fallback is None or target is not self._primary:
                raise
            logger.warning(
                "主模型 %s 调用失败（%s），降级到 %s", target.litellm_model, exc, self._fallback.litellm_model
            )
            return await self._complete(
                self._fallback,
                messages,
                temperature=temperature,
                max_tokens=max_tokens,
                thinking=True if thinking is None else thinking,
            )

    async def chat_json(
        self,
        messages: list[dict[str, str]],
        *,
        temperature: float | None = None,
        schema_hint: str = "",
        spec: ModelSpec | None = None,
    ) -> dict[str, Any]:
        """要求模型输出 JSON。schema_hint 用于在 prompt 中强化格式约束。"""
        msg = list(messages)
        if schema_hint:
            msg.append(
                {
                    "role": "system",
                    "content": f"只输出 JSON，不要包含任何解释或 markdown 代码块。{schema_hint}",
                }
            )
        raw = await self.chat(msg, temperature=temperature or 0.0, spec=spec)
        return extract_json(raw)

    async def chat_stream(
        self,
        messages: list[dict[str, str]],
        *,
        blocks: list[dict[str, Any]] | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        spec: ModelSpec | None = None,
    ) -> AsyncIterator[str]:
        """流式输出，逐段 yield 增量文本。

        用于前端打字机效果。注意：流式路径**不走降级模型** ——
        流已经开始后无法无缝切到另一个模型。

        实现说明：这里用 openai SDK 直连而**不是** ``litellm.acompletion(stream=True)``。
        实测（2026-11-26）litellm 1.111.0 在 Windows + httpx 0.28 下对 OpenAI 兼容
        中转站的 SSE 流会静默挂起（非流式正常、裸 HTTP 流式正常），openai SDK
        没有这个问题。ModelSpec 里的 ``litellm_model`` 带 provider 前缀
        （``openai/xxx`` / ``deepseek/xxx``），直连时要去掉。
        """
        target = spec or self._primary
        model = target.litellm_model.split("/", 1)[1]
        client = AsyncOpenAI(
            base_url=target.api_base,
            api_key=target.api_key or "EMPTY",
            timeout=settings.llm_timeout,
            max_retries=2,
        )
        stream = await client.chat.completions.create(
            model=model,
            messages=messages,
            temperature=temperature if temperature is not None else settings.llm_temperature,
            max_tokens=max_tokens or settings.llm_max_tokens,
            stream=True,
        )
        async for part in stream:
            choices = part.choices or []
            if not choices:
                continue
            content = choices[0].delta.content
            if content:
                yield content

    # ---------------- 内部实现 ----------------

    async def _complete(
        self,
        spec: ModelSpec,
        messages: list[dict[str, str]],
        *,
        temperature: float | None,
        max_tokens: int | None,
        thinking: bool | None,
    ) -> str:
        params: dict[str, Any] = {
            "model": spec.litellm_model,
            "api_base": spec.api_base,
            "api_key": spec.api_key,
            "messages": messages,
            "temperature": temperature if temperature is not None else settings.llm_temperature,
            "max_tokens": max_tokens or settings.llm_max_tokens,
            "timeout": settings.llm_timeout,
            # 公网 API 偶发 429 / 连接重置：幂等的补全请求重试一次即可
            "num_retries": 2,
        }
        use_thinking = settings.llm_thinking if thinking is None else thinking
        params.update(_thinking_params(spec, use_thinking))

        try:
            resp = await litellm.acompletion(**params)
        except litellm.BadRequestError as exc:
            # 老版本 provider 不认 thinking 参数，去掉后重试
            if "thinking" not in str(exc).lower() and "enable_thinking" not in str(exc).lower():
                raise
            logger.warning("provider 不接受思考模式参数，降级为普通模式")
            params.pop("thinking", None)
            params.pop("chat_template_kwargs", None)
            resp = await litellm.acompletion(**params)

        content = resp.choices[0].message.content or ""
        logger.debug(
            "LLM[%s] 响应 %s 字符, 用量 %s",
            spec.litellm_model,
            len(content),
            resp.usage,
        )
        return content


_llm_service: LLMService | None = None


def get_llm_service() -> LLMService | ExtractiveLLM:
    """全局单例。首次调用时解析 provider 配置。

    ``LLM_PROVIDER=extractive`` 时返回抽取式引擎 ——
    没有生成模型就如实降级为"原文摘录 + 引用"，而不是硬调一个不存在的服务。
    """
    global _llm_service
    if _llm_service is None:
        if settings.llm_provider == "extractive":
            from app.core.extractive import ExtractiveLLM

            _llm_service = ExtractiveLLM()
            logger.warning(
                "LLM_PROVIDER=extractive —— 使用抽取式回答（原文摘录 + 引用），不生成改写文本"
            )
        else:
            _llm_service = LLMService()
    return _llm_service



def extract_json(raw: str) -> dict[str, Any]:
    """从模型输出中稳健地提取 JSON。

    模型常返回 ```json 包裹或前后带解释文字，这里逐层剥离。
    """
    text = raw.strip()

    fence = re.search(r"```(?:json)?\s*(.+?)```", text, re.DOTALL)
    if fence:
        text = fence.group(1).strip()

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # 退而求其次：抓第一个平衡的 {...}
    start = text.find("{")
    if start == -1:
        raise ValueError(f"模型未返回 JSON: {raw[:200]}")
    depth = 0
    in_string = False
    escape = False
    for i in range(start, len(text)):
        ch = text[i]
        if escape:
            escape = False
            continue
        if ch == "\\":
            escape = True
            continue
        if ch == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return json.loads(text[start : i + 1])
    raise ValueError(f"模型返回的 JSON 不完整: {raw[:200]}")
