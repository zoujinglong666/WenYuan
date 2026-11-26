"""冒烟自检：验证模块可导入、FastAPI 路由已注册、配置可加载。

不需要任何外部服务（数据库、ES、Qdrant、模型服务都不连），
所以可以在 CI 里当第一步门禁跑 —— 语法错误、导入错误、路由丢失一眼看穿。

    python -m scripts.smoke
"""

from __future__ import annotations

import importlib
import pkgutil
import sys
import traceback


def check_imports() -> int:
    print("=== 模块导入 ===")
    failures: list[tuple[str, str]] = []
    for mod in pkgutil.walk_packages(["app"], prefix="app."):
        name = mod.name
        if name.endswith("__main__"):
            continue
        try:
            importlib.import_module(name)
        except Exception as exc:  # noqa: BLE001
            failures.append((name, repr(exc)))
            traceback.print_exc()

    if failures:
        print(f"FAIL  {len(failures)} 个模块导入失败:")
        for name, err in failures:
            print(f"  - {name}: {err}")
        return 1

    print(f"OK    所有模块导入成功")
    return 0


def check_config() -> int:
    print("\n=== 配置 ===")
    from app.config import settings

    print(f"  LLM      : {settings.llm_provider} / {settings.llm_model}")
    print(f"  Embedding: {settings.embedding_model} ({settings.embedding_dim} 维)")
    print(f"  Rerank   : {settings.rerank_model}")
    print(f"  MinerU   : {settings.mineru_api_url} (effort={settings.mineru_effort})")
    print(f"  检索     : top_k={settings.retrieve_top_k} rerank_top_k={settings.rerank_top_k} rrf_k={settings.rrf_k}")
    print(f"  权限     : enabled={settings.acl_enabled} default_allow={settings.acl_default_allow}")

    problems: list[str] = []
    if settings.app_env == "prod":
        if settings.api_key == "change_me_in_prod":
            problems.append("生产环境 API_KEY 仍是默认值")
        if settings.postgres_password == "kb_change_me":
            problems.append("生产环境 POSTGRES_PASSWORD 仍是默认值")
    if settings.llm_provider == "deepseek" and not settings.deepseek_api_key:
        problems.append("LLM_PROVIDER=deepseek 但未配置 DEEPSEEK_API_KEY")
    if not settings.acl_enabled:
        problems.append("ACL_ENABLED=false —— 仅限本地调试，生产必须为 true")

    if problems:
        print("\nFAIL  配置存在问题:")
        for p in problems:
            print(f"  - {p}")
        return 1
    print("OK    配置检查通过")
    return 0


def check_routes() -> int:
    print("\n=== FastAPI 路由 ===")
    from app.main import app

    expected = {
        "/health",
        "/ready",
        "/v1/chat",
        "/v1/documents",
        "/v1/search",
    }

    # 用 OpenAPI schema 取路径，而不是 app.routes。
    # 新版 FastAPI（0.165+）的 include_router 是懒加载的，
    # app.routes 里是 _IncludedRouter 占位对象，不会展平。
    # OpenAPI schema 是版本无关的权威来源。
    try:
        paths = set(app.openapi().get("paths", {}))
    except Exception as exc:  # noqa: BLE001
        print(f"FAIL  无法生成 OpenAPI schema: {exc}")
        return 1

    for path in sorted(paths):
        methods = ",".join(sorted(app.openapi()["paths"][path]))
        print(f"  {methods:<12} {path}")

    missing = expected - paths
    if missing:
        print(f"\nFAIL  缺少路由: {sorted(missing)}")
        return 1
    print("OK    路由注册完整")
    return 0


def main() -> int:
    print("=" * 52)
    print("企业知识库 · 冒烟自检")
    print("=" * 52 + "\n")

    code = 0
    code |= check_imports()
    code |= check_config()
    code |= check_routes()

    print("\n" + "=" * 52)
    if code == 0:
        print("全部通过")
    else:
        print("存在问题，请修复后再启动服务")
    print("=" * 52)
    return code


if __name__ == "__main__":
    sys.exit(main())
