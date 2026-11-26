# 企业知识库 RAG —— 项目规格（SDD）

> 本项目采用轻量规格驱动开发（Spec-Driven Development）：
> 每个需求先在本文成文（背景 / 目标 / 接口 / 验收标准），再在 [tasks.md](tasks.md) 拆任务，
> 完成后按 [checklist.md](checklist.md) 逐项验收。改动与规格不符时，先改规格再改代码。

## 1. 项目概述

企业内部知识库问答系统（RAG）：文档接入 → 解析切块 → 双通道检索（BM25 + 向量）→ RRF 融合 → 精排 → 生成回答（带引用溯源）。

- **后端** `app/`：FastAPI + 异步 Python。LLM 可插拔（local / deepseek / openai 兼容 / extractive 抽取式降级）；
  检索、存储各层均有 local 单机实现（SQLite / 本地向量库 / 本地 BM25），生产可切 Qdrant / Elasticsearch / PostgreSQL。
- **前端** `web/`：React 19 + Vite + Tailwind v4 + TanStack Query。SSE 流式问答，引用可溯源到 chunk 原文。
- **交互基线**：Apple HIG 风格 —— 中性灰白配色、胶囊控件、按压缩放反馈、150ms ease-out 过渡、
  非必要元素一律删除；降级状态必须可悬浮查明原因（DepTooltip）。
- **流程基线**：Windows 本地启动后端必须用 `python -m app.main`（Selector 事件循环），禁止 uvicorn CLI。

## 2. 技术约束

1. 后端所有 IO 异步化；存储层每操作短连接（aiosqlite / asyncpg 模式一致）。
2. 权限：检索与 chunk 溯源均按调用方主体（user_id / departments / roles）过滤 ACL；
   生产身份必须来自网关/SSO，不信客户端传值。
3. 检索失败或依赖缺失时如实降级（extractive / 明确报错），不静默编造。
4. 前端错误文案必须人能读（normalizeDetail），状态必须可解释（就绪/降级/不可达 + 原因）。

## 3. 需求索引

| 编号 | 名称 | 状态 |
| --- | --- | --- |
| RQ-1 | 聊天会话持久化（服务端存储） | 已完成 |
| RQ-2 | 提问敏感词过滤（入口拒绝） | 已完成 |

---

## RQ-2 提问敏感词过滤

### 背景
知识库问答面向企业全员开放，入口需要内容安全底线：命中敏感词的提问不进检索、
不调模型（省下游调用 + 留审计记录），直接拒绝并提示文明用语。

### 目标
1. 词库为纯文本文件 `data/sensitive_words.txt`（每行一词，`#` 注释），运维改文件
   即生效（按 mtime 热加载），无需重启后端。
2. 匹配前归一化（全角→半角、大小写、剔除空白与装饰符号）——「傻 B」「傻  b」同命中。
3. 过滤在 `/v1/chat` 与 `/v1/chat/stream` 入口执行（`app/core/sensitive.py` 接缝，
   未来可替换为词库服务）。
4. 拒绝轮照常落库（问题 + 固定提示文案），保留完整对话审计记录。

### 非目标
不做回答内容的二次审核（生成后过滤）、不做用户画像/封禁策略。

### 接口契约
- 非流式：`ChatResponse.refused: bool`，`answer` 为固定提示文案，`citations: []`。
- 流式：事件序列变为 `refused` → `done`（`answer`=提示文案、`refused: true`、
  `conversation_id` 照常回传），不发 `trace/delta/citations`。
- 提示文案统一为「您的提问包含不文明用语，请调整措辞、文明提问后再试。」

### 验收标准
- A1: 命中词（含变体）提问 → 拒绝提示，日志无任何检索/LLM 调用。
- A2: 正常提问不受影响；词库中不存在的词不误伤。
- A3: 词库文件修改后无需重启即生效。
- A4: 拒绝轮落库；切换页面 / 刷新后历史中该轮以警示样式呈现。
- A5: 开关 `ENABLE_SENSITIVE_FILTER=false` 时完全旁路。

---

## RQ-1 聊天会话持久化

### 背景

`ChatPage` 的对话记录存于组件本地 `useState`，切换页面即卸载丢失；刷新浏览器也丢失。
企业级主流做法是**服务端持久化会话**：前端只持有 `conversation_id`，历史从后端拉取。

### 目标

1. 对话记录（问题、回答、引用）持久化到服务端 SQLite。
2. 切换页面、刷新浏览器后回到聊天页，能完整恢复当前会话。
3. 支持多会话：新建、切换、删除，会话列表从后端拉取。
4. 用户中止生成时，已生成的部分回答也入库（标注未完成不必须，内容优先）。

### 非目标（本期不做）

- 会话标题自动摘要（首条问题截断即标题）。
- 多端同步 / 会话分享 / 权限继承（单人 MVP，user_id 归属校验防呆即可）。
- PostgreSQL 会话表（沿用 local 优先模式，接口与 local 实现一致，生产再补 PG 实现）。

### 数据模型（SQLite `data/local_store/conversations.db`）

```sql
conversations(id TEXT PRIMARY KEY,            -- uuid4 hex
              user_id TEXT NOT NULL,          -- 归属主体，get/delete 校验
              title TEXT NOT NULL,            -- 首条问题前 24 字
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL);
messages(seq INTEGER PRIMARY KEY AUTOINCREMENT,
         conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
         role TEXT NOT NULL,                  -- user | assistant
         content TEXT NOT NULL,
         citations TEXT NOT NULL DEFAULT '[]',-- Citation[] JSON
         created_at TEXT NOT NULL);
```

### API 契约

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| POST | `/v1/conversations` | 建空会话 `{id,title,user_id,...}` |
| GET | `/v1/conversations` | 列表（按 updated_at 倒序，limit≤100） |
| GET | `/v1/conversations/{id}` | 详情含全部消息；不存在或非本人 → 404 |
| DELETE | `/v1/conversations/{id}` | 删除（级联消息）；404 同上 |
| POST | `/v1/chat`、`/v1/chat/stream` | 请求体新增可选 `conversation_id`；缺省时服务端自动建会话。一次性响应与流式 `done` 事件均回传 `conversation_id` |

消息写入规则：每次问答成功（拿到回答文本）后，按序追加 `user` 与 `assistant` 两条消息；
流式中止但已有部分回答时，同样追加（内容为已生成部分）。

### 前端行为

1. 当前会话 id 存 `localStorage`（`kb.current_conversation`）；ChatPage 挂载时凭 id 拉详情重建消息流，404 则静默清空回落新会话。
2. 每次发问带 `conversation_id`；收到响应中的新 id 后立即落 localStorage。
3. ChatPage 顶部会话栏：`新对话` 按钮 + 历史会话下拉（点击切换、可删除），风格与全站胶囊交互一致。
4. 重建的消息 id 带固定前缀，避免与实时轮次序号冲突；引用列表交互（展开原文、高亮）与实时消息一致。

### 验收标准（详见 checklist.md）

- 发问 → 切到文档页 → 切回：历史完整。
- 发问 → 刷新浏览器 → 回到聊天页：历史完整。
- 新建第二个会话提问 → 在历史下拉来回切换：内容互不串扰。
- 删除会话：列表与当前会话正确回落；后端消息级联删除。
