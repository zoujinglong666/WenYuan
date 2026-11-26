"""敏感词过滤：入口拦截，命中即拒绝。

设计
----
- 词库是纯文本文件（``data/sensitive_words.txt``），运维改文件即可生效，
  不用重启后端 —— 按文件 mtime 热加载。
- 匹配前对问题做归一化（全角→半角、去空白与装饰符号、转小写），
  「傻 B」「傻  b」「ＳＢ」这类变体同样命中。
- 过滤发生在检索与 LLM 之前：拒绝的请求不花一次向量检索和模型调用。

企业级演进方向：接入可信词库服务（如本地部署的敏感词引擎），
本模块只保留 ``contains_sensitive()`` 这个接缝。
"""

from __future__ import annotations

import logging
import threading
from pathlib import Path

from app.config import settings

logger = logging.getLogger(__name__)

#: 归一化时剔除的字符：空白 + 常见装饰/分隔符号（对抗「傻 b」式变体）
_STRIP_CHARS = " \t\r\n*·•-—_~!！?？。，,、;；:：'\"()（）[]【】《》<>|/\\+=`@#$%^&"

_lock = threading.Lock()
_cache: tuple[float, frozenset[str]] | None = None  # (mtime, 词集)


def _load_words(path: Path) -> frozenset[str]:
    words: set[str] = set()
    if not path.exists():
        return frozenset()
    for line in path.read_text(encoding="utf-8").splitlines():
        word = line.strip()
        if word and not word.startswith("#"):
            words.add(_normalize(word))
    return frozenset(words)


def _normalize(text: str) -> str:
    """全角→半角 + 小写 + 去空白/装饰符号。"""
    # 全角字母数字与常见全角符号（FF01-FF5E 与半角一一对应）
    translated = "".join(
        chr(ord(ch) - 0xFEE0) if 0xFF01 <= ord(ch) <= 0xFF5E else ch for ch in text
    )
    return "".join(ch for ch in translated.lower() if ch not in _STRIP_CHARS)


def _get_words() -> frozenset[str]:
    global _cache
    path = Path(settings.sensitive_words_file)
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return frozenset()

    with _lock:
        if _cache is None or _cache[0] != mtime:
            _cache = (mtime, _load_words(path))
            logger.info("敏感词库已加载: %d 词（%s）", len(_cache[1]), path.name)
        return _cache[1]


def contains_sensitive(text: str) -> bool:
    """问题里是否命中敏感词。归一化后做子串匹配。"""
    words = _get_words()
    if not words:
        return False
    normalized = _normalize(text)
    return any(w in normalized for w in words)
