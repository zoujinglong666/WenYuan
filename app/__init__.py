"""企业知识库 · 检索服务。

线程数限制（必须在 numpy / onnxruntime 被导入之前生效）
--------------------------------------------------------
numpy 的 OpenBLAS 后端会为**每个线程**预分配一块大的运算缓冲区；
onnxruntime 的 arena 分配器同理。16 核机器上两者叠加能吃掉好几个 GB，
在内网机器上再叠加别的服务，就会出现
「分配 23MB 都失败」这种完全看不出原因的错误。

所以这里在包被导入的第一时间把线程数钉死。环境变量必须在
numpy 加载 OpenBLAS **之前**设置，否则不生效 —— 这也是它必须放在
``app/__init__.py`` 而不是某个模块里的原因。

想调就调环境变量；这里给的是保守默认值。
"""

import os

__version__ = "0.1.0"

#: BLAS / OpenMP 线程数。0 = 不限制（不推荐）。
_DEFAULT_MATH_THREADS = os.environ.get("KB_MATH_THREADS", "2")

for _var in (
    "OPENBLAS_NUM_THREADS",
    "OMP_NUM_THREADS",
    "MKL_NUM_THREADS",
    "NUMEXPR_NUM_THREADS",
):
    os.environ.setdefault(_var, _DEFAULT_MATH_THREADS)

#: onnxruntime 会读这个变量作为默认 intra_op 线程数
os.environ.setdefault("OMP_NUM_THREADS", _DEFAULT_MATH_THREADS)

# Windows：事件循环策略（必须在**任何**事件循环被创建之前生效）
# ------------------------------------------------------------------
# psycopg3 的异步模式依赖 loop.add_reader / add_writer，而 Windows 默认的
# ProactorEventLoop 不支持 —— 摄入文档时直接报
# "Psycopg cannot use the 'ProactorEventLoop' to run in async mode"。
# 与上面的线程数限制同理：这类设置必须放在包初始化第一时间，
# 等 asyncio.run / uvicorn 把循环建起来再改就晚了。
if os.name == "nt":
    import asyncio

    _selector = getattr(asyncio, "WindowsSelectorEventLoopPolicy", None)
    if _selector is not None:
        asyncio.set_event_loop_policy(_selector())
