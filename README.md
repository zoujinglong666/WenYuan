# 企业知识库 · 检索服务

> 私有化内网部署 · 混合检索 + 权限感知 + 引用溯源

一套面向企业内部文档问答的检索服务：**解析 → 切块 → 双引擎索引 → 混合召回 → 精排 → 带引用生成**。

## 为什么是这个架构

| 决策 | 选择 | 理由 |
|---|---|---|
| 召回方式 | **BM25 ⊕ 向量 → RRF** | 合同编号、制度名这类"必须字面命中"的查询纯向量召不回；同义改写、口语化提问纯关键词召不回。两路都不可替代 |
| 融合算法 | **RRF**（`1/(60+rank)`） | BM25 分数无上界、余弦相似度在 [-1,1]，手工加权要逐语料调参。RRF 只看排名，无需调参 |
| 精排 | **Cross-Encoder**（bge-reranker-v2-m3） | 召回段宽召回允许噪音，精度段负责排准。Bi-Encoder 打不过 Cross-Encoder |
| 权限 | **ACL 前置过滤** | 先召回再过滤 = 无权内容已经进 prompt，模型仍会引用。那是数据泄露，不是 bug |
| 切块 | **结构感知 + 父子块** | 子块 300-500 token 检索（精度高），父块章节级喂 LLM（上下文完整）。表格整块不切 |
| 编排 | **LangGraph** | 节点职责分离，改写/召回/精排/生成可独立调参与观测 |
| 部署 | **全内网闭环** | bge-m3 / bge-reranker / Qwen3 / MinerU 全部自托管，数据不出网 |

## 模型选型（核实于 2026-11-25）

| 组件 | 默认 | 说明 |
|---|---|---|
| Embedding | `BAAI/bge-m3` | 1024 维 / 8192 token / 100+ 语言。截至 2026-08 **仍无 bge-m4 继任者**。⚠️ 独立评测显示其 dense-only 模式在零样本 BEIR 上落后 E5/mE5 类模型 —— 换 `Qwen3-Embedding-8B`（MTEB 多语言榜首 70.58）前请先跑 `scripts/eval` 对比 |
| Rerank | `BAAI/bge-reranker-v2-m3` | 开源中文首选。Apache-2.0 许可 |
| LLM（内网） | `Qwen3-32B-AWQ` | air-gap 场景的现实选择。INT4 量化后约 18GB，单卡可跑 |
| LLM（外网） | `deepseek-flash` | = DeepSeek-V4.1-Flash-1017。1M 上下文 / 384K 输出 / 原生图像理解 / off-peak $0.15→$0.60 每百万 token |
| 解析 | `MinerU 3.x` | 3.1.0+ 原生支持 DOCX/PPTX/XLSX；3.3 的 `effort=medium` 精度仅降 0.13 分但提速 35%~220% |

### DeepSeek 模型名对照（别用错）

| 名称 | 状态 |
|---|---|
| `deepseek-flash` | ✅ 当前主力（DeepSeek-V4.1-Flash-1017） |
| `deepseek-v4-pro` | 最难推理，但**已在逐步下线**（2026-09-14 起请求路由到 V4.1-Flash） |
| `deepseek-v4-flash` | ⚠️ **已退役别名**，仍被接受但只是路由到 V4.1-Flash。不要直接用 |
| `deepseek-chat` / `deepseek-reasoner` | ❌ 2026-07-24 已下线 |

**私有化内网 + DeepSeek 的现实约束**：V4.1-Flash 是 284B 总参 / 13B 激活的 MoE，自托管需要多卡服务器，普通企业内网机器跑不动。所以默认走内网自托管 Qwen3；若要用 DeepSeek，走官方 API 并走合规审批。

### 前缀缓存（成本必看）

DeepSeek 前缀缓存命中可使输入成本降低约 **98%**。因此 prompt 组装顺序必须是「稳定前缀在前、变量在后」：

```
[0] system  ← 固定不变，命中缓存
[1] user    ← 检索上下文 + 问题，每次都变
```

把问题放在上下文前面，缓存永远命中不了，每次都按全价重算系统提示词。`app/rag/prompts.py` 已按此顺序实现。

## 目录结构

