import { useEffect, useState } from 'react'
import type { ComponentProps } from 'react'
import { useQuery } from '@tanstack/react-query'
import { LogOut, Menu, TriangleAlert, X } from 'lucide-react'
import { NavLink, Outlet, useLocation, useNavigate } from 'react-router-dom'
import { api } from '../lib/api'
import { authApi } from '../lib/api'
import { isAdmin, type AuthUser } from '../lib/auth'
import { cn } from '../lib/utils'
import DepTooltip, { depFailures } from './DepTooltip'
import ThemeToggle from './ThemeToggle'
import ConversationList from './ConversationList'
import {
  emitConversationSelected,
  requestNewConversation,
} from '../lib/conversationBus'

const NAV = [
  { to: '/chat', label: '对话问答', icon: '/enterprise-ui/icon-chat.png', adminOnly: false },
  { to: '/documents', label: '文档管理', icon: '/enterprise-ui/icon-folder.png', adminOnly: true },
  { to: '/search', label: '检索调试', icon: '/enterprise-ui/icon-sliders.png', adminOnly: true },
  { to: '/audit', label: '审计与用户', icon: '/enterprise-ui/icon-analytics.png', adminOnly: true },
  { to: '/status', label: '服务状态', icon: '/enterprise-ui/icon-analytics.png', adminOnly: false },
]

const TITLES: Record<string, string> = {
  '/chat': '对话问答',
  '/documents': '文档管理',
  '/search': '检索调试',
  '/audit': '审计与用户',
  '/status': '服务状态',
}

function StatusStrip() {
  const { data: ready, isError: readyError, error: readyErr } = useQuery({
    queryKey: ['ready'],
    queryFn: api.ready,
    refetchInterval: 30_000,
    retry: 0,
  })

  // 演示桩的后端会带 demo 标记 —— 合成数据必须标出来，不能让人当成真实制度
  const { data: info } = useQuery({
    queryKey: ['info'],
    queryFn: api.info,
    refetchInterval: 60_000,
    retry: 0,
  })

  // /ready 要逐个探测下游依赖，可能要等一会儿 —— 这三种状态要分得清
  const state = readyError ? 'unreachable' : ready ? (ready.ready ? 'ok' : 'degraded') : 'pending'

  const dotColor =
    state === 'ok'
      ? 'bg-accent'
      : state === 'degraded'
        ? 'bg-degraded'
        : state === 'unreachable'
          ? 'bg-critical'
          : 'bg-hairline-strong'

  const failures = depFailures(ready?.checks)

  return (
    <div className="flex min-w-0 items-center gap-2.5 text-[13px] text-ink-2">
      <span className="inline-flex items-center gap-1.5">
        <span className={cn('size-1.5 shrink-0 rounded-full', dotColor)} aria-hidden />

        {state === 'degraded' ? (
          // 降级不是一句状态就完了 —— 悬浮要能看见是哪个依赖、为什么
          <DepTooltip failures={failures}>
            <span className="inline-flex cursor-help items-center gap-1 whitespace-nowrap font-medium text-degraded">
              <TriangleAlert className="size-3.5" aria-hidden />
              降级
            </span>
          </DepTooltip>
        ) : state === 'unreachable' ? (
          <DepTooltip note={`无法连接后端：${(readyErr as Error | null)?.message ?? '服务未响应'}`}>
            <span className="inline-flex cursor-help items-center gap-1 whitespace-nowrap font-medium text-critical">
              <TriangleAlert className="size-3.5" aria-hidden />
              不可达
            </span>
          </DepTooltip>
        ) : (
          <span className="whitespace-nowrap text-ink-2">
            {state === 'ok' ? '服务就绪' : '连接中'}
          </span>
        )}
      </span>

      {info?.demo ? (
        <span
          className="whitespace-nowrap rounded-full bg-degraded-soft px-2 py-[1px] text-[11px] font-medium leading-4 text-degraded"
          title="当前后端是演示桩，返回的都是合成数据"
        >
          演示数据
        </span>
      ) : null}
    </div>
  )
}

export function Logo() {
  return (
    <span
      aria-hidden
      className="grid size-11 shrink-0 place-items-center rounded-[13px] bg-gradient-to-br from-[#3d7bfd] to-[#1256d8] shadow-[0_4px_12px_rgb(22_119_255/0.4)]"
    >
      <span className="flex flex-col items-center gap-[3px]">
        <span className="block h-[3px] w-4 rounded-full bg-white/95" />
        <span className="block h-[3px] w-4 rounded-full bg-white/95" />
        <span className="block h-[3px] w-2.5 rounded-full bg-white/95" />
      </span>
    </span>
  )
}

/**
 * 侧栏内容（xl 全宽侧栏 / <lg 抽屉共用同一份内容）。
 */
