import { type FormEvent, useState } from 'react'
import { Link } from 'react-router-dom'
import { CircleCheck, UserPlus } from 'lucide-react'
import AuthLayout from '../components/AuthLayout'
import { authApi } from '../lib/api'

/** 自助注册页：账号创建后默认禁用，提交结果就是「待管理员审批」。 */
export default function RegisterPage() {
  const [username, setUsername] = useState('')
  const [displayName, setDisplayName] = useState('')
  const [password, setPassword] = useState('')
  const [confirm, setConfirm] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  /** 注册成功后的提示文案（含审批说明），非空时整卡切换为成功态 */
  const [done, setDone] = useState<string | null>(null)

  const canSubmit = username.trim().length >= 2 && password.length >= 8 && password === confirm && !busy

  async function onSubmit(e: FormEvent) {
    e.preventDefault()
    if (!canSubmit) return
    setError(null)
    if (password !== confirm) {
      setError('两次输入的密码不一致')
      return
    }
    setBusy(true)
    try {
      const message = await authApi.register(username.trim(), password, displayName.trim())
      setDone(message)
    } catch (err) {
      setError((err as Error).message || '注册失败')
    } finally {
      setBusy(false)
    }
  }

  const inputClass =
    'h-10 rounded-lg border border-hairline bg-canvas px-3 text-[14px] text-ink-1 outline-none transition focus:border-primary-6 focus:ring-2 focus:ring-primary-6/15'

  return (
    <AuthLayout title="申请注册" subtitle="提交后需管理员审批，审批通过方可登录">
      {done ? (
        <div className="flex flex-col items-center gap-3 text-center">
          <CircleCheck className="h-12 w-12 text-emerald-600" aria-hidden />
          <h2 className="text-lg font-semibold text-ink-1">注册成功</h2>
          <p className="text-[13px] leading-5 text-ink-2">{done}</p>
          <Link
            to="/login"
            className="mt-3 flex h-10 w-full items-center justify-center rounded-lg bg-primary-6 text-[14px] font-medium text-white transition hover:bg-primary-5"
          >
            返回登录
          </Link>
        </div>
      ) : (
        <>
          <form onSubmit={onSubmit} className="flex flex-col gap-4">
            <label className="flex flex-col gap-1.5">
              <span className="text-[13px] font-medium text-ink-2">用户名</span>
              <input
                autoFocus
                value={username}
                onChange={(e) => setUsername(e.target.value)}
                autoComplete="username"
                className={inputClass}
                placeholder="字母、数字、_.- 组合"
              />
            </label>
            <label className="flex flex-col gap-1.5">
              <span className="text-[13px] font-medium text-ink-2">姓名（选填）</span>
              <input
                value={displayName}
                onChange={(e) => setDisplayName(e.target.value)}
                className={inputClass}
                placeholder="显示在页面右上角"
              />
            </label>
            <label className="flex flex-col gap-1.5">
              <span className="text-[13px] font-medium text-ink-2">密码</span>
              <input
                type="password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                autoComplete="new-password"
                className={inputClass}
                placeholder="至少 8 位，需包含字母和数字"
              />
            </label>
            <label className="flex flex-col gap-1.5">
              <span className="text-[13px] font-medium text-ink-2">确认密码</span>
              <input
                type="password"
                value={confirm}
                onChange={(e) => setConfirm(e.target.value)}
                autoComplete="new-password"
                className={inputClass}
                placeholder="再次输入密码"
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
              disabled={!canSubmit}
              className="mt-1 flex h-10 items-center justify-center gap-2 rounded-lg bg-primary-6 text-[14px] font-medium text-white transition hover:bg-primary-5 disabled:cursor-not-allowed disabled:opacity-50"
            >
              <UserPlus className="h-4 w-4" />
              {busy ? '提交中…' : '提交注册申请'}
            </button>
          </form>

          <p className="mt-5 text-center text-xs text-ink-3">
            已有账号？{' '}
            <Link to="/login" className="font-medium text-primary-7 hover:underline">
              返回登录
            </Link>
          </p>
        </>
      )}
    </AuthLayout>
  )
}
