import { forwardRef } from 'react'
import type { InputHTMLAttributes } from 'react'
import { cn } from '../../lib/utils'

export type InputProps = InputHTMLAttributes<HTMLInputElement>

const Input = forwardRef<HTMLInputElement, InputProps>(function Input(
  { className, ...rest },
  ref,
) {
  return (
    <input
      ref={ref}
      className={cn(
        'h-10 w-full rounded-lg border border-hairline bg-surface px-3 text-sm text-ink transition-colors',
        'placeholder:text-ink-3 hover:border-hairline-strong focus:border-accent',
        'disabled:cursor-not-allowed disabled:bg-sunken disabled:text-ink-3',
        className,
      )}
      {...rest}
    />
  )
})

export default Input
