import { useCallback, useEffect, useState } from 'react'
import { ShieldAlert, UserPlus, UserRoundX, Users } from 'lucide-react'
import { api, type AuditRow, type UserRow } from '../lib/api'

const ACTION_LABEL: Record<string, string> = {
  login: '登录',
  login_failed: '登录失败',
  login_locked: '登录锁定',
  login_disabled: '禁用账号尝试登录',
  logout: '登出',
  refresh: '刷新凭证',
  ask: '提问',
  search: '检索调试',
  view_chunk: '查看引用原文',
  upload: '上传文档',
  delete_document: '删除文档',
  create_user: '创建用户',
  register: '自助注册',
  register_limited: '注册限流',
  user_disabled: '禁用用户',
  user_enabled: '启用用户',
}

/** 管理员的审计与用户管理页。 */
export default function AuditPage() {
  const [tab, setTab] = useState<'audit' | 'users'>('audit')
  return (
    <div className="flex h-full flex-col">
      <div className="flex items-center gap-1 border-b border-hairline px-6 pt-4">
        {(
          [
            ['audit', '审计日志', <ShieldAlert key="a" className="h-4 w-4" />],
            ['users', '用户管理', <Users key="u" className="h-4 w-4" />],
          ] as const
        ).map(([key, label, icon]) => (
          <button
            key={key}
            onClick={() => setTab(key)}
            className={
              'flex items-center gap-1.5 rounded-t-lg px-4 py-2 text-[13px] font-medium transition ' +
              (tab === key
                ? 'border border-b-0 border-hairline bg-surface text-accent'
                : 'text-ink-3 hover:text-ink-2')
            }
          >
            {icon}
            {label}
          </button>
        ))}
      </div>
      {tab === 'audit' ? <AuditTable /> : <UserTable />}
    </div>
  )
}

function AuditTable() {
  const [rows, setRows] = useState<AuditRow[]>([])
  const [action, setAction] = useState('')
  const [userId, setUserId] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)

  const load = useCallback(async () => {
    setLoading(true)
    try {
      setRows(await api.audit.query({ action: action || undefined, user_id: userId.trim() || undefined }))
      setError(null)
    } catch (err) {
      setError((err as Error).message)
    } finally {
      setLoading(false)
    }
  }, [action, userId])

  useEffect(() => {
    void load()
  }, [load])

  return (
    <div className="flex-1 overflow-auto p-6">
      <div className="mb-4 flex items-center gap-3">
        <input
          value={userId}
          onChange={(e) => setUserId(e.target.value)}
          placeholder="按用户筛选（用户名或 ID）"
          className="h-9 w-64 rounded-lg border border-hairline bg-surface px-3 text-[13px] outline-none focus:border-accent"
        />
        <select
          value={action}
          onChange={(e) => setAction(e.target.value)}
          className="h-9 rounded-lg border border-hairline bg-surface px-3 text-[13px] outline-none focus:border-accent"
        >
          <option value="">全部动作</option>
          {Object.entries(ACTION_LABEL).map(([k, v]) => (
            <option key={k} value={k}>
              {v}
            </option>
          ))}
        </select>
        <button
          onClick={() => void load()}
          className="h-9 rounded-lg border border-hairline bg-surface px-4 text-[13px] text-ink-2 transition hover:border-accent hover:text-accent"
        >
          刷新
        </button>
        {loading && <span className="text-xs text-ink-3">加载中…</span>}
      </div>

      {error && <p className="text-[13px] text-critical">{error}</p>}

      <div className="overflow-hidden rounded-xl border border-hairline bg-surface">
        <table className="w-full text-left text-[13px]">
          <thead className="bg-canvas text-ink-3">
            <tr>
              <th className="px-4 py-2.5 font-medium">时间</th>
              <th className="px-4 py-2.5 font-medium">用户</th>
              <th className="px-4 py-2.5 font-medium">动作</th>
              <th className="px-4 py-2.5 font-medium">对象</th>
              <th className="px-4 py-2.5 font-medium">详情</th>
              <th className="px-4 py-2.5 font-medium">IP</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.id} className="border-t border-hairline align-top text-ink-2">
                <td className="whitespace-nowrap px-4 py-2.5 text-ink-3">{r.ts}</td>
                <td className="px-4 py-2.5">{r.username || r.user_id || '—'}</td>
                <td className="px-4 py-2.5">
                  <span className="rounded bg-accent-soft px-1.5 py-0.5 text-xs text-accent">
                    {ACTION_LABEL[r.action] ?? r.action}
                  </span>
                </td>
                <td className="max-w-40 truncate px-4 py-2.5" title={r.target}>
                  {r.target || '—'}
                </td>
                <td className="max-w-80 truncate px-4 py-2.5 text-ink-3" title={r.detail}>
                  {r.detail || '—'}
                </td>
                <td className="px-4 py-2.5 text-ink-3">{r.ip || '—'}</td>
              </tr>
            ))}
            {!loading && rows.length === 0 && (
              <tr>
                <td colSpan={6} className="px-4 py-10 text-center text-ink-3">
                  暂无审计记录
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  )
}

