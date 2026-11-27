import { useEffect, useRef, useState } from 'react'
import { ChevronDown, MessageSquareQuote } from 'lucide-react'
import { api } from '../lib/api'
import type { ChunkSource, Citation } from '../lib/types'
import { cn, formatHeading } from '../lib/utils'

export interface CitationListProps {
  citations: Citation[]
  activeIndex?: number | null
  onSelect?: (index: number) => void
}

export default function CitationList({
  citations,
  activeIndex = null,
  onSelect,
}: CitationListProps) {
  // 引用区整体默认收起 —— 它是佐证材料，不该和正文抢版面
  const [listOpen, setListOpen] = useState(false)
  // 展开中的引用（按 chunk_id 记，切换引用不会互相打架）
  const [openId, setOpenId] = useState<string | null>(null)
  const [sources, setSources] = useState<Record<string, ChunkSource>>({})
  const [errors, setErrors] = useState<Record<string, string>>({})
  const [loadingId, setLoadingId] = useState<string | null>(null)
  const itemRefs = useRef<Map<string, HTMLLIElement>>(new Map())

  const docCount = new Set(citations.map((c) => c.filename)).size

  // 正文里点 [n] 角标 → 自动展开引用区并滚到对应条目
  useEffect(() => {
    if (activeIndex == null) return
    setListOpen(true)
    const timer = requestAnimationFrame(() => {
      const c = citations.find((x) => x.index === activeIndex)
      itemRefs.current.get(c?.chunk_id ?? '')?.scrollIntoView({
        block: 'nearest',
        behavior: 'smooth',
      })
    })
    return () => cancelAnimationFrame(timer)
  }, [activeIndex, citations])

  if (citations.length === 0) return null

  /** 点引用 → 取原文。引用要能回到出处，而不是只把一行高亮。 */
  const toggleSource = async (c: Citation) => {
    const opening = openId !== c.chunk_id
    setOpenId(opening ? c.chunk_id : null)
    onSelect?.(c.index)

    if (!opening) return
    if (sources[c.chunk_id] || loadingId === c.chunk_id) return

    setLoadingId(c.chunk_id)
    try {
      const src = await api.chunk(c.chunk_id)
      setSources((prev) => ({ ...prev, [c.chunk_id]: src }))
    } catch (err) {
      setErrors((prev) => ({ ...prev, [c.chunk_id]: (err as Error).message }))
    } finally {
      setLoadingId(null)
    }
  }

  return (
    <div className="overflow-hidden rounded-lg border border-hairline bg-surface">
      <button
        type="button"
        onClick={() => setListOpen((v) => !v)}
        aria-expanded={listOpen}
        className="flex w-full items-center gap-2 px-3 py-2 text-left transition-colors duration-150 hover:bg-sunken/60"
      >
        <MessageSquareQuote className="size-3 shrink-0 text-ink-3" aria-hidden />
        <span className="text-[12.5px] font-medium text-ink">引用来源</span>
        <span className="tnum text-[11.5px] text-ink-3">
          {citations.length} 条 · {docCount} 个文档
        </span>
        <ChevronDown
          aria-hidden
          className={cn(
            'ml-auto size-3 text-ink-3 transition-transform duration-200 ease-out',
            listOpen && 'rotate-180',
          )}
        />
      </button>

      {/* 展开收起：grid-rows 0fr→1fr 过渡，高度平滑且不用测高 */}
      <div
        aria-hidden={!listOpen}
        className={cn(
          'grid border-t transition-[grid-template-rows,border-color] duration-250 ease-out',
          listOpen ? 'grid-rows-[1fr] border-hairline' : 'grid-rows-[0fr] border-transparent',
        )}
      >
        <div className="overflow-hidden">
          {listOpen ? (
            <ul>
              {citations.map((c) => {
                const active = activeIndex === c.index
                const open = openId === c.chunk_id
                const source = sources[c.chunk_id]
                return (
                  <li
                    key={`${c.chunk_id}-${c.index}`}
                    ref={(el) => {
                      if (el) itemRefs.current.set(c.chunk_id, el)
                      else itemRefs.current.delete(c.chunk_id)
                    }}
                    className={cn(
                      'border-t border-hairline first:border-t-0 transition-colors',
                      active ? 'bg-accent-soft' : 'hover:bg-sunken/70',
                    )}
                  >
                    <button
                      type="button"
                      onClick={() => void toggleSource(c)}
                      className="flex w-full items-center gap-2 px-3 py-2 text-left"
                      aria-current={active ? 'true' : undefined}
                      aria-expanded={open}
                    >
                      <span
                        className={cn(
                          'tnum grid size-4 shrink-0 place-items-center rounded-[5px] border text-[11px]',
                          active
                            ? 'border-accent bg-accent text-on-accent'
                            : 'border-accent-line bg-accent-soft text-accent',
                        )}
                      >
                        {c.index}
                      </span>

                      <span className="min-w-0 flex-1 truncate text-[12.5px] text-ink-2">
                        <span className="font-medium text-ink">
                          {c.filename || '未命名文档'}
                        </span>
                        {c.heading_text ? (
                          <span className="text-ink-3"> · {formatHeading(c.heading_text)}</span>
                        ) : null}
                      </span>

                      <ChevronDown
                        aria-hidden
                        className={cn(
                          'size-3 shrink-0 text-ink-3 transition-transform duration-200 ease-out',
                          open && 'rotate-180',
                        )}
                      />
                    </button>

                    <div
                      aria-hidden={!open}
                      className={cn(
                        'grid border-t transition-[grid-template-rows,border-color] duration-250 ease-out',
                        open
                          ? 'grid-rows-[1fr] border-hairline bg-sunken/60'
                          : 'grid-rows-[0fr] border-transparent',
                      )}
                    >
                      <div className="overflow-hidden">
                        {open ? (
                          <div className="px-3 py-3">
                            {loadingId === c.chunk_id ? (
                              <p className="text-[12px] text-ink-3">正在取原文…</p>
                            ) : errors[c.chunk_id] ? (
                              <p className="text-[12px] leading-relaxed text-critical">
                                取不到原文：{errors[c.chunk_id]}
                              </p>
                            ) : source ? (
                              <>
                                <p className="text-[11.5px] text-ink-3">
                                  原文片段
                                  {source.page != null ? ` · 第 ${source.page} 页` : ''}
                                  {source.filename ? ` · ${source.filename}` : ''}
                                </p>
                                <p className="mt-2 whitespace-pre-wrap text-[12.5px] leading-relaxed text-ink">
                                  {source.text}
                                </p>
                              </>
                            ) : null}
                          </div>
                        ) : null}
                      </div>
                    </div>
                  </li>
                )
              })}
            </ul>
          ) : null}
        </div>
      </div>
    </div>
  )
}
