import { type FormEvent, useState } from 'react'
import { Link, useLocation } from 'react-router-dom'
import { LogIn, ShieldCheck } from 'lucide-react'
import { authApi } from '../lib/api'

/** 登录页：Enterprise Blue，居中卡片 + 深蓝底纹。 */
export default function LoginPage() {
  const location = useLocation()
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  const from = (location.state as { from?: string } | null)?.from ?? '/chat'

  async function onSubmit(e: FormEvent) {
    e.preventDefault()
    if (!username.trim() || !password || busy) return
    setBusy(true)
    setError(null)
    try {
      await authApi.login(username.trim(), password)
      // 整页跳转：App 重新读取本地用户态，导航按角色过滤
      window.location.href = from
    } catch (err) {
      setError((err as Error).message || '登录失败')
      setBusy(false)
    }
  }

  return (
    <div className="relative flex min-h-dvh items-center justify-center overflow-hidden bg-canvas px-4">
      {/* 深蓝底纹：与侧栏同源的渐变，呼应主界面 */}
      <div
        aria-hidden
        className="pointer-events-none absolute inset-0"
        style={{
          background:
            'radial-gradient(1100px 520px at 18% -10%, rgba(22,119,255,0.16), transparent 60%),' +
            'radial-gradient(900px 480px at 92% 108%, rgba(9,37,84,0.35), transparent 55%)',
        }}
      />

      <div className="relative w-full max-w-[400px]">
        <div className="rounded-2xl border border-hairline bg-surface p-8 shadow-lg shadow-ink-1/5">
          <div className="mb-7 flex flex-col items-center gap-2.5">
            <div className="flex h-12 w-12 items-center justify-center rounded-xl bg-primary-6 text-white shadow-md shadow-primary-6/30">
              <ShieldCheck className="h-6 w-6" />
            </div>
            <h1 className="text-xl font-semibold text-ink-1">问源 · 企业知识库</h1>
            <p className="text-[13px] text-ink-3">登录后使用智能问答与知识检索</p>
          </div>

          <form onSubmit={onSubmit} className="flex flex-col gap-4">
            <label className="flex flex-col gap-1.5">
              <span className="text-[13px] font-medium text-ink-2">用户名</span>
              <input
                autoFocus
                value={username}
                onChange={(e) => setUsername(e.target.value)}
                autoComplete="username"
                className="h-10 rounded-lg border border-hairline bg-canvas px-3 text-[14px] text-ink-1 outline-none transition focus:border-primary-6 focus:ring-2 focus:ring-primary-6/15"
                placeholder="请输入用户名"
              />
            </label>
            <label className="flex flex-col gap-1.5">
              <span className="text-[13px] font-medium text-ink-2">密码</span>
              <input
                type="password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                autoComplete="current-password"
                className="h-10 rounded-lg border border-hairline bg-canvas px-3 text-[14px] text-ink-1 outline-none transition focus:border-primary-6 focus:ring-2 focus:ring-primary-6/15"
                placeholder="请输入密码"
              />
            </label>

            {error && (
              <p
                role="alert"
                className="rounded-lg border border-red-2 bg-red-1/60 px-3 py-2 text-[13px] text-red-6"
              >
                {error}
              </p>
            )}

            <button
              type="submit"
              disabled={busy || !username.trim() || !password}
              className="mt-1 flex h-10 items-center justify-center gap-2 rounded-lg bg-primary-6 text-[14px] font-medium text-white transition hover:bg-primary-5 disabled:cursor-not-allowed disabled:opacity-50"
            >
              <LogIn className="h-4 w-4" />
              {busy ? '登录中…' : '登 录'}
            </button>
          </form>
        </div>

        <p className="mt-4 text-center text-xs text-ink-3">
          已有账号由管理员开通 · 没有账号？{' '}
          <Link to="/register" className="font-medium text-primary-7 hover:underline">
            申请注册
          </Link>
        </p>
      </div>
    </div>
  )
}