function UserTable() {
  const [users, setUsers] = useState<UserRow[]>([])
  const [error, setError] = useState<string | null>(null)
  const [creating, setCreating] = useState(false)
  const [form, setForm] = useState({ username: '', password: '', display_name: '', departments: '', admin: false })
  const [formError, setFormError] = useState<string | null>(null)

  const load = useCallback(async () => {
    try {
      setUsers(await api.users.list())
    } catch (err) {
      setError((err as Error).message)
    }
  }, [])

  useEffect(() => {
    void load()
  }, [load])

  async function onCreate() {
    setFormError(null)
    try {
      await api.users.create({
        username: form.username.trim(),
        password: form.password,
        display_name: form.display_name.trim() || form.username.trim(),
        departments: form.departments
          .split(',')
          .map((s) => s.trim())
          .filter(Boolean),
        roles: [form.admin ? 'admin' : 'employee'],
      })
      setCreating(false)
      setForm({ username: '', password: '', display_name: '', departments: '', admin: false })
      await load()
    } catch (err) {
      setFormError((err as Error).message)
    }
  }

  async function toggleDisabled(u: UserRow) {
    try {
      await api.users.setDisabled(u.id, !u.disabled)
      await load()
    } catch (err) {
      setError((err as Error).message)
    }
  }

  return (
    <div className="flex-1 overflow-auto p-6">
      <div className="mb-4 flex items-center justify-between">
        <span className="text-[13px] text-ink-3">共 {users.length} 个账号</span>
        <button
          onClick={() => setCreating(true)}
          className="flex h-9 items-center gap-1.5 rounded-lg bg-accent px-4 text-[13px] font-medium text-white transition hover:bg-accent-hover"
        >
          <UserPlus className="h-4 w-4" />
          新建用户
        </button>
      </div>

      {error && <p className="mb-3 text-[13px] text-critical">{error}</p>}

      {creating && (
        <div className="mb-4 rounded-xl border border-hairline bg-surface p-5">
          <div className="grid grid-cols-2 gap-4">
            <label className="flex flex-col gap-1.5">
              <span className="text-[13px] font-medium text-ink-2">用户名（字母数字._-）</span>
              <input
                value={form.username}
                onChange={(e) => setForm({ ...form, username: e.target.value })}
                className="h-9 rounded-lg border border-hairline bg-canvas px-3 text-[13px] outline-none focus:border-accent"
              />
            </label>
            <label className="flex flex-col gap-1.5">
              <span className="text-[13px] font-medium text-ink-2">显示名</span>
              <input
                value={form.display_name}
                onChange={(e) => setForm({ ...form, display_name: e.target.value })}
                className="h-9 rounded-lg border border-hairline bg-canvas px-3 text-[13px] outline-none focus:border-accent"
              />
            </label>
            <label className="flex flex-col gap-1.5">
              <span className="text-[13px] font-medium text-ink-2">初始密码（≥8 位，含字母和数字）</span>
              <input
                type="password"
                value={form.password}
                onChange={(e) => setForm({ ...form, password: e.target.value })}
                className="h-9 rounded-lg border border-hairline bg-canvas px-3 text-[13px] outline-none focus:border-accent"
              />
            </label>
            <label className="flex flex-col gap-1.5">
              <span className="text-[13px] font-medium text-ink-2">部门（逗号分隔，决定可见文档范围）</span>
              <input
                value={form.departments}
                onChange={(e) => setForm({ ...form, departments: e.target.value })}
                placeholder="如：人力资源部,行政部"
                className="h-9 rounded-lg border border-hairline bg-canvas px-3 text-[13px] outline-none focus:border-accent"
              />
            </label>
          </div>
          <label className="mt-3 flex items-center gap-2 text-[13px] text-ink-2">
            <input
              type="checkbox"
              checked={form.admin}
              onChange={(e) => setForm({ ...form, admin: e.target.checked })}
            />
            设为管理员（可管理文档、审计与用户）
          </label>
          {formError && <p className="mt-2 text-[13px] text-critical">{formError}</p>}
          <div className="mt-4 flex gap-2">
            <button
              onClick={() => void onCreate()}
              disabled={!form.username.trim() || !form.password}
              className="h-9 rounded-lg bg-accent px-4 text-[13px] font-medium text-white transition hover:bg-accent-hover disabled:opacity-50"
            >
              创建
            </button>
            <button
              onClick={() => setCreating(false)}
              className="h-9 rounded-lg border border-hairline bg-surface px-4 text-[13px] text-ink-2"
            >
              取消
            </button>
          </div>
        </div>
      )}

      <div className="overflow-hidden rounded-xl border border-hairline bg-surface">
        <table className="w-full text-left text-[13px]">
          <thead className="bg-canvas text-ink-3">
            <tr>
              <th className="px-4 py-2.5 font-medium">用户名</th>
              <th className="px-4 py-2.5 font-medium">显示名</th>
              <th className="px-4 py-2.5 font-medium">角色</th>
              <th className="px-4 py-2.5 font-medium">部门</th>
              <th className="px-4 py-2.5 font-medium">最近登录</th>
              <th className="px-4 py-2.5 font-medium">状态</th>
              <th className="px-4 py-2.5 font-medium">操作</th>
            </tr>
          </thead>
          <tbody>
            {users.map((u) => (
              <tr key={u.id} className="border-t border-hairline text-ink-2">
                <td className="px-4 py-2.5 font-medium text-ink-1">{u.username}</td>
                <td className="px-4 py-2.5">{u.display_name}</td>
                <td className="px-4 py-2.5">
                  {u.roles.includes('admin') ? '管理员' : '员工'}
                </td>
                <td className="px-4 py-2.5">{u.departments.join('、') || '—'}</td>
                <td className="px-4 py-2.5 text-ink-3">{u.last_login_at || '从未'}</td>
                <td className="px-4 py-2.5">
                  {u.disabled ? (
                    // 从未登录过的禁用账号 = 注册待审批，和「管理员手动禁用」区分开
                    <span className={u.last_login_at ? 'text-critical' : 'text-degraded'}>
                      {u.last_login_at ? '已禁用' : '待审批'}
                    </span>
                  ) : (
                    <span className="text-emerald-600">正常</span>
                  )}
                </td>
                <td className="px-4 py-2.5">
                  <button
                    onClick={() => void toggleDisabled(u)}
                    className="flex items-center gap-1 text-accent transition hover:text-accent-hover"
                  >
                    <UserRoundX className="h-4 w-4" />
                    {u.disabled ? '启用' : '禁用'}
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}
