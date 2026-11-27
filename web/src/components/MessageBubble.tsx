import { memo, useEffect, useRef, useState } from 'react'
import { Check, Copy } from 'lucide-react'
import { Streamdown } from 'streamdown'
import { TriangleAlert } from 'lucide-react'
import type { Citation } from '../lib/types'
import { cn } from '../lib/utils'

export interface MessageBubbleProps {
  role: 'user' | 'assistant'
  content: string
  citations?: Citation[]
  streaming?: boolean
  activeCitation?: number | null
  onCitationClick?: (index: number) => void
  /** 敏感词拒绝（spec RQ-2）—— 展示为警示条而不是普通回答 */
  refused?: boolean
}

const MARKER = /\[(\d{1,2})\]/g

/**
 * 把答案里的已知引用 [n] 预处理成锚链接，交给 markdown 渲染器后
 * 由自定义 `a` 组件还原成可点击角标 —— 这样引用角标能和 markdown
 * 排版（加粗 / 列表 / 标题）共存。未知编号保持原样显示。
 */
function withCitationLinks(md: string, citations: Citation[]): string {
  return md.replace(MARKER, (match, num) => {
    const n = Number(num)
    return citations.some((c) => c.index === n) ? `[${n}](#cite-${n})` : match
  })
}

export default memo(function MessageBubble({
  role,
  content,
  citations = [],
  streaming = false,
  activeCitation = null,
  onCitationClick,
  refused = false,
}: MessageBubbleProps) {
  if (role === 'user') {
    return (
      <div className="flex justify-end">
        <div className="max-w-[85%] rounded-[18px] bg-accent px-4 py-2 text-[14.5px] leading-relaxed whitespace-pre-wrap text-on-accent">
          {content}
        </div>
      </div>
    )
  }

  // 敏感词拒绝：琥珀警示条（走 degraded 语义色），不进 markdown 渲染、不带引用
  if (refused) {
    return (
      <div
        role="alert"
        className="flex items-start gap-2 rounded-lg border border-degraded/30 bg-degraded-soft px-3 py-3 text-[13.5px] leading-relaxed text-degraded"
      >
        <TriangleAlert className="mt-0.5 size-4 shrink-0" aria-hidden />
        <span>{content}</span>
      </div>
    )
  }

  return (
    <div className="group relative">
      {/* 悬浮复制：hover 出现，点击后短暂变成对勾；触屏没有 hover 所以常显 */}
      <CopyButton content={content} />
      <div className="text-[15px] leading-[1.75] text-ink">
        <div className="md-answer measure">
          <Streamdown
            components={{
              a: ({ href, children }) => {
                const m = typeof href === 'string' && /^#cite-(\d+)$/.exec(href)
                if (m) {
                  const n = Number(m[1])
                  const active = activeCitation === n
                  return (
                    <button
                      type="button"
                      onClick={() => onCitationClick?.(n)}
                      className={cn(
                        'tnum mx-0.5 inline-flex h-[17px] min-w-[17px] translate-y-[-1px] items-center justify-center rounded-[5px] border px-1 align-baseline text-[11px] leading-none transition-colors',
                        active
                          ? 'border-accent bg-accent text-on-accent'
                          : 'border-accent-line bg-accent-soft text-accent hover:bg-accent/20',
                      )}
                      aria-label={`查看引用 ${n}`}
                    >
                      {children}
                    </button>
                  )
                }
                return (
                  <a href={href} target="_blank" rel="noreferrer" className="text-accent underline">
                    {children}
                  </a>
                )
              },
            }}
          >
            {withCitationLinks(content, citations)}
          </Streamdown>
          {streaming ? (
            <span
              aria-hidden
              className="ml-0.5 inline-block h-[15px] w-[2px] translate-y-[2px] bg-accent align-baseline"
            />
          ) : null}
        </div>
      </div>
    </div>
  )
})
function CopyButton({ content }: { content: string }) {
  const [copied, setCopied] = useState(false)
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  // 卸载时清掉挂着的定时器，避免对旧组件 setState
  useEffect(() => () => {
    if (timerRef.current) clearTimeout(timerRef.current)
  }, [])

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(content)
      setCopied(true)
      if (timerRef.current) clearTimeout(timerRef.current)
      timerRef.current = setTimeout(() => setCopied(false), 1500)
    } catch {
      /* 剪贴板不可用（非安全上下文/权限拒绝）：静默，不弹错误打断阅读 */
    }
  }

  return (
    <button
      type="button"
      onClick={() => void copy()}
      aria-label={copied ? '已复制' : '复制回答'}
      title={copied ? '已复制' : '复制'}
      className={cn(
        'absolute right-0 top-0 z-10 grid size-8 place-items-center rounded-[8px]',
        'border border-hairline bg-surface text-ink-3 shadow-lift',
        'transition-[opacity,color,background-color] duration-150 hover:bg-sunken hover:text-accent',
        copied && 'text-accent',
        /* 触屏没有 hover：窄屏常显，桌面 hover 才出现 */
        'opacity-100 sm:opacity-0 sm:group-hover:opacity-100 sm:focus-visible:opacity-100',
      )}
    >
      {copied ? <Check className="size-3" aria-hidden /> : <Copy className="size-3" aria-hidden />}
    </button>
  )
}
