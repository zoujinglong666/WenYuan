/**
 * 当前会话的身份。
 *
 * ⚠️ 后端目前直接信任客户端上报的 user_id / departments / roles。
 * 接入 SSO 或网关鉴权后，这三项应当由网关注入的会话信息提供，
 * 而不是浏览器里可改的输入 —— 详见 README「权限模型」一节。
 */

function splitEnv(value: string | undefined): string[] {
  return (value ?? '')
    .split(',')
    .map((s) => s.trim())
    .filter(Boolean)
}

export const identity = {
  userId: import.meta.env.VITE_USER_ID ?? 'u_demo',
  departments: splitEnv(import.meta.env.VITE_DEPARTMENTS),
  roles: splitEnv(import.meta.env.VITE_ROLES),
}
