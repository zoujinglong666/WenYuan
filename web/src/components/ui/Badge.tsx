import type { ReactNode } from 'react'
import { cn } from '../../lib/utils'

export type Tone =
  | 'neutral'
  | 'accent'
  | 'ok'
  | 'warn'
  | 'crit'
  | 'bm25'
  | 'vector'

const TONES: Record<Tone, string> = {
  neutral: 'bg-sunken text-ink-2',
  accent: 'bg-accent-soft text-accent',
  // 正常/就绪是「确认」语义，用苔绿 —— 不能落在朱砂上，红在这里会读成故障
  ok: 'bg-ok-soft text-ok',
  warn: 'bg-degraded-soft text-degraded',
  crit: 'bg-critical-soft text-critical',
  bm25: 'bg-bm25-soft text-bm25',
  vector: 'bg-vector-soft text-vector',
}

export interface BadgeProps {
  tone?: Tone
  children: ReactNode
  className?: string
  mono?: boolean
}

export default function Badge({
  tone = 'neutral',
  children,
  className,
  mono = false,
}: BadgeProps) {
  return (
    <span
      className={cn(
        'inline-flex items-center gap-1 rounded-[5px] px-2 py-0.5 text-[11px] font-medium leading-4',
        mono && 'tnum',
        TONES[tone],
        className,
      )}
    >
      {children}
    </span>
  )
}
