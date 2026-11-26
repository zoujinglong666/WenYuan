/**
 * 前端鉴权状态：token 双件套（access + refresh）与当前用户。
 *
 * 约定：
 * - access 短命（后端 30 分钟），401 时用 refresh 静默换新（见 api.ts）；
 * - refresh 轮换制 —— 每次刷新旧 token 作废，两端都以最新为准；
 * - 任何环节失败都清库跳登录，由路由守卫接管。
 */

const ACCESS_KEY = 'wenyuan.access_token'
const REFRESH_KEY = 'wenyuan.refresh_token'
const USER_KEY = 'wenyuan.user'

export interface AuthUser {
  user_id: string
  username: string
  display_name: string
  departments: string[]
  roles: string[]
  permissions: string[]
}

export function getAccessToken(): string | null {
  return localStorage.getItem(ACCESS_KEY)
}

export function getRefreshToken(): string | null {
  return localStorage.getItem(REFRESH_KEY)
}

export function getStoredUser(): AuthUser | null {
  try {
    const raw = localStorage.getItem(USER_KEY)
    return raw ? (JSON.parse(raw) as AuthUser) : null
  } catch {
    return null
  }
}

export function saveSession(access: string, refresh: string, user: AuthUser): void {
  localStorage.setItem(ACCESS_KEY, access)
  localStorage.setItem(REFRESH_KEY, refresh)
  localStorage.setItem(USER_KEY, JSON.stringify(user))
}

/** 只更新 access（静默刷新成功但不换 refresh 时不会走到这） */
export function saveTokens(access: string, refresh: string): void {
  localStorage.setItem(ACCESS_KEY, access)
  localStorage.setItem(REFRESH_KEY, refresh)
}

export function clearSession(): void {
  localStorage.removeItem(ACCESS_KEY)
  localStorage.removeItem(REFRESH_KEY)
  localStorage.removeItem(USER_KEY)
}

export function isAdmin(user: AuthUser | null): boolean {
  return !!user?.roles.includes('admin')
}

export function loginUrl(): string {
  return '/login'
}

export function logoutRedirect(): void {
  clearSession()
  window.location.href = loginUrl()
}
