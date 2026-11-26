"""耗时计时的唯一单位：**毫秒**。

`time.perf_counter()` 返回秒，直接塞进 API 会让界面把 1.8 秒显示成「2ms」。
所以所有对外暴露的耗时一律在计时处换算成毫秒 —— 接口契约里只有一种单位，
前端不需要猜。
"""

from __future__ import annotations

import time


def elapsed_ms(t0: float) -> float:
    """从 `time.perf_counter()` 的起点算起经过的毫秒数（保留 1 位小数）。"""
    return round((time.perf_counter() - t0) * 1000, 1)
