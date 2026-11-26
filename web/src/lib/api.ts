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
import {
  clearSession,
  getAccessToken,
  getRefreshToken,
  logoutRedirect,
  saveSession,
  saveTokens,
  type AuthUser,
} from './auth'

/**
 * 鉴权：登录拿 token 双件套，之后所有请求自动带 Bearer access。
 * 401 时用 refresh 静默换新并重放一次；refresh 也失效才踢回登录页。
 */
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
  const token = getAccessToken()
  if (token) base['Authorization'] = `Bearer ${token}`
  return { ...base, ...(extra as Record<string, string> | undefined) }
}

/**
 * 把后端的错误体变成一句人能读的话。
 *
 * FastAPI 的 `detail` 不是永远都是字符串：
 * - 校验失败（422）时是**数组**：`[{loc: ["body", "query"], msg: "Field required"}]`
 * - 业务错误时才是字符串：`"未找到该片段，或你无权查看"`
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

async function readError(resp: Response): Promise<ApiError> {
  let detail = resp.statusText
  try {
    detail = normalizeDetail(await resp.json(), detail)
  } catch {
    /* 响应体不是 JSON 时保留 statusText */
  }
  return new ApiError(detail, resp.status)
}

interface TokenPairBody {
  access_token: string
  refresh_token: string
  user: AuthUser
}

let refreshing: Promise<boolean> | null = null

/** 用 refresh 换新 token 对。并发 401 只发一次刷新，其余等结果。 */
async function tryRefresh(): Promise<boolean> {
  if (refreshing) return refreshing
  refreshing = (async () => {
    const refresh = getRefreshToken()
    if (!refresh) return false
    try {
      const resp = await fetch('/v1/auth/refresh', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ refresh_token: refresh }),
      })
      if (!resp.ok) return false
      const body = (await resp.json()) as TokenPairBody
      saveSession(body.access_token, body.refresh_token, body.user)
      return true
    } catch {
      return false
    } finally {
      refreshing = null
    }
  })()
  return refreshing
}

async function request<T>(path: string, init?: RequestInit, retried = false): Promise<T> {
  const resp = await fetch(path, {
    ...init,
    headers: headers(init?.headers),
  })

  if (resp.status === 401 && !retried && !path.startsWith('/v1/auth/')) {
    // access 过期：静默刷新后重放一次；refresh 也挂了就踢回登录
    if (await tryRefresh()) return request<T>(path, init, true)
    clearSession()
    logoutRedirect()
    throw new ApiError('登录已过期', 401)
  }

  if (!resp.ok) throw await readError(resp)
  return (await resp.json()) as T
}

export const authApi = {
  async login(username: string, password: string): Promise<AuthUser> {
    const resp = await fetch('/v1/auth/login', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ username, password }),
    })
    if (!resp.ok) throw await readError(resp)
    const body = (await resp.json()) as TokenPairBody
    saveSession(body.access_token, body.refresh_token, body.user)
    return body.user
  },

  async logout(): Promise<void> {
    const refresh = getRefreshToken()
    try {
      if (refresh) {
        await fetch('/v1/auth/logout', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ refresh_token: refresh }),
        })
      }
    } finally {
      clearSession()
    }
  },

  /** 自助注册：账号默认禁用，需管理员审批后才能登录 */
  async register(username: string, password: string, displayName: string): Promise<string> {
    const resp = await fetch('/v1/auth/register', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ username, password, display_name: displayName }),
    })
    if (!resp.ok) throw await readError(resp)
    const body = (await resp.json()) as { ok: boolean; message: string }
    return body.message
  },

  /** access 即将过期时的主动续期（路由切换时调用，可选） */
  async refreshNow(): Promise<boolean> {
    const refresh = getRefreshToken()
    if (!refresh) return false
    const resp = await fetch('/v1/auth/refresh', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ refresh_token: refresh }),
    })
    if (!resp.ok) return false
    const body = (await resp.json()) as TokenPairBody
    saveTokens(body.access_token, body.refresh_token)
    return true
  },
}