```
app/
├── config.py              # 全部配置（pydantic-settings）
├── core/                  # 模型层
│   ├── embeddings.py      # bge-m3 / TEI，含 query 检索指令前缀
│   ├── reranker.py        # Cross-Encoder 精排
│   └── llm.py             # 多 provider 路由 + 降级 + 前缀缓存友好
├── parsing/               # 解析层
│   ├── mineru_client.py   # MinerU 3.x HTTP 客户端（/file_parse）
│   ├── office.py          # DOCX/PPTX/XLSX 原生解析（Word Heading 样式层级更准）
│   └── registry.py        # 分派 + 降级链
├── chunking/structure.py  # 结构感知 + 父子块切分
├── indexing/              # 索引层
│   ├── qdrant_store.py    # 向量路 + ACL filter
│   ├── es_store.py        # 关键词路（BM25, cjk analyzer）+ ACL filter
│   ├── rrf.py             # RRF 纯函数（无 I/O 依赖，可独立测试）
│   └── hybrid.py          # 双路并行 + 融合
├── retrieval/             # 检索层
│   ├── acl.py             # 主体解析 + 权限模型
│   └── rewrite.py         # Multi-Query / HyDE / 历史改写
├── rag/
│   ├── prompts.py         # 提示词（稳定前缀在前）
│   └── graph.py           # LangGraph 编排
├── ingestion/pipeline.py  # 摄入管线（增量索引）
├── storage/               # MinIO 原文 + PostgreSQL 登记
├── api/                   # FastAPI 路由
└── main.py
scripts/
├── setup_indexes.py       # 初始化 ES / Qdrant / PG
├── ingest.py              # 命令行导入
└── eval.py                # 检索评估 Recall@K / MRR / nDCG@K
tests/                     # 无外部依赖的单元测试
```

## 快速开始

```bash
# 1. 配置
cp .env.example .env
$EDITOR .env          # 至少改 API_KEY、POSTGRES_PASSWORD、模型路径

# 2. 启动（首次会拉模型，约 20-40GB）
docker compose up -d --build

# Windows 本地（无 Docker）直启用这个入口，别用 uvicorn CLI：
# uvicorn 先建事件循环再导入 app，psycopg 异步模式在 Windows 默认的
# ProactorEventLoop 上直接报错（Selector 策略来不及生效）
python -m app.main

# 3. 初始化索引
docker compose exec api python -m scripts.setup_indexes

# 4. 导入文档
docker compose exec api python -m scripts.ingest --path /data/docs --acl public,dept:finance

# 5. 就绪检查
curl http://localhost:8088/ready
```

## 接口

### 问答

```bash
curl -X POST http://localhost:8088/v1/chat \
  -H "X-API-Key: $API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "question": "差旅住宿标准是多少钱一晚？",
    "user_id": "u1001",
    "departments": ["finance"],
    "history": []
  }'
```

响应：

```json
{
  "answer": "根据《差旅管理办法》3.2 条，一线城市住宿标准为每晚 600 元 [1]。",
  "citations": [
    {"index": 1, "filename": "差旅管理办法.pdf", "heading_text": "第三章 差旅制度 > 3.2 住宿标准", "page": 12}
  ],
  "queries": ["差旅住宿标准是多少钱一晚？", "差旅 住宿 标准 限额", "..."],
  "timings": {"rewrite": 0.42, "retrieve": 0.13, "rerank": 0.08, "generate": 1.35}
}
```

### 只检索不生成（调参 / 评估用，省 LLM 成本）

```bash
curl -X POST http://localhost:8088/v1/search -H "X-API-Key: $API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"query": "合同编号 HT-2024-0873", "top_k": 5}'
```

### 摄入

```bash
curl -X POST http://localhost:8088/v1/documents \
  -H "X-API-Key: $API_KEY" \
  -F "file=@差旅管理办法.pdf" \
  -F "acl_ids=public,dept:finance"
```

## 评估：先证明"召得回"，再谈"答得准"

```bash
# 1. 建金标集（每行一条 JSONL）
#    data/goldens/retrieval_golden.jsonl
# 2. 跑检索评估
docker compose exec api python -m scripts.eval \
  --goldens data/goldens/retrieval_golden.jsonl --k 5 --verbose
```

输出 `Recall@5` / `MRR` / `nDCG@5` / 未命中数，并对偏低时给出**按顺序的排查建议**（解析质量 → 切块粒度 → Query 改写 → Embedding 模型），而不是只丢一堆数字。

**关键纪律**：检索评估和生成评估必须分开看。`Recall@5` 不达标时去调 Prompt 是最常见的错误排序 —— 召回问题只能在切块、索引、检索层面解决。

生成质量另用 RAGAS 评估（Faithfulness / Context Precision / Answer Relevance）。

## 压测：证明容量，而不只是证明"能跑"

