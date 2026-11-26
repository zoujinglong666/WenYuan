import { cn } from '../../lib/utils'

export interface SpinnerProps {
  className?: string
  label?: string
}

export default function Spinner({ className, label }: SpinnerProps) {
  return (
    <span className={cn('inline-flex items-center gap-2', className)} role="status">
      <svg
        viewBox="0 0 16 16"
        className="size-4 animate-spin text-ink-3"
        aria-hidden="true"
      >
        <circle
          cx="8"
          cy="8"
          r="6"
          fill="none"
          stroke="currentColor"
          strokeWidth="1.6"
          strokeOpacity="0.25"
        />
        <path
          d="M8 2a6 6 0 0 1 6 6"
          fill="none"
          stroke="currentColor"
          strokeWidth="1.6"
          strokeLinecap="round"
        />
      </svg>
      {label ? <span className="text-[13px] text-ink-3">{label}</span> : null}
    </span>
  )
}
