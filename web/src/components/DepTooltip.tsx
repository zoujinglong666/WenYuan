import type { ReactNode } from 'react'
import { TriangleAlert } from 'lucide-react'
import { cn } from '../lib/utils'

export interface DepFailure {
  name: string
  message: string
}

/** 从 /ready 的 checks 里挑出真正异常的依赖及其原因（disabled 是中性状态，不算异常）。 */
export function depFailures(checks: Record<string, string> = {}): DepFailure[] {
  return Object.entries(checks)
    .filter(([, v]) => v !== 'ok' && !v.startsWith('disabled'))
    .map(([name, message]) => ({ name, message }))
}

/**
 * 降级原因悬浮提示。
 *
 * 触发器由调用方给（徽章 / 状态文字），本组件负责包一层并在
 * hover / 键盘聚焦时展开原因卡片。span 可聚焦 —— 纯 hover 的
 * 提示对键盘用户不可达。
 */
export default function DepTooltip({
  failures = [],
  note,
  children,
  className,
}: {
  failures?: DepFailure[]
  /** 没有具体依赖列表时的一句话说明（如「无法连接后端」）。 */
  note?: string
  children: ReactNode
  className?: string
}) {
  const empty = failures.length === 0 && !note
  return (
    <span className={cn('group relative inline-flex', className)} tabIndex={0}>
      {children}

      <span
        role="tooltip"
        className="pointer-events-none invisible absolute left-0 top-full z-50 mt-2 w-80 max-w-[80vw] opacity-0 transition-[opacity,transform] duration-150 ease-out group-hover:visible group-hover:opacity-100 group-focus-within:visible group-focus-within:opacity-100"
      >
        <span className="block rounded-lg border border-hairline bg-surface p-3 shadow-pop">
          <span className="mb-2 flex items-center gap-1.5 text-[11px] font-medium tracking-wide text-ink-3">
            <TriangleAlert className="size-3 text-degraded" aria-hidden />
            降级原因
          </span>

          {failures.length > 0 ? (
            <ul className="space-y-2">
              {failures.map((f) => (
                <li key={f.name}>
                  <span className="block text-[12px] font-medium text-ink">{f.name}</span>
                  <span className="mt-0.5 block break-words text-[11.5px] leading-relaxed text-critical">
                    {f.message}
                  </span>
                </li>
              ))}
            </ul>
          ) : null}

          {note ? (
            <span className="block break-words text-[11.5px] leading-relaxed text-ink-2">
              {note}
            </span>
          ) : null}

          {empty ? (
            <span className="block text-[11.5px] text-ink-3">部分依赖未就绪。</span>
          ) : null}
        </span>
      </span>
    </span>
  )
}