评估回答"检索效果好不好"，压测回答"一台机器扛得住多少人"。延迟只看分位（p50/p95/p99），平均延迟会掩盖排队。

```bash
# 检索压测：并发 1/2/4/8/16 扫延迟与吞吐（查询取自 data/goldens/*.jsonl）
docker compose exec api python -m scripts.stress --base-url http://127.0.0.1:8088

# 进程内直调：把「检索本身」和「HTTP + JSON」的开销拆开对比
docker compose exec api python -m scripts.stress --target inproc

# 全链路压测（含 LLM 生成，注意成本）
docker compose exec api python -m scripts.stress --mode chat --levels 1,4,8

# 摄入基线：解析/切块/向量化/入库各段耗时（--incremental 量增量短路路径）
docker compose exec api python -m scripts.stress --mode ingest --ingest-dir data/samples
```

读数要点：

- **失败请求不计入延迟分位**——失败延迟混进 p95 会把分位变成「超时分布」；错误数与错误样本单独列出
- **首请求必须预热**（`--warmup`，默认 2 次）：模型加载、连接池建立都发生在第一跳，不预热会污染 p50
- **RSS 有范围标注**：`inproc` 模式测的是检索服务本身；`http` 模式只是压测客户端，别拿它下服务端内存结论
- 报告落盘 `data/stress_report.json`，含每档阶段耗时（retrieve / rerank）的分位拆解
- 全部请求失败时退出码为 1，CI 可直接判定

## 权限模型

- **主体（principal）**：`public`（所有人）、`user:<id>`、`dept:<部门>`、`role:<角色>`、`project:<项目>`
- **文档侧**：每个 chunk 带 `acl_ids`，即对哪些主体可见
- **可见性**：用户主体集合 ∩ 文档主体集合 ≠ ∅
- **默认拒绝**：用户无任何可见主体 → 返回空结果，绝不"默认全放行"

接入企业 IAM 时替换 `app/retrieval/acl.py` 的 `PrincipalResolver` 即可，其余代码不用动。

⚠️ **生产必改**：`app/api/routes_chat.py` 的 `current_user()` 目前直接信任客户端传的 `user_id`。上线前必须改成从鉴权中间件（Bearer / SSO / 网关）取身份注入 `request.state`，否则任何人都能伪造身份读别人的文档。

## 增量索引

- `doc_id` 按**文件名**稳定生成 —— 同一份文档更新内容后仍是同一个 id，旧 chunk 删除重写，版本 +1
- `content_hash` 按**内容**生成 —— 内容没变直接跳过，不重复解析与向量化
- 原文归档 MinIO —— 引用溯源要能跳回原文，解析器换版本后要能重跑

## 常见问题

**Q：ES 中文分词要不要装 IK？**
不用。用 ES 内置的 `cjk` analyzer（中文二元切分），不依赖第三方插件镜像，内网部署省事，对中文 BM25 足够好。要更高精度再评估 IK。

**Q：`/ready` 报某个依赖 fail 怎么排查？**
按返回的 `checks` 字段逐个定位。多半是模型还在下载（TEI 首次启动拉模型很慢）或 GPU 显存不足。

**Q：切块大小怎么定？**
先跑一遍 `scripts/eval` 建立基线，再改 `CHILD_CHUNK_TOKENS` 对比 `Recall@5`。没有基线就调参 = 玄学。

**Q：Qwen3-Embedding-8B 换掉 bge-m3 值不值？**
值得测。bge-m3 的强项是 dense+sparse+multi-vector 多目标统一，dense-only 在零样本 BEIR 上有独立评测显示落后 E5/mE5 类模型。但切换要重刷全量向量 —— 先用评估集对比再决定。

## 与 RAGFlow 的关系

本服务与 RAGFlow 是**互补**关系，不是替代。详见 `deploy/ragflow/README.md`。

- RAGFlow：文档录入、解析、对话 UI —— 开箱即用，解析能力现成
- 本服务：混合检索、权限过滤、LangGraph 编排、评估体系 —— 需要深度定制的部分

推荐路径：先用 RAGFlow 跑通"检索效果能不能打"，跑通后再把混合检索与权限剥离到本服务。

## 许可与合规

- MinerU 3.1.0 起采用 Apache 2.0 的 MinerU 开源许可证（商业落地无门槛）
- bge-m3 为 MIT；bge-reranker-v2 系列为 Apache-2.0
- Qwen3 系列为 Apache-2.0
- DeepSeek V4 系列权重为 MIT
