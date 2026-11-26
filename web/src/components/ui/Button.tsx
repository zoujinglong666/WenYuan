import type { ButtonHTMLAttributes } from 'react'
import { cn } from '../../lib/utils'

type Variant = 'primary' | 'secondary' | 'ghost' | 'danger'
type Size = 'sm' | 'md'

export interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: Variant
  size?: Size
}

const VARIANTS: Record<Variant, string> = {
  // 主操作用墨：黑底暖白字，朱砂只留给批注语义
  primary: 'bg-ink text-on-ink hover:bg-ink-hover',
  secondary: 'bg-sunken text-ink hover:bg-hairline',
  ghost: 'text-ink-2 hover:bg-sunken hover:text-ink',
  danger: 'text-critical hover:bg-critical-soft',
}

const SIZES: Record<Size, string> = {
  sm: 'h-8 px-3.5 text-[13px]',
  md: 'h-9 px-4 text-sm',
}

export default function Button({
  variant = 'secondary',
  size = 'md',
  className,
  type = 'button',
  ...rest
}: ButtonProps) {
  return (
    <button
      type={type}
      className={cn(
        'inline-flex shrink-0 items-center justify-center gap-1.5 rounded-[10px] font-medium',
        'transition-[background-color,color,transform] duration-150 ease-out active:scale-[0.96]',
        'disabled:pointer-events-none disabled:opacity-40',
        VARIANTS[variant],
        SIZES[size],
        className,
      )}
      {...rest}
    />
  )
}
