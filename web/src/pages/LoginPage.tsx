import { type FormEvent, useState } from 'react'
import { Link, useLocation } from 'react-router-dom'
import { LogIn } from 'lucide-react'
import AuthLayout from '../components/AuthLayout'
import Button from '../components/ui/Button'
import { authApi } from '../lib/api'

/** 登录页：左侧品牌视觉 + 右侧登录表单。 */
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
    <AuthLayout title="欢迎回来" subtitle="登录后使用智能问答与知识检索">
      <form onSubmit={onSubmit} className="flex flex-col gap-4">
        <label className="flex flex-col gap-1.5">
          <span className="text-[13px] font-medium text-ink-2">用户名</span>
          <input
            autoFocus
            value={username}
            onChange={(e) => setUsername(e.target.value)}
            autoComplete="username"
            className="h-10 rounded-lg border border-hairline bg-canvas px-3 text-[14px] text-ink-1 outline-none transition focus:border-accent focus:ring-2 focus:ring-accent/15"
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
            className="h-10 rounded-lg border border-hairline bg-canvas px-3 text-[14px] text-ink-1 outline-none transition focus:border-accent focus:ring-2 focus:ring-accent/15"
            placeholder="请输入密码"
          />
        </label>

        {error && (
          <p
            role="alert"
            className="rounded-lg border border-critical/30 bg-critical-soft px-3 py-2 text-[13px] text-critical"
          >
            {error}
          </p>
        )}

        <Button
          type="submit"
          variant="primary"
          disabled={busy || !username.trim() || !password}
          className="mt-1 h-10 w-full"
        >
          <LogIn className="h-4 w-4" />
          {busy ? '登录中…' : '登 录'}
        </Button>
      </form>

      <p className="mt-5 text-center text-xs text-ink-3">
        没有账号？{' '}
        <Link to="/register" className="font-medium text-accent hover:underline">
          申请注册
        </Link>
      </p>
    </AuthLayout>
  )
}
