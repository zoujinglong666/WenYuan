# ============================================================
# 多阶段构建：前端（Node）→ 运行时（Python）
# 最终镜像里只有构建好的静态产物与 Python 依赖，单端口由 FastAPI 托管。
# ============================================================

# ---------- 阶段 1：构建前端 ----------
FROM node:22-alpine AS web-builder

WORKDIR /web

# 先只拷依赖清单 —— 源码变动时这一层仍可命中缓存
COPY web/package.json web/package-lock.json* ./
RUN (npm ci --no-audit --no-fund || npm install --no-audit --no-fund)

COPY web/ ./

# API Key 会在构建期打进 bundle。生产建议由网关接管鉴权，
# 这里只是让前端能带上同一个 key。
ARG VITE_API_KEY=""
ENV VITE_API_KEY=$VITE_API_KEY

RUN npm run build


# ---------- 阶段 2：运行时 ----------
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# 中文字体：PDF/Office 解析与表格渲染需要
RUN apt-get update && apt-get install -y --no-install-recommends \
        curl \
        fonts-wqy-zenhei \
        fonts-wqy-microhei \
        libgl1 \
        libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml ./
COPY app ./app
COPY scripts ./scripts

RUN pip install --upgrade pip && \
    pip install \
        "fastapi>=0.115" \
        "uvicorn[standard]>=0.32" \
        "pydantic>=2.9" \
        "pydantic-settings>=2.6" \
        "langgraph>=0.2.50" \
        "langchain-core>=0.3" \
        "litellm>=1.55" \
        "qdrant-client>=1.12" \
        "elasticsearch>=8.15,<9" \
        "redis>=5.2" \
        "psycopg[binary]>=3.2" \
        "boto3>=1.35" \
        "python-multipart>=0.0.17" \
        "httpx>=0.28" \
        "python-docx>=1.1" \
        "python-pptx>=1.0" \
        "openpyxl>=3.1" \
        "orjson>=3.10" \
        "structlog>=24.4" \
        "tenacity>=9.0" \
    && pip install --no-deps -e . || true

# 前端构建产物 —— FastAPI 直接托管，单端口对外
COPY --from=web-builder /web/dist ./web/dist

EXPOSE 8088

HEALTHCHECK --interval=15s --timeout=5s --start-period=20s --retries=5 \
    CMD curl -fs http://localhost:8088/health || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8088", "--workers", "2"]
