import type { ButtonHTMLAttributes } from 'react'
import { cn } from '../../lib/utils'

type Variant = 'primary' | 'secondary' | 'ghost' | 'danger'
type Size = 'sm' | 'md'

export interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: Variant
  size?: Size
}

const VARIANTS: Record<Variant, string> = {
  // 主操作 = 品牌科技蓝（Design-System §2 --brand 三态）；墨色留给停止等中性动作
  primary: 'bg-accent text-on-accent hover:bg-accent-hover active:bg-accent-active',
  secondary: 'bg-sunken text-ink hover:bg-hairline',
  ghost: 'text-ink-2 hover:bg-sunken hover:text-ink',
  danger: 'text-critical hover:bg-critical-soft',
}

// 交互区规范（Design-System §2）：按钮高度 ≥40px，圆角档位 md=12px
const SIZES: Record<Size, string> = {
  sm: 'h-10 px-3.5 text-[13px]',
  md: 'h-10 px-4 text-sm',
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
        'inline-flex shrink-0 items-center justify-center gap-1.5 rounded-xl font-medium',
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
