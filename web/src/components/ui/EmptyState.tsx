import type { ReactNode } from 'react'
import { cn } from '../../lib/utils'

export interface EmptyStateProps {
  icon?: ReactNode
  title: string
  description?: ReactNode
  action?: ReactNode
  className?: string
}

export default function EmptyState({
  icon,
  title,
  description,
  action,
  className,
}: EmptyStateProps) {
  return (
    <div
      className={cn(
        'flex flex-col items-center justify-center gap-2.5 px-6 py-14 text-center',
        className,
      )}
    >
      {icon ? <div className="text-ink-3">{icon}</div> : null}
      <p className="font-serif text-[15px] font-semibold text-ink">{title}</p>
      {description ? (
        <div className="measure text-[13px] leading-relaxed text-ink-2">{description}</div>
      ) : null}
      {action ? <div className="mt-1">{action}</div> : null}
    </div>
  )
}
