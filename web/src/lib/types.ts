/** 与后端 `app/api/schemas.py` 对应的类型。 */

export interface Message {
  role: 'user' | 'assistant'
  content: string
}

export interface Citation {
  index: number
  chunk_id: string
  doc_id: string
  filename: string
  heading_text: string
  page: number | null
  snippet: string
}

/** 检索链路的真实计数与耗时 —— 前端「检索轨道」据此点亮。 */
export interface RetrievalTrace {
  queries: string[]
  bm25_hits: number
  dense_hits: number
  merged: number
  reranked: number
  context_blocks: number
  timings: Record<string, number>
}

export interface ChatRequest {
  question: string
  user_id: string
  departments: string[]
  roles: string[]
  history: Message[]
  doc_ids?: string[] | null
  /** 所属会话；缺省由服务端自动创建并在响应中回传 */
  conversation_id?: string | null
}

export interface ChatResponse {
  answer: string
  citations: Citation[]
  retrieval: RetrievalTrace
  conversation_id?: string | null
}

/** 会话持久化（spec.md RQ-1）——与服务端 SQLite 会话仓库对应 */
export interface ConversationSummary {
  id: string
  title: string
  message_count: number
  created_at: string
  updated_at: string
}

export interface StoredMessage {
  seq: number
  role: 'user' | 'assistant'
  content: string
  citations: Citation[]
  created_at: string
}

export interface ConversationDetail {
  id: string
  title: string
  user_id: string
  created_at: string
  updated_at: string
  messages: StoredMessage[]
}

export interface DocumentInfo {
  doc_id: string
  filename: string
  title: string
  status: string
  version: number
  chunk_count: number
  parser: string
  acl_ids: string[]
}

export interface IngestResponse {
  doc_id: string
  filename: string
  status: string
  chunk_count: number
  skipped: boolean
  parser: string
  error: string | null
  timings: Record<string, number>
}

export interface SearchHit {
  chunk_id: string
  filename: string
  heading_text: string
  rrf_score: number
  rerank_score: number | null
  sources: string[]
  snippet: string
}

export interface SearchResponse {
  hits: SearchHit[]
  queries: string[]
  retrieval: RetrievalTrace
}

/** chunk 原文 —— 引用要能回到出处，而不只是回到一句摘要。 */
export interface ChunkSource {
  chunk_id: string
  doc_id: string
  filename: string
  heading_text: string
  page: number | null
  text: string
}

/** 服务元信息 —— `demo` 为真时界面必须把合成数据标出来。 */
export interface ServiceInfo {
  service: string
  version: string
  docs: string
  demo: boolean
  llm_provider: string
  llm_model: string
  embedding_model: string
  rerank_model: string
}

export interface ReadyChecks {
  ready: boolean
  checks: Record<string, string>
}

export interface StatsResponse {
  documents: number | null
  chunks: number | null
  qdrant_points: number | null
  es_documents: number | null
  errors: Record<string, string>
  config: {
    llm_provider: string
    llm_model: string
    embedding_model: string
    rerank_model: string
    retrieval: {
      retrieve_top_k: number
      rerank_top_k: number
      rrf_k: number
    }
    acl_enabled: boolean
  }
}

/** SSE 事件 —— 与后端 `app/api/routes_chat.py` 的流式协议一致。 */
export type StreamEvent =
  | { type: 'trace'; stage: string; trace: RetrievalTrace }
  | { type: 'delta'; text: string }
  | { type: 'citations'; citations: Citation[] }
  | { type: 'refused'; message: string }
  | { type: 'done'; trace?: RetrievalTrace; conversation_id?: string | null; refused?: boolean; answer?: string }
  | { type: 'error'; message: string }
