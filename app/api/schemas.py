"""API 请求 / 响应模型。"""

from __future__ import annotations

from pydantic import BaseModel, Field


class Message(BaseModel):
    role: str = Field(default="user", pattern="^(user|assistant)$")
    content: str


class ChatRequest(BaseModel):
    question: str = Field(min_length=1, max_length=4000)
    #: 调用方身份。生产环境应从鉴权中间件（Bearer / SSO）注入，而不是信客户端传值
    user_id: str = Field(default="anonymous")
    departments: list[str] = Field(default_factory=list)
    roles: list[str] = Field(default_factory=list)
    history: list[Message] = Field(default_factory=list)
    #: 限定检索范围（按知识库 / 文档集过滤）
    doc_ids: list[str] | None = None
    #: 所属会话。缺省时服务端自动建会话并在响应中回传 id（见 spec.md RQ-1）
    conversation_id: str | None = None


class Citation(BaseModel):
    index: int
    chunk_id: str
    doc_id: str = ""
    filename: str = ""
    heading_text: str = ""
    page: int | None = None
    snippet: str = ""


class RetrievalTrace(BaseModel):
    """检索链路的真实计数与耗时。

    界面用它把「召回了什么」摊开给用户看：
    两路通道各召回多少、融合后剩多少、精排留下多少、最终喂给模型几块。
    """

    #: 本次实际执行的检索查询（含改写结果）
    queries: list[str] = Field(default_factory=list)
    bm25_hits: int = 0
    dense_hits: int = 0
    merged: int = 0
    reranked: int = 0
    context_blocks: int = 0
    #: 各阶段耗时，单位毫秒
    timings: dict[str, float] = Field(default_factory=dict)


class ChatResponse(BaseModel):
    answer: str
    citations: list[Citation] = Field(default_factory=list)
    retrieval: RetrievalTrace = Field(default_factory=RetrievalTrace)
    #: 本次问答归属的会话（请求未带 id 时为服务端新建的会话）
    conversation_id: str | None = None
    #: 命中敏感词被拒绝（spec RQ-2）：answer 是固定提示文案
    refused: bool = False


# ---------------- 会话持久化（spec.md RQ-1） ----------------


class ConversationSummary(BaseModel):
    id: str
    title: str
    message_count: int = 0
    created_at: str = ""
    updated_at: str = ""


class StoredMessage(BaseModel):
    seq: int
    role: str
    content: str
    citations: list[Citation] = Field(default_factory=list)
    created_at: str = ""


class ConversationDetail(BaseModel):
    id: str
    title: str
    user_id: str = ""
    created_at: str = ""
    updated_at: str = ""
    messages: list[StoredMessage] = Field(default_factory=list)


class IngestResponse(BaseModel):
    doc_id: str
    filename: str
    status: str
    chunk_count: int = 0
    skipped: bool = False
    parser: str = ""
    error: str | None = None
    timings: dict[str, float] = Field(default_factory=dict)


class DocumentInfo(BaseModel):
    doc_id: str
    filename: str
    title: str
    status: str
    version: int
    chunk_count: int
    parser: str = ""
    acl_ids: list[str] = Field(default_factory=list)


class SearchRequest(BaseModel):
    """只检索不生成，用于调试与评估。"""

    query: str = Field(min_length=1)
    user_id: str = "anonymous"
    departments: list[str] = Field(default_factory=list)
    roles: list[str] = Field(default_factory=list)
    top_k: int = Field(default=10, ge=1, le=100)
    doc_ids: list[str] | None = None


class SearchHit(BaseModel):
    chunk_id: str
    filename: str = ""
    heading_text: str = ""
    rrf_score: float = 0.0
    rerank_score: float | None = None
    sources: list[str] = Field(default_factory=list)
    snippet: str = ""


class SearchResponse(BaseModel):
    hits: list[SearchHit] = Field(default_factory=list)
    queries: list[str] = Field(default_factory=list)
    retrieval: RetrievalTrace = Field(default_factory=RetrievalTrace)


class ChunkSource(BaseModel):
    """chunk 原文 —— 引用溯源要能回到出处，而不只是回到一段摘要。

    权限与检索一致：**只返回调用者有权查看的 chunk**，
    越权访问按「不存在」处理（403 会泄露 chunk 是否存在）。
    """

    chunk_id: str
    doc_id: str = ""
    filename: str = ""
    heading_text: str = ""
    page: int | None = None
    text: str = ""
