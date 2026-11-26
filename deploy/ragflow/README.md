# RAGFlow 对接说明

> 核实于 2026-11-25。RAGFlow 版本：1.0 系列。

## 定位：互补，不是替代

一句话分工：**RAGFlow 负责知识供给（解析 + 录入 + UI），本服务负责检索编排（混合召回 + 权限 + 评估）。**

| 能力 | RAGFlow | 本服务 |
|---|---|---|
| 文档录入 UI | ✅ 成熟 | ❌ |
| 文档解析（DeepDoc） | ✅ 强，表格/图片/多栏 PDF | ✅ MinerU 3.x（可替换） |
| 对话 UI / 助手编排 | ✅ | ❌ |
| 混合检索（BM25 ⊕ 向量 + RRF） | 基础向量检索 | ✅ 双引擎 |
| Cross-Encoder 精排 | 部分支持 | ✅ |
| **权限前置过滤（ACL）** | 弱 | ✅ 核心能力 |
| **检索评估体系（Recall@K/MRR/nDCG）** | 有 Retrieval Testing 页（单条调试） | ✅ 可批量、可进 CI |
| 增量索引 / 软删除 | 有，但无外部源自动同步 | ✅ hash 驱动 |
| 外部源同步（SharePoint/Drive/飞书） | ❌ 需手动或 API 触发 | ✅ 待接 |

## 三种对接路径

### 路径 A：RAGFlow 做前端，本服务做检索后端（推荐）

RAGFlow 1.0 明确把自身定位为「知识供给方」而非端到端 RAG 引擎，外部编排层负责调度。
这个定位天然适合我们的架构。

**RAGFlow 侧（已验证的 API）**：

```bash
# 1. 认证
curl -X POST http://ragflow:80/api/v1/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email":"<邮箱>","password":"<密码>"}'
# Profile > API Key 获取 API Key，认证用 Bearer

# 2. 上传文档到数据集
curl -X POST http://ragflow:80/api/v1/datasets/{dataset_id}/documents \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -F "file=@/data/差旅管理办法.pdf"

# 3. 发起问答
curl -X POST http://ragflow:80/api/v1/chats \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"name":"research-qa","dataset_ids":["your-dataset-id"]}'
```

**本服务侧**：把 RAGFlow 解析后的 chunk 灌入本服务，或让本服务独立解析（推荐，两条路可并存）。

RAGFlow 的 REST API 支持批量文档操作与流式响应，接入成本不高。

### 路径 B：RAGFlow 独立跑，用它的 Retrieval Testing 做评估

RAGFlow 在知识库下提供 **Retrieval Testing** 页面：输入查询即可看到**召回了哪些 chunk、相关性分数、来源文档**，且**不生成 LLM 答案**。

这一点和本服务 `POST /v1/search` 的定位完全一致 —— 都是"只检索不生成"，专门用来定位召回问题。

**建议做法**：同一份金标集（`data/goldens/retrieval_golden.jsonl`）分别喂给 RAGFlow Retrieval Testing 和本服务 `scripts/eval`，对比 `Recall@5`。这才是"要不要继续二开"的决策依据 —— 靠感觉评估没法做技术决策。

### 路径 C：渐进迁移（终态）

1. **第 1-2 周**：RAGFlow 独立部署，灌入真实文档，用 Retrieval Testing 建立召回基线
2. **第 3-4 周**：本服务独立跑同一批文档，同一套金标集对比 Recall@5
3. **第 5-6 周**：差距确认后，只把**有差距的部分**搬过来 —— 通常是权限过滤和多路 Query 改写
4. **第 7 周起**：RAGFlow 保留录入 UI，检索链路切到本服务

**不要一上来就二开 RAGFlow 源码**。它的解析与 UI 是真优势，改源码改坏了很难回滚。

## 值得注意的两个协同点

### 1. RAGFlow 的内置 ES 瓶颈，正好由我们的架构解决

RAGFlow 内置 Elasticsearch 容器，在 **10,000+ 文档**规模时会成为瓶颈。官方支持指向外部 ES 集群。

我们的架构本来就是 ES（关键词）+ Qdrant（向量）双引擎，**ES 可以直接复用为 RAGFlow 的外部集群**，两边共享同一份索引数据 —— 这能省掉一份重复索引，也让我们对"关键词路"和"向量路"的数据保持一致。

> ⚠️ 注意：两边共用 ES 索引要求索引结构与分词器一致。RAGFlow 内部使用的 analyzer 我们无法完全对齐，所以**建议先不共用**，而是让本服务独立维护索引、通过 API 互通。等结构对齐验证过再合并。

### 2. 权限模型是 RAGFlow 最弱的一环

RAGFlow 的权限主要到"数据集"和"用户"粒度，缺少 chunk 级的 ACL 前置过滤。企业场景里"部门可见 / 密级可见"是硬需求，这块必须自建。

本服务的权限模型：

- 主体：`public` / `user:<id>` / `dept:<部门>` / `role:<角色>` / `project:<项目>`
- 文档侧每条 chunk 带 `acl_ids`
- **ACL 作为 filter 下推到 Qdrant / ES 查询条件**，无权内容根本不进候选集

接入 IAM 时只改 `app/retrieval/acl.py` 的 `PrincipalResolver`，其余代码不动。

## RAGFlow 侧已知限制（规划时提前算进去）

| 限制 | 影响 | 我们的应对 |
|---|---|---|
| **无外部源自动同步**（Drive/SharePoint/飞书等需手动或 API 触发） | 企业知识有一半来自这些源 | 本服务侧实现 Webhook 增量推送 + 内容 hash 变更检测 |
| 内置 ES 在万级文档时成瓶颈 | 大规模部署卡住 | 双引擎架构，ES 可指向外部集群 |
| chunk 级 ACL 能力弱 | 部门/密级控制难做 | 本服务原生支持 |
| 检索评估不能批量、不能进 CI | 无法持续回归 | `scripts/eval` 可批量、可进 CI |

## 快速对照：同一份文档两条链路

```bash
# 方式 1：RAGFlow 解析 + RAGFlow 检索（基线）
#   RAGFlow UI → Retrieval Testing 页面

# 方式 2：RAGFlow 解析 + 本服务检索
docker compose exec api python -m scripts.ingest \
  --path /data/docs --acl public,dept:finance

docker compose exec api python -m scripts.eval \
  --goldens data/goldens/retrieval_golden.jsonl --k 5 --verbose

# 方式 3：本服务解析 + 本服务检索（MinerU 3.x）
docker compose exec api python -m scripts.ingest \
  --path /data/docs --prefer mineru --force
```

方式 2 与方式 3 的差距，就是"MinerU vs DeepDoc 的解析质量差距" —— 这个数字直接决定
"RAGFlow 的解析能力值不值得留着"。
