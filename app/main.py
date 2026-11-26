"""FastAPI 应用入口。

内网部署形态：**单端口**。FastAPI 同时提供 API 与前端 SPA，
省掉一层 nginx，也避免跨域配置 —— 对内网运维更省事。
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.utils import get_openapi
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app.api import (
    routes_audit,
    routes_auth,
    routes_chat,
    routes_conversations,
    routes_health,
    routes_ingest,
    routes_stats,
)
from app.config import settings

logging.basicConfig(
    level=getattr(logging, settings.log_level.upper(), logging.INFO),
    format="%(asctime)s %(levelname)s %(name)s | %(message)s",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """启动时确保索引就绪。

    初始化失败或超时都**不阻断启动** —— 下游依赖不通时服务仍然要能起来，
    让 /ready 把具体问题暴露给运维，而不是整台机器上的应用直接不可达。
    """
    logger.info("正在初始化索引…")
    try:
        async def _init() -> None:
            from app.core.auth import ensure_bootstrap_admin
            from app.indexing.factory import get_keyword_store, get_vector_store
            from app.storage.factory import get_conversation_repo, get_document_repo
            from app.storage.audit_repo import AuditRepository
            from app.storage.user_repo import UserRepository

            await get_vector_store().ensure_collection()
            await get_keyword_store().ensure_index()
            await get_document_repo().init_schema()
            await get_conversation_repo().init_schema()
            user_repo = UserRepository()
            await user_repo.init_schema()
            await AuditRepository().init_schema()
            await ensure_bootstrap_admin(user_repo)

        await asyncio.wait_for(_init(), timeout=settings.startup_init_timeout)
        logger.info("索引初始化完成")
    except TimeoutError:
        logger.warning(
            "索引初始化超时（%ss），服务照常启动 —— 请检查 Qdrant / ES / Postgres 是否可达",
            settings.startup_init_timeout,
        )
    except Exception as exc:  # noqa: BLE001
        # 不阻断启动 —— 让 /ready 暴露问题，便于运维排查
        logger.exception("索引初始化失败，请检查依赖: %s", exc)

    if settings.app_env == "prod" and not settings.api_key_enabled:
        logger.warning(
            "APP_ENV=prod 但 API_KEY_ENABLED=false —— /v1 接口当前不校验任何凭据。"
            "生产环境请置为 true 并由网关 / SSO 接管身份。"
        )

    yield
    logger.info("服务已停止")


app = FastAPI(
    title="企业知识库 · 检索服务",
    description="混合检索 + 权限感知 + 引用溯源的企业知识库检索服务",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    # 开发时 Vite dev server 走 proxy，不产生跨域；
    # 生产是同源部署，这里放开仅为了将来拆分部署
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def bearer_auth(request, call_next):
    """全局 Bearer 鉴权中间件。

    企业级身份链路：登录换 token → 请求带 ``Authorization: Bearer <access>``，
    路由内通过 ``deps.get_current_user`` 取服务端确认过的身份。
    中间件只做「门禁」（有没有有效凭证），身份解析与权限检查在依赖层完成，
    两者共用同一套签名密钥与错误语义。

    白名单：登录/刷新、探活、前端 SPA 与静态资源。
    ``API_KEY_ENABLED=true`` 时 X-API-Key 旁路（机器间调用，映射到服务账号）。
    """
    path = request.url.path
    public = (
        "/v1/auth/login",
        "/v1/auth/refresh",
        # 自助注册：创建的账号默认禁用，仍需管理员审批才能登录
        "/v1/auth/register",
        # logout 只做撤销、不签发新凭证 —— access 过期时也必须能登出
        "/v1/auth/logout",
        "/health",
        "/ready",
        "/docs",
        "/openapi.json",
        "/redoc",
    )
    if path in public or not path.startswith("/v1"):
        return await call_next(request)

    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer ") and auth_header.count(" ") == 1:
        # 延迟导入避免循环依赖；这里只验证签名合法性，账号状态在依赖层回查
        from app.core.auth import decode_token

        if decode_token(auth_header[7:], expected_kind="access") is not None:
            return await call_next(request)
        return JSONResponse(
            status_code=401,
            content={"detail": "登录已过期，请重新登录"},
            headers={"WWW-Authenticate": "Bearer"},
        )

    if settings.api_key_enabled and request.headers.get("X-API-Key") == settings.api_key:
        return await call_next(request)

    return JSONResponse(
        status_code=401,
        content={"detail": "未登录或凭证缺失"},
        headers={"WWW-Authenticate": "Bearer"},
    )


app.include_router(routes_health.router)
app.include_router(routes_auth.router)
app.include_router(routes_audit.router)
app.include_router(routes_chat.router)
app.include_router(routes_conversations.router)
app.include_router(routes_ingest.router)
app.include_router(routes_stats.router)


def custom_openapi() -> dict:
    """OpenAPI 补充 Bearer securitySchemes —— /docs 里能直接登录调试。"""
    if app.openapi_schema:
        return app.openapi_schema
    from fastapi.openapi.models import HTTPBearer, SecurityScheme

    openapi_schema = get_openapi(
        title=app.title,
        version=app.version,
        description=app.description,
        routes=app.routes,
    )
    openapi_schema["components"]["securitySchemes"] = {
        "HTTPBearer": SecurityScheme(
            type="http", scheme="bearer", bearerFormat="JWT"
        ).model_dump(exclude_none=True)
    }
    for path in openapi_schema.get("paths", {}).values():
        for method in path.values():
            if isinstance(method, dict) and method.get("operationId"):
                method.setdefault("security", [{"HTTPBearer": []}])
    app.openapi_schema = openapi_schema
    return openapi_schema


app.openapi = custom_openapi  # type: ignore[method-assign]


@app.get("/v1/info")
async def info() -> dict[str, object]:
    """服务元信息。根路径留给前端 SPA，所以元信息放在 /v1/info。

    ``demo`` 告诉前端当前后端是不是演示桩 —— 演示数据必须在界面上
    标出来，否则合成文档会被当成真实制度。
    """
    return {
        "service": "enterprise-kb",
        "version": "0.1.0",
        "docs": "/docs",
        "demo": False,
        "llm_provider": settings.llm_provider,
        "llm_model": settings.llm_model,
        "embedding_model": settings.embedding_model,
        "rerank_model": settings.rerank_model,
        # 当前实际跑在什么上面 —— 离线单机部署时必须能一眼看出
        # 这不是生产拓扑，否则排障时会把替身当生产用
        "backends": {
            "vector": settings.vector_backend,
            "keyword": settings.keyword_backend,
            "document": settings.doc_backend,
            "object": settings.object_backend,
            "embedding": settings.embedding_backend,
            "rerank": settings.rerank_backend,
        },
    }


# ============================================================
# 前端 SPA 托管
# ============================================================

FRONTEND_DIST = Path(settings.frontend_dist_dir).resolve()


def _mount_frontend() -> None:
    """挂载前端构建产物。

    构建不存在时（本地还没跑 npm run build）不挂载，
    API 仍然可用 —— 开发时前端用 Vite dev server + proxy。

    挂载后根路径由 SPA 接管：浏览器打开 `/` 直接进控制台，
    运维想看服务元信息走 `/v1/info`。
    """
    if not FRONTEND_DIST.is_dir():
        logger.warning("未找到前端构建产物 %s，仅提供 API", FRONTEND_DIST)

        @app.get("/", include_in_schema=False)
        async def root() -> dict[str, object]:
            return await info()

        return

    assets = FRONTEND_DIST / "assets"
    if assets.is_dir():
        app.mount("/assets", StaticFiles(directory=assets), name="assets")

    index = FRONTEND_DIST / "index.html"

    @app.get("/{full_path:path}", include_in_schema=False)
    async def spa(full_path: str) -> FileResponse:
        """SPA 回退：静态文件优先，其余路径一律返回 index.html。

        API 前缀下的未知路径要老老实实报 404 —— 返回一张 HTML 页面会让
        前端的 JSON 解析报出看不懂的错。
        """
        if full_path.startswith(("v1/", "api/")):
            raise HTTPException(status_code=404, detail="接口不存在")

        if full_path:
            candidate = (FRONTEND_DIST / full_path).resolve()
            # 防目录穿越：目标必须仍在 dist 之内
            if FRONTEND_DIST in candidate.parents and candidate.is_file():
                return FileResponse(candidate)
        return FileResponse(index)

    logger.info("前端已挂载: %s", FRONTEND_DIST)


_mount_frontend()


if __name__ == "__main__":
    # Windows 本地直启入口：python -m app.main
    #
    # 为什么不用 `uvicorn app.main:app`：CLI 方式下 uvicorn 先创建事件循环
    # 再导入 app 包，app/__init__.py 里为 psycopg 设置的 Selector 策略
    # 就晚了 —— 这是 Windows 上「上传报 ProactorEventLoop 错误」的入口级根源。
    # `python -m app.main` 先导入 app（策略生效），再建循环，顺序就对了。
    import uvicorn

    uvicorn.run(app, host=settings.api_host, port=settings.api_port)
