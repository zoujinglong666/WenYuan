import type {
  ChatRequest,
  ChatResponse,
  ChunkSource,
  ConversationDetail,
  ConversationSummary,
  DocumentInfo,
  IngestResponse,
  ReadyChecks,
  SearchResponse,
  ServiceInfo,
  StatsResponse,
  StreamEvent,
} from './types'

/**
 * API Key 在构建时注入（VITE_API_KEY）。
 *
 * ⚠️ 打进 bundle 的密钥不等于真正的鉴权 —— 生产环境应当由网关 / SSO
 * 接管身份，浏览器只拿到一个短期令牌。这是当前 MVP 的取舍，
 * 详见 README「权限模型」一节。
 */
const API_KEY = import.meta.env.VITE_API_KEY as string | undefined

class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
  ) {
    super(message)
    this.name = 'ApiError'
  }
}

function headers(extra?: HeadersInit): HeadersInit {
  const base: Record<string, string> = { 'Content-Type': 'application/json' }
  if (API_KEY) base['X-API-Key'] = API_KEY
  return { ...base, ...(extra as Record<string, string> | undefined) }
}

/**
 * 把后端的错误体变成一句人能读的话。
 *
 * FastAPI 的 `detail` 不是永远都是字符串：
 * - 校验失败（422）时是**数组**：`[{loc: ["body", "query"], msg: "Field required"}]`
 * - 业务错误时才是字符串：`"未找到该片段，或你无权查看"`
 *
 * 直接 `body?.detail ?? detail` 会把 422 的数组塞进 Error，界面显示成
 * `[object Object]` —— 用户既看不到字段名，也看不到原因。
 */
function normalizeDetail(body: unknown, fallback: string): string {
  const detail = (body as { detail?: unknown } | null | undefined)?.detail

  if (typeof detail === 'string' && detail.trim()) return detail

  if (Array.isArray(detail)) {
    const msgs = detail
      .map((item) => {
        if (typeof item === 'string') return item
        if (!item || typeof item !== 'object') return ''
        const rec = item as Record<string, unknown>
        const msg = typeof rec.msg === 'string' ? rec.msg : ''
        const loc = Array.isArray(rec.loc) ? rec.loc.slice(1).join('.') : ''
        if (msg && loc) return `${loc}：${msg}`
        return msg || loc
      })
      .filter(Boolean)
      .join('；')
    if (msgs) return msgs
  }

  if (detail != null && typeof detail === 'object') {
    try {
      return JSON.stringify(detail)
    } catch {
      /* 循环引用等极端情况，落到 fallback */
    }
  }

  return fallback
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const resp = await fetch(path, {
    ...init,
    headers: headers(init?.headers),
  })
  if (!resp.ok) {
    let detail = resp.statusText
    try {
      detail = normalizeDetail(await resp.json(), detail)
    } catch {
      /* 响应体不是 JSON 时保留 statusText */
    }
    throw new ApiError(detail, resp.status)
  }
  return (await resp.json()) as T
}

