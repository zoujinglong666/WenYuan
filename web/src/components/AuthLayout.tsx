import type { ReactNode } from 'react'
import { Logo } from './AppShell'

/** 左侧品牌区的三个特性点：icon 复用 enterprise-ui 3D 素材 */
const FEATURES = [
  {
    icon: '/enterprise-ui/auth-feature-fast.png',
    title: '智能问答',
    desc: '基于企业知识库秒级作答',
  },
  {
    icon: '/enterprise-ui/auth-feature-search.png',
    title: '混合检索',
    desc: '语义 + 关键词，引用可溯源',
  },
  {
    icon: '/enterprise-ui/auth-feature-book.png',
    title: '知识沉淀',
    desc: '文档统一管理持续积累',
  },
]

/**
 * 认证页共享布局：左侧深蓝品牌区（lg 以下隐藏）+ 右侧表单卡。
 * 登录 / 注册同构，保证两个页面视觉与品牌一致。
 */
export default function AuthLayout({
  title,
  subtitle,
  children,
}: {
  title: string
  subtitle: string
  children: ReactNode
}) {
  return (
    <div className="flex min-h-dvh bg-canvas">
      {/* 品牌区：与主界面侧栏同源的深蓝渐变 */}
      <aside
        className="relative hidden w-[46%] shrink-0 flex-col overflow-hidden lg:flex"
        style={{
          backgroundImage: 'linear-gradient(160deg, var(--nav-top) 0%, var(--nav-bottom) 100%)',
        }}
      >
        {/* 装饰光斑：呼应 Enterprise Blue 的径向高光 */}
        <div
          aria-hidden
          className="pointer-events-none absolute inset-0"
          style={{
            background:
              'radial-gradient(720px 420px at 20% 0%, rgba(255,255,255,0.10), transparent 60%),' +
              'radial-gradient(640px 380px at 90% 100%, rgba(22,119,255,0.28), transparent 55%)',
          }}
        />

        <div className="relative z-10 flex items-center gap-3 px-10 pt-9">
          <Logo />
          <div className="flex flex-col">
            <span className="text-[17px] font-semibold tracking-tight text-white">问源 · 企业知识库</span>
            <span className="text-xs text-white/55">WenYuan Knowledge Platform</span>
          </div>
        </div>

        {/* 主视觉：文档 + 对话气泡 + 闪电，正是产品的三件事 */}
        <div className="relative z-10 flex min-h-0 flex-1 flex-col items-center justify-center px-10">
          <img
            src="/enterprise-ui/auth-hero-chat.png"
            alt=""
            aria-hidden
            width={280}
            height={280}
            className="auth-hero-float drop-shadow-[0_24px_48px_rgba(4,20,52,0.5)]"
          />
          <p className="mt-7 max-w-[360px] text-center text-[15px] leading-6 text-white/75">
            让每一次提问都有据可循
            <br />
            检索、溯源、生成，一站完成
          </p>
        </div>

        {/* 特性点：icon 与主视觉同风格，尺寸统一 44px */}
        <ul className="relative z-10 grid grid-cols-3 gap-3 px-10">
          {FEATURES.map((f) => (
            <li key={f.title} className="flex flex-col items-center gap-1.5 text-center">
              <img src={f.icon} alt="" aria-hidden className="size-11 object-contain" />
              <span className="text-[13px] font-medium text-white">{f.title}</span>
              <span className="text-[11px] leading-4 text-white/55">{f.desc}</span>
            </li>
          ))}
        </ul>

        <footer className="relative z-10 px-10 pb-7 pt-6 text-center text-xs text-white/40">
          © 2026 问源 · 企业内部系统，请勿外传账号
        </footer>
      </aside>

      {/* 表单区：白底居中卡片 */}
      <main className="flex min-w-0 flex-1 items-center justify-center px-4 py-10">
        <div className="w-full max-w-[400px]">
          {/* lg 以下品牌区不可见，表单上方保留品牌标识 */}
          <div className="mb-7 flex flex-col items-center gap-2.5 lg:hidden">
            <div className="flex items-center gap-3">
              <Logo />
              <span className="text-[17px] font-semibold tracking-tight text-ink-1">问源 · 企业知识库</span>
            </div>
          </div>

          <div className="rounded-2xl border border-hairline bg-surface p-8 shadow-lg shadow-ink-1/5">
            <div className="mb-7 flex flex-col items-center gap-2 text-center">
              <h1 className="text-xl font-semibold text-ink-1">{title}</h1>
              <p className="text-[13px] text-ink-3">{subtitle}</p>
            </div>
            {children}
          </div>

          <p className="mt-4 text-center text-xs text-ink-3 lg:hidden">
            © 2026 问源 · 企业内部系统
          </p>
        </div>
      </main>
    </div>
  )
}
