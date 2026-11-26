# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Stack

前端 React + TypeScript + Vite（用户确认）；后端 FastAPI（用户确认），Python 3.11+。
私有化内网部署，前端构建产物由 FastAPI 直接托管，单端口对外。

## Users

两类主要用户，同一套系统：

1. **企业员工** —— 在办公场景下查询内部制度、流程、报销标准、合同编号等。日常高频使用，随时可能提问。他们不懂检索工程，但看得懂答案和原文出处。
2. **知识管理员 / 知识运营** —— 负责文档入库、权限配置、检索质量排查。关注 chunk 质量、召回排序、评估指标。

## Product Purpose

把企业内部散落的文档（制度、手册、合同台账、财务规定、飞书/网页导出）变成可问答、可溯源的知识。

成功的定义：一个员工用自然语言提问，系统基于**他有权查看的**文档给出带引用的答案；答案不编造，引用能跳回原文；管理员能看清每一次检索召回了什么、为什么是这个结果。

## Positioning

**权限前置过滤的混合检索层。**

大多数 RAG 演示只做「向量召回 + LLM 生成」。本产品的差异在于：

- **BM25 与向量双通道并行，RRF 融合** —— 合同编号「HT-2024-0873」这类必须字面命中的查询，纯向量召不回；同义改写、口语化提问，纯关键词召不回。两路都不可替代。
- **ACL 作为 filter 下推到查询条件** —— 无权文档根本不进入候选集。这不是「召回后过滤」的安全性问题（那样无权片段已经进过 prompt，模型仍会引用），而是架构问题。
- **父子块（Small-to-Big）** —— 子块送去检索（短、精度高），父块送去喂 LLM（章节级、上下文完整）。表格整块不切。
- **检索评估与生成评估分离** —— Recall@K 没达标就去调 Prompt 是最常见的错误排序。检索问题只能在切块、索引、检索层解决。

与 RAGFlow / Dify 的关系是**互补而非替代**：RAGFlow 负责录入、解析与对话 UI，本服务负责检索编排、权限与评估。

## Operating Context

- **部署形态**：私有化内网。数据不出内网，模型（embedding / rerank / LLM / 文档解析）全部自托管。
- **模型组合**：内网默认 Qwen3-32B-AWQ 自托管；如需 DeepSeek 走官方 API（`deepseek-flash` = DeepSeek-V4.1-Flash-1017），需合规审批。Embedding 用 bge-m3，Rerank 用 bge-reranker-v2-m3，解析用 MinerU 3.x。
- **典型文档**：数字 PDF 与 Office（DOCX/PPTX/XLSX）为主，扫描件占比低 —— 因此解析预算不投 OCR，投在表格结构保真上。
- **日常动线**：员工提问 → 看答案 → 点引用跳原文；管理员上传新文档 → 看解析状态与 chunk 数 → 用检索调试页排查召回 → 跑评估脚本看 Recall@5。
- **高频场景**：制度条款查询、报销标准、合同编号检索、跨文档对比。

## Capabilities and Constraints

已确认：

- 文档摄入（上传 → 解析 → 切块 → 向量化 → 双引擎入库），按内容 hash 增量索引
- 混合检索（BM25 + 向量 + RRF）+ Cross-Encoder 精排
- 权限主体模型：`public` / `user:<id>` / `dept:<部门>` / `role:<角色>` / `project:<项目>`，默认拒绝
- 对话问答，SSE 流式输出，带引用溯源
- 检索调试接口（只检索不生成）
- 检索评估脚本（Recall@K / MRR / nDCG@K）+ 金标集

未决 / 明确待办：

- **鉴权**：当前 API 直接信任客户端传入的 `user_id`，生产上线前必须改为从 SSO / 网关注入身份。
- **IAM 对接**：`PrincipalResolver` 仍是静态映射，需接企业 IAM（LDAP / AD / 飞书 / 钉钉）。
- **外部源同步**：飞书/钉钉/SharePoint 的 webhook 增量推送尚未实现，当前只能手动上传。
- **Embedding 模型对比**：bge-m3 的 dense-only 模式在独立评测中落后 E5/mE5 类模型；是否换 Qwen3-Embedding-8B 需用评估集实测后决定，尚未决定。

## Brand Commitments

无既有品牌资产。产品名为「企业知识库」，界面语言为简体中文，术语统一使用中文（chunk、embedding、rerank 等技术词保留英文）。

## Evidence on Hand

- 真实产品代码与文档：`app/`（FastAPI 检索服务）、`docs/tech-brainstorm.md`（技术选型脑暴）、`deploy/ragflow/README.md`（RAGFlow 对接路径）
- 金标集样例：`data/goldens/retrieval_golden.jsonl` —— **当前为占位示例**，`relevant_doc_ids` 里的文档名尚未对应真实文档
- 没有真实企业文档、截图、客户案例、效果数据。任何演示数据都需标注为合成示例。

## Product Principles

1. **先证明召得回，再谈答得准。** 检索评估与生成评估分开，诊断顺序固定。
2. **权限在检索期生效，不在展示期补救。**
3. **答案必须可溯源。** 引用能跳回原文；无据可依时明说不知道，而不是编。
4. **解析质量决定上限。** 表格不拍平，标题路径全程携带，解析层值得单独投入。
5. **工具应该暴露机制，而不是藏起来。** 管理员能看到召回排序、通道命中、分数与耗时 —— 这是可排查性的前提。

## Accessibility & Inclusion

- 键盘可达：所有交互元素可 Tab 聚焦，焦点态可见；对话输入区支持 Enter 发送 / Shift+Enter 换行
- 对比度：正文与占位文字 ≥ 4.5:1
- 尊重 `prefers-reduced-motion`，流式输出与过渡动画在减弱动效下不产生位移
- 状态不只用颜色表达：检索通道、就绪状态、解析状态均带文字标签
