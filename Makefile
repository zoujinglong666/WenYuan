.PHONY: help up down logs ps build shell test lint smoke ingest eval stress api es-init env

help:
	@echo "make up       启动全栈服务"
	@echo "make api      Windows 本地直启 API（python -m app.main，修复 psycopg 事件循环）"
	@echo "make down     停止服务"
	@echo "make ps       查看服务状态"
	@echo "make logs     跟踪 API 日志"
	@echo "make env      从 .env.example 生成 .env"
	@echo "make es-init  初始化 ES 索引与 Qdrant collection"
	@echo "make smoke    冒烟自检（导入/配置/路由，不依赖外部服务）"
	@echo "make ingest   导入文档 (make ingest FILE=./docs/xxx.pdf)"
	@echo "make eval     跑检索评估 (Recall@5 / MRR / nDCG)"
	@echo "make stress   跑检索压测 (LEVELS=1,2,4,8,16 / MODE=search,chat,ingest / TARGET=http,inproc)"
	@echo "make test     跑单元测试"

env:
	@test -f .env || cp .env.example .env
	@echo ".env ready"

up: env
	docker compose up -d --build
	@echo "启动完成，API: http://localhost:$${API_PORT:-8088}/docs"

down:
	docker compose down

ps:
	docker compose ps

logs:
	docker compose logs -f api

build:
	docker compose build

shell:
	docker compose exec api bash

test:
	docker compose exec api pytest -q

smoke:
	docker compose exec api python -m scripts.smoke

lint:
	ruff check app tests scripts
	ruff format --check app tests scripts

es-init:
	docker compose exec api python -m scripts.setup_indexes

ingest:
	docker compose exec api python -m scripts.ingest --path $(FILE)

eval:
	docker compose exec api python -m scripts.eval --goldens data/goldens/retrieval_golden.jsonl

# 压测：make stress LEVELS=1,4,8 MODE=chat TARGET=http
LEVELS ?= 1,2,4,8,16
MODE ?= search
TARGET ?= http
stress:
	docker compose exec api python -m scripts.stress \
		--base-url http://127.0.0.1:8088 \
		--target $(TARGET) --mode $(MODE) --levels $(LEVELS)

# Windows 本地直启（psycopg 异步要求 Selector 事件循环，必须走这个入口而不是 uvicorn CLI）
api:
	python -m app.main