function SidebarContent({
  conversation,
  user,
}: {
  conversation: ComponentProps<typeof ConversationList>
  user: AuthUser | null
}) {
  const admin = isAdmin(user)
  return (
    <>
      <div className="flex items-center gap-3 px-5 pb-6 pt-7">
        <Logo />
        <span className="text-[17px] font-semibold tracking-tight text-white">企业知识库</span>
      </div>

      <nav className="min-h-0 flex-1 overflow-y-auto" aria-label="主导航">
        <ul className="flex flex-col gap-3 px-5">
          {NAV.filter((item) => !item.adminOnly || admin).map((item) => (
            <li key={item.to}>
              <NavLink
                to={item.to}
                className={({ isActive }) =>
                  cn(
                    'flex h-[56px] items-center gap-3 whitespace-nowrap rounded-[18px] px-5 text-[15px] font-medium',
                    'transition-[background-color,color] duration-150 ease-out',
                    // 选中蓝底白字；未选中白字压暗，hover 只变色不跳动
                    isActive
                      ? 'bg-accent text-white shadow-[0_4px_14px_rgb(22_119_255/0.35)]'
                      : 'text-white/70 hover:bg-white/10 hover:text-white',
                  )
                }
              >
                {({ isActive }) => (
                  <>
                    <img
                      src={item.icon}
                      alt=""
                      aria-hidden
                      className={cn(
                        'size-6 shrink-0 object-contain',
                        // 深蓝底上统一滤白：选中纯白，未选中白 75% 与文字层级一致
                        isActive ? 'brightness-0 invert' : 'opacity-75 brightness-0 invert',
                      )}
                    />
                    {item.label}
                  </>
                )}
              </NavLink>
            </li>
          ))}
        </ul>
      </nav>

      {/* 会话区：max-h 封顶 —— 历史再多也只压缩自己，绝不挤掉上面的导航 */}
      <div className="flex max-h-[45%] min-h-0 shrink-0 flex-col border-t border-white/10 px-5 pb-6 pt-4">
        <ConversationList {...conversation} />
      </div>
    </>
  )
}

export default function AppShell({ user }: { user: AuthUser | null }) {
  const [drawerOpen, setDrawerOpen] = useState(false)
  const { pathname } = useLocation()
  const navigate = useNavigate()
  const title = TITLES[pathname] ?? '企业知识库'

  async function onLogout() {
    await authApi.logout()
    // 整页跳登录：App 重新读本地态，守卫自然接管
    window.location.href = '/login'
  }

  const conversation = {
    onNew: () => {
      navigate('/chat')
      requestNewConversation()
    },
    onSelect: (id: string) => {
      navigate('/chat')
      emitConversationSelected(id)
    },
  }

  // 路由变化时收起移动端抽屉
  useEffect(() => {
    setDrawerOpen(false)
  }, [pathname])

  // 抽屉打开时锁背景滚动（移动端 safari 橡皮筋会带着底下的页面走）
  useEffect(() => {
    document.body.style.overflow = drawerOpen ? 'hidden' : ''
    return () => {
      document.body.style.overflow = ''
    }
  }, [drawerOpen])

  return (
    <div className="flex h-dvh overflow-hidden bg-paper">
      {/* ≥xl：280px 全宽深蓝侧栏（固定深色，不随主题翻转） */}
      <aside
        className="hidden w-[280px] shrink-0 flex-col xl:flex"
        style={{ backgroundImage: 'linear-gradient(180deg, var(--nav-top) 0%, var(--nav-bottom) 100%)' }}
      >
        <SidebarContent conversation={conversation} user={user} />
      </aside>

      <div className="flex min-h-0 min-w-0 flex-1 flex-col">
        {/* 白色页头：xl 高 92px，小屏 56px */}
        <header className="flex h-14 shrink-0 items-center gap-3 border-b border-hairline bg-surface px-4 shadow-[0_2px_8px_rgb(30_64_120/0.04)] xl:h-[92px] xl:gap-5 xl:px-8">
          <button
            type="button"
            onClick={() => setDrawerOpen(true)}
            aria-label="打开导航菜单"
            className="grid size-9 shrink-0 place-items-center rounded-[10px] text-ink-2 transition-colors hover:bg-sunken xl:hidden"
          >
            <Menu className="size-5" aria-hidden />
          </button>

          <h1 className="min-w-0 flex-1 truncate text-[17px] font-semibold tracking-tight text-ink xl:text-[26px]">
            {title}
          </h1>

          <div className="flex shrink-0 items-center gap-3">
            <StatusStrip />
            <ThemeToggle />
            {/* 用户菜单：显示名 + 登出 */}
            <div className="flex items-center gap-2 border-l border-hairline pl-3">
              <span
                aria-hidden
                className="grid size-8 place-items-center rounded-full bg-primary-1 text-[13px] font-semibold text-primary-7"
              >
                {(user?.display_name || user?.username || '?').slice(0, 1)}
              </span>
              <span className="hidden text-[13px] text-ink-2 lg:block">
                {user?.display_name || user?.username}
              </span>
              <button
                type="button"
                onClick={() => void onLogout()}
                aria-label="退出登录"
                title="退出登录"
                className="grid size-9 place-items-center rounded-[10px] text-ink-3 transition-colors hover:bg-sunken hover:text-critical"
              >
                <LogOut className="size-4" aria-hidden />
              </button>
            </div>
          </div>
        </header>

        {/* min-h-0：没有它内容一长就会把整列撑破，h-dvh 约束失效 */}
        <main className="min-h-0 flex-1 overflow-hidden">
          <Outlet />
        </main>
      </div>

      {/* <xl：抽屉导航 */}
      {drawerOpen ? (
        <div className="fixed inset-0 z-50 xl:hidden">
          <div
            className="absolute inset-0 bg-ink/40 backdrop-blur-[2px]"
            onClick={() => setDrawerOpen(false)}
            aria-hidden
          />
          <aside
            className="absolute inset-y-0 left-0 flex w-[280px] max-w-[85vw] flex-col shadow-pop"
            style={{ backgroundImage: 'linear-gradient(180deg, var(--nav-top) 0%, var(--nav-bottom) 100%)' }}
            role="dialog"
            aria-label="导航菜单"
          >
            <button
              type="button"
              onClick={() => setDrawerOpen(false)}
              aria-label="关闭导航菜单"
              className="absolute right-3 top-3 grid size-9 place-items-center rounded-[10px] text-white/70 transition-colors hover:bg-white/10 hover:text-white"
            >
              <X className="size-5" aria-hidden />
            </button>
            <SidebarContent conversation={conversation} user={user} />
          </aside>
        </div>
      ) : null}
    </div>
  )
}