export const api = {
  /** 服务元信息 —— `demo` 决定界面要不要标注「演示数据」。 */
  info: () => request<ServiceInfo>('/v1/info'),

  /**
   * 就绪探针（公开接口，无需登录）。
   *
   * 未就绪时后端会回 503，但响应体里带着每个依赖的失败原因 ——
   * 状态页要靠它显示「到底是哪一环挂了」，所以这里不按 ok/err 抛异常。
   */
  ready: async () => {
    const resp = await fetch('/ready')
    const body = (await resp.json().catch(() => null)) as ReadyChecks | null
    if (!body) throw new ApiError(resp.statusText, resp.status)
    return body
  },

  /**
   * 取 chunk 原文（引用溯源）。身份由后端从 token 解出，前端只传 chunk_id。
   * 权限与检索期一致：越权与不存在都回 404。
   */
  chunk: (chunkId: string) =>
    request<ChunkSource>(`/v1/chunks/${encodeURIComponent(chunkId)}`),

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
      headers: headers(),
      body: form,
    })
    if (!resp.ok) throw await readError(resp)
    return (await resp.json()) as IngestResponse
  },

  /** 检索调试（管理员）。身份由后端从 token 解出。 */
  search: (query: string, topK: number) =>
    request<SearchResponse>('/v1/search', {
      method: 'POST',
      body: JSON.stringify({ query, top_k: topK }),
    }),

  chat: (body: ChatRequest) =>
    request<ChatResponse>('/v1/chat', {
      method: 'POST',
      body: JSON.stringify(body),
    }),

  /**
   * 会话持久化（spec.md RQ-1）。
   * 归属校验在后端：拿不到的会话一律 404，与「不存在」不可区分。
   * 身份由后端从 token 解出 —— 前端不再传 user_id。
   */
  conversations: {
    list: (limit = 50) => request<ConversationSummary[]>(`/v1/conversations?limit=${limit}`),
    get: (id: string) => request<ConversationDetail>(`/v1/conversations/${encodeURIComponent(id)}`),
    create: () => request<ConversationSummary>('/v1/conversations', { method: 'POST' }),
    remove: (id: string) =>
      request<{ status: string; conversation_id: string }>(
        `/v1/conversations/${encodeURIComponent(id)}`,
        { method: 'DELETE' },
      ),
  },

  // ---------------- 审计与用户管理（管理员） ----------------

  audit: {
    query: (params: { user_id?: string; action?: string; limit?: number } = {}) => {
      const q = new URLSearchParams()
      if (params.user_id) q.set('user_id', params.user_id)
      if (params.action) q.set('action', params.action)
      q.set('limit', String(params.limit ?? 200))
      return request<AuditRow[]>(`/v1/audit?${q.toString()}`)
    },
  },

  users: {
    list: () => request<UserRow[]>('/v1/auth/users'),
    create: (body: { username: string; password: string; display_name?: string; departments?: string[]; roles?: string[] }) =>
      request<UserRow>('/v1/auth/users', { method: 'POST', body: JSON.stringify(body) }),
    setDisabled: (userId: string, disabled: boolean) =>
      request<UserRow>(`/v1/auth/users/${encodeURIComponent(userId)}`, {
        method: 'PATCH',
        body: JSON.stringify({ disabled }),
      }),
  },
}

export interface AuditRow {
  id: number
  ts: string
  user_id: string
  username: string
  action: string
  target: string
  detail: string
  ip: string
  user_agent: string
}

export interface UserRow {
  id: string
  username: string
  display_name: string
  departments: string[]
  roles: string[]
  disabled: boolean
  created_at?: string
  last_login_at?: string | null
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

  if (resp.status === 401) {
    if (await tryRefresh()) return chatStream(body, onEvent, signal)
    clearSession()
    logoutRedirect()
    throw new ApiError('登录已过期', 401)
  }

  if (!resp.ok || !resp.body) throw await readError(resp)

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