export const api = {
  /** 服务元信息 —— `demo` 决定界面要不要标注「演示数据」。 */
  info: () => request<ServiceInfo>('/v1/info'),

  /**
   * 就绪探针。
   *
   * 未就绪时后端会回 503，但响应体里带着每个依赖的失败原因 ——
   * 状态页要靠它显示「到底是哪一环挂了」，所以这里不按 ok/err 抛异常。
   */
  ready: async () => {
    const resp = await fetch('/ready', { headers: headers() })
    const body = (await resp.json().catch(() => null)) as ReadyChecks | null
    if (!body) throw new ApiError(resp.statusText, resp.status)
    return body
  },

  /**
   * 取 chunk 原文。
   *
   * 权限与检索期一致：后端按调用者主体过滤 ACL，
   * 越权与不存在都回 404。身份必须一起带上 —— 只传 chunk_id 是取不到的。
   */
  chunk: (
    chunkId: string,
    userId: string,
    departments: string[] = [],
    roles: string[] = [],
  ) => {
    const params = new URLSearchParams({ user_id: userId })
    for (const d of departments) params.append('departments', d)
    for (const r of roles) params.append('roles', r)
    return request<ChunkSource>(`/v1/chunks/${encodeURIComponent(chunkId)}?${params.toString()}`)
  },

  stats: () => request<StatsResponse>('/v1/stats'),

  documents: () => request<DocumentInfo[]>('/v1/documents'),

  deleteDocument: (docId: string) =>
    request<{ status: string; doc_id: string }>(`/v1/documents/${encodeURIComponent(docId)}`, {
      method: 'DELETE',
    }),

  ingest: async (file: File, aclIds: string[], preferParser?: string): Promise<IngestResponse> => {
    const form = new FormData()
    form.append('file', file)
    form.append('acl_ids', aclIds.join(','))
    if (preferParser) form.append('prefer_parser', preferParser)

    const resp = await fetch('/v1/documents', {
      method: 'POST',
      headers: API_KEY ? { 'X-API-Key': API_KEY } : undefined,
      body: form,
    })
    if (!resp.ok) {
      let detail = resp.statusText
      try {
        detail = normalizeDetail(await resp.json(), detail)
      } catch {
        /* 非 JSON 错误体 */
      }
      throw new ApiError(detail, resp.status)
    }
    return (await resp.json()) as IngestResponse
  },

  search: (
    query: string,
    topK: number,
    userId: string,
    departments: string[],
    roles: string[] = [],
  ) =>
    request<SearchResponse>('/v1/search', {
      method: 'POST',
      body: JSON.stringify({
        query,
        top_k: topK,
        user_id: userId,
        departments,
        roles,
      }),
    }),

  chat: (body: ChatRequest) =>
    request<ChatResponse>('/v1/chat', {
      method: 'POST',
      body: JSON.stringify(body),
    }),

  /**
   * 会话持久化（spec.md RQ-1）。
   * 归属校验在后端：拿不到的会话一律 404，与「不存在」不可区分。
   */
  conversations: {
    list: (userId: string, limit = 50) =>
      request<ConversationSummary[]>(
        `/v1/conversations?user_id=${encodeURIComponent(userId)}&limit=${limit}`,
      ),
    get: (id: string, userId: string) =>
      request<ConversationDetail>(
        `/v1/conversations/${encodeURIComponent(id)}?user_id=${encodeURIComponent(userId)}`,
      ),
    create: (userId: string) =>
      request<ConversationSummary>(
        `/v1/conversations?user_id=${encodeURIComponent(userId)}`,
        { method: 'POST' },
      ),
    remove: (id: string, userId: string) =>
      request<{ status: string; conversation_id: string }>(
        `/v1/conversations/${encodeURIComponent(id)}?user_id=${encodeURIComponent(userId)}`,
        { method: 'DELETE' },
      ),
  },
}

/**
 * 流式问答。
 *
 * 用 fetch + ReadableStream 而不是 EventSource：EventSource 只支持 GET，
 * 而问答必须带请求体。
 */
export async function chatStream(
  body: ChatRequest,
  onEvent: (event: StreamEvent) => void,
  signal?: AbortSignal,
): Promise<void> {
  const resp = await fetch('/v1/chat/stream', {
    method: 'POST',
    headers: headers(),
    body: JSON.stringify(body),
    signal,
  })

  if (!resp.ok || !resp.body) {
    let detail = resp.statusText
    try {
      detail = normalizeDetail(await resp.json(), detail)
    } catch {
      /* 非 JSON 错误体 */
    }
    throw new ApiError(detail, resp.status)
  }

  const reader = resp.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''

  while (true) {
    const { done, value } = await reader.read()
    if (done) break

    buffer += decoder.decode(value, { stream: true })

    // SSE 以空行分隔事件
    let boundary = buffer.indexOf('\n\n')
    while (boundary !== -1) {
      const raw = buffer.slice(0, boundary)
      buffer = buffer.slice(boundary + 2)
      const event = parseSse(raw)
      if (event) onEvent(event)
      boundary = buffer.indexOf('\n\n')
    }
  }

  // 流结束时可能还残留最后一段
  const tail = parseSse(buffer)
  if (tail) onEvent(tail)
}

function parseSse(raw: string): StreamEvent | null {
  if (!raw.trim()) return null

  let eventName = 'message'
  const dataLines: string[] = []

  for (const line of raw.split('\n')) {
    if (line.startsWith('event:')) {
      eventName = line.slice(6).trim()
    } else if (line.startsWith('data:')) {
      dataLines.push(line.slice(5).trimStart())
    }
  }

  if (dataLines.length === 0) return null

  try {
    const payload = JSON.parse(dataLines.join('\n'))
    return { ...payload, type: eventName } as StreamEvent
  } catch {
    return null
  }
}

export { ApiError }
