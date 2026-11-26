"""健康检查与依赖探针。"""

from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from app.config import settings

logger = logging.getLogger(__name__)
router = APIRouter(tags=["health"])


@router.get("/health")
async def health() -> dict[str, str]:
    """存活探针：进程在跑就返回 200。"""
    return {"status": "ok", "env": settings.app_env}


async def _probe(name: str, coro, timeout: float | None = None) -> str:
    """跑一个依赖探针，超时算失败而不是把整个请求拖住。

    健康检查的价值在于「马上给答案」—— 一个探针卡两分钟，
    运维在半夜根本看不到到底是哪一环挂了。
    各探针可用独立超时（如公网 LLM 比内网依赖慢得多）。
    """
    try:
        await asyncio.wait_for(coro, timeout=timeout or settings.readiness_probe_timeout)
        return "ok"
    except TimeoutError:
        used = timeout or settings.readiness_probe_timeout
        logger.warning("探针 %s 超时（%ss）", name, used)
        return f"fail: timeout after {used}s"
    except Exception as exc:  # noqa: BLE001
        return f"fail: {exc}"


@router.get("/ready")
async def ready() -> JSONResponse:
    """就绪探针：逐个探测下游依赖。

    任何一个关键依赖不通都会明确报出来 —— 部署时最耗时间的就是
    "服务起来了但检索一直报错"这种问题。

    每个探针都有独立超时，因此最坏耗时 ≈ 探针数 × 单探针超时，
    而不是被最慢的那个无限拖下去。
    """
    checks: dict[str, str] = {}

    async def _qdrant() -> None:
        from app.indexing.factory import get_vector_store

        await get_vector_store().ping()

    async def _es() -> None:
        from app.indexing.factory import get_keyword_store

        await get_keyword_store().ping()

    async def _postgres() -> None:
        from app.storage.factory import get_document_repo

        await get_document_repo().ping()

    async def _embedding() -> None:
        from app.core.embeddings import get_embedding_service

        await get_embedding_service().embed_query("健康检查")

    async def _llm() -> None:
        # 抽取式引擎是本地逻辑，没有远端可探
        if settings.llm_provider == "extractive":
            return
        # 轻量探测：GET /models 验证网关可达 + Key 有效，不产生补全成本。
        # 真实补全 ping（max_tokens=1）在公网模型上动辄超过内网探针超时，
        # 且前端每 30s 轮询一次 /ready，等于高频烧钱。
        import httpx

        base = (settings.llm_base_url or "").rstrip("/")
        async with httpx.AsyncClient(timeout=settings.llm_probe_timeout) as client:
            resp = await client.get(
                f"{base}/models",
                headers={"Authorization": f"Bearer {settings.llm_api_key}"},
            )
            if resp.status_code in (401, 403):
                raise RuntimeError(f"鉴权失败（status {resp.status_code}）")
            if resp.status_code >= 400:
                raise RuntimeError(f"status {resp.status_code}")

    async def _mineru() -> None:
        import httpx

        async with httpx.AsyncClient(timeout=settings.readiness_probe_timeout) as client:
            resp = await client.get(f"{settings.mineru_api_url.rstrip('/')}/health")
            if resp.status_code >= 400:
                raise RuntimeError(f"status {resp.status_code}")

    # MinerU 未启用的部署形态（MINERU_ENABLED=false，本地 OCR 兜底）不去探活：
    # 服务本来就不打算连它，把就绪状态拖成 503 是误报，不是故障信号
    # (探针协程, 独立超时)。None = 用通用 readiness_probe_timeout
    probes: dict[str, tuple[object, float | None]] = {
        "qdrant": (_qdrant, None),
        "elasticsearch": (_es, None),
        "postgres": (_postgres, None),
        "embedding": (_embedding, None),
        "llm": (_llm, settings.llm_probe_timeout),
    }
    if settings.mineru_enabled:
        probes["mineru"] = (_mineru, None)

    results = await asyncio.gather(
        *(_probe(name, fn(), timeout) for name, (fn, timeout) in probes.items()),  # type: ignore[arg-type]
        return_exceptions=True,
    )
    for (name, _), result in zip(probes.items(), results, strict=False):
        checks[name] = result if isinstance(result, str) else f"fail: {result}"

    is_ready = all(v == "ok" for v in checks.values())
    if not settings.mineru_enabled:
        # 只做展示，不参与 ready 判定 —— 让运维一眼看清这个后端为什么没探
        checks["mineru"] = "disabled (MINERU_ENABLED=false)"
    return JSONResponse(status_code=200 if is_ready else 503, content={"ready": is_ready, "checks": checks})
