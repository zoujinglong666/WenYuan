import { useState } from 'react'
import { useMutation } from '@tanstack/react-query'
import { Search as SearchIcon } from 'lucide-react'
import { api } from '../lib/api'
import { identity } from '../lib/session'
import type { RetrievalTrace, SearchHit } from '../lib/types'
import { cn, formatHeading, formatMs, formatScore } from '../lib/utils'
import RetrievalRail from '../components/RetrievalRail'
import Badge from '../components/ui/Badge'
import Button from '../components/ui/Button'
import EmptyState from '../components/ui/EmptyState'
import Input from '../components/ui/Input'
import Spinner from '../components/ui/Spinner'

/** 命中来源决定点的颜色：两路都命中 = 墨（双通道是「全」的中性强色），只命中一路 = 该通道色。 */
function channelTone(sources: string[]): { bg: string; label: string } {
  const bm25 = sources.includes('bm25')
  const dense = sources.includes('dense')
  if (bm25 && dense) return { bg: 'var(--ink)', label: '双通道命中' }
  if (bm25) return { bg: 'var(--bm25)', label: '关键词命中' }
  if (dense) return { bg: 'var(--vector)', label: '语义命中' }
  return { bg: 'var(--hairline-strong)', label: '未知来源' }
}

export default function SearchPage() {
  const [query, setQuery] = useState('')
  const [topK, setTopK] = useState(10)
  const [selected, setSelected] = useState<number | null>(null)
  const [hits, setHits] = useState<SearchHit[]>([])
  const [trace, setTrace] = useState<RetrievalTrace | null>(null)

  const run = useMutation({
    mutationFn: () =>
      api.search(
        query.trim(),
        topK,
        identity.userId,
        identity.departments,
        identity.roles,
      ),
    onSuccess: (data) => {
      setHits(data.hits)
      setTrace(data.retrieval)
      setSelected(null)
    },
  })

  const active = selected != null ? hits[selected] : null

  return (
    <div className="h-full overflow-y-auto">
      <div className="mx-auto w-full max-w-5xl px-4 py-8 lg:px-10">
        <form
          className="mt-2 flex flex-wrap items-end gap-2.5"
          onSubmit={(e) => {
            e.preventDefault()
            if (query.trim()) run.mutate()
          }}
        >
          <div className="min-w-[240px] flex-1">
            <Input
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="输入查询语句"
              aria-label="查询语句"
            />
          </div>
          <div className="w-[104px]">
            <Input
              type="number"
              min={1}
              max={100}
              value={topK}
              onChange={(e) => setTopK(Math.min(100, Math.max(1, Number(e.target.value) || 1)))}
              aria-label="返回条数"
              className="tnum"
            />
          </div>
          <Button type="submit" variant="primary" disabled={!query.trim() || run.isPending}>
            {run.isPending ? <Spinner /> : <SearchIcon className="size-4" aria-hidden />}
            检索
          </Button>
        </form>

        <div className="mt-5">
          <RetrievalRail
            mode="search"
            stage={run.isPending ? 'retrieve' : trace ? 'done' : 'idle'}
            trace={trace}
          />
        </div>

        {run.isError ? (
          <p
            role="alert"
            className="mt-4 rounded-lg bg-critical-soft px-3 py-2 text-[13px] text-critical"
          >
            {(run.error as Error).message}
          </p>
        ) : null}

        {hits.length > 0 ? (
          <div
            className={cn(
              'mt-5 grid gap-5',
              // 详情面板没打开时不要留一列空白 —— 结果列表要占满整行
              active ? 'lg:grid-cols-[minmax(0,1fr)_320px]' : '',
            )}
          >
            <ul className="overflow-hidden rounded-lg border border-hairline bg-surface">
              <li className="flex flex-wrap items-center justify-between gap-x-4 gap-y-2 border-b border-hairline px-3.5 py-2">
                <span className="flex items-center gap-3 text-[11.5px] text-ink-3">
                  <span className="flex items-center gap-1.5">
                    <span
                      aria-hidden
                      className="size-2 rounded-full"
                      style={{ background: 'var(--ink)' }}
                    />
                    双通道
                  </span>
                  <span className="flex items-center gap-1.5">
                    <span
                      aria-hidden
                      className="size-2 rounded-full"
                      style={{ background: 'var(--bm25)' }}
                    />
                    关键词
                  </span>
                  <span className="flex items-center gap-1.5">
                    <span
                      aria-hidden
                      className="size-2 rounded-full"
                      style={{ background: 'var(--vector)' }}
                    />
                    语义
                  </span>
                </span>
                <span className="tnum text-[11.5px] text-ink-3">{hits.length} 条结果</span>
              </li>
              {hits.map((hit, i) => {
                const tone = channelTone(hit.sources)
                const isActive = selected === i
                const dim = selected != null && !isActive
                return (
                  <li
                    key={hit.chunk_id}
                    className={cn(
                      'border-t border-hairline transition-opacity first:border-t-0',
                      dim ? 'opacity-45' : 'opacity-100',
                    )}
                  >
                    <button
                      type="button"
                      onClick={() => setSelected(isActive ? null : i)}
                      className={cn(
                        'flex w-full items-start gap-3 px-3.5 py-3 text-left transition-colors',
                        isActive ? 'bg-accent-soft' : 'hover:bg-sunken/70',
                      )}
                    >
                      <span className="mt-1 flex h-4 w-4 shrink-0 items-center justify-center">
                        <span
                          aria-hidden
                          title={tone.label}
                          className="size-2 rounded-full"
                          style={{ background: tone.bg }}
                        />
                        {/* 命中通道不能只靠颜色 —— 读屏用户看不到点的颜色 */}
                        <span className="sr-only">{tone.label}</span>
                      </span>

                      <span className="min-w-0 flex-1">
                        <span className="flex flex-wrap items-baseline gap-x-2 gap-y-0.5">
                          <span className="tnum text-[11px] text-ink-3">
                            {String(i + 1).padStart(2, '0')}
                          </span>
                          <span className="text-[13px] font-medium text-ink">
                            {hit.filename || '未命名文档'}
                          </span>
                          {hit.heading_text ? (
                            <span className="text-[12px] text-ink-2">
                              {formatHeading(hit.heading_text)}
                            </span>
                          ) : null}
                        </span>
                        {hit.snippet ? (
                          <span className="mt-1 block line-clamp-2 text-[12px] leading-relaxed text-ink-3">
                            {hit.snippet}
                          </span>
                        ) : null}
                      </span>

                      <span className="tnum shrink-0 text-right text-[11.5px] text-ink-2">
                        <span className="block">{formatScore(hit.rerank_score)}</span>
                        <span className="block text-ink-3">rrf {formatScore(hit.rrf_score)}</span>
                      </span>
                    </button>
                  </li>
                )
              })}
            </ul>

            {active ? (
              <aside className="h-fit rounded-lg border border-hairline bg-surface p-4 lg:sticky lg:top-4">
                <h2 className="tnum truncate text-[13px] font-medium text-ink" title={active.chunk_id}>
                  {active.chunk_id}
                </h2>
                <dl className="mt-3 space-y-2 text-[12.5px]">
                  <div className="flex items-baseline justify-between gap-3">
                    <dt className="text-ink-2">精排分</dt>
                    <dd className="tnum text-ink">{formatScore(active.rerank_score)}</dd>
                  </div>
                  <div className="flex items-baseline justify-between gap-3">
                    <dt className="text-ink-2">融合分</dt>
                    <dd className="tnum text-ink">{formatScore(active.rrf_score)}</dd>
                  </div>
                  <div className="flex items-baseline justify-between gap-3">
                    <dt className="text-ink-2">来源</dt>
                    <dd className="flex gap-1">
                      {active.sources.map((s) => (
                        <Badge key={s} tone={s === 'bm25' ? 'bm25' : 'vector'} mono>
                          {s}
                        </Badge>
                      ))}
                    </dd>
                  </div>
                  {trace?.timings ? (
                    <div className="flex items-baseline justify-between gap-3">
                      <dt className="text-ink-2">召回耗时</dt>
                      <dd className="tnum text-ink">{formatMs(trace.timings.retrieve)}</dd>
                    </div>
                  ) : null}
                </dl>
                {active.snippet ? (
                  <p className="mt-3 border-t border-hairline pt-3 text-[12.5px] leading-relaxed text-ink-2">
                    {active.snippet}
                  </p>
                ) : null}
              </aside>
            ) : null}
          </div>
        ) : run.isSuccess ? (
          <EmptyState
            className="mt-5"
            icon={<SearchIcon className="size-5" aria-hidden />}
            title="没有召回到任何片段"
            description="可能是查询词太具体，或相关文档尚未同步到索引。"
          />
        ) : (
          <EmptyState
            className="mt-5"
            icon={<SearchIcon className="size-5" aria-hidden />}
            title="还没有执行检索"
            description="输入查询语句后可以看到两路通道各自的召回数、融合与精排后的结果。"
          />
        )}
      </div>
    </div>
  )
}
