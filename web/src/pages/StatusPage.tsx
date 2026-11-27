import { useQuery } from '@tanstack/react-query'
import { AlertTriangle, TriangleAlert } from 'lucide-react'
import { api } from '../lib/api'
import { cn, formatCount } from '../lib/utils'
import Badge from '../components/ui/Badge'
import DepTooltip, { depFailures } from '../components/DepTooltip'
import Spinner from '../components/ui/Spinner'

const CHECK_ORDER = ['llm', 'embedding', 'qdrant', 'elasticsearch', 'mineru']

function Row({
  label,
  value,
  mono = false,
}: {
  label: string
  value: string
  mono?: boolean
}) {
  return (
    <div className="flex items-baseline justify-between gap-4 border-b border-hairline px-4 py-2 last:border-b-0">
      <dt className="text-[12.5px] text-ink-2">{label}</dt>
      <dd className={cn('text-right text-[12.5px] text-ink', mono && 'tnum')}>{value}</dd>
    </div>
  )
}

export default function StatusPage() {
  const ready = useQuery({
    queryKey: ['ready'],
    queryFn: api.ready,
    refetchInterval: 30_000,
  })

  const stats = useQuery({
    queryKey: ['stats'],
    queryFn: api.stats,
    refetchInterval: 30_000,
  })

  const checks = ready.data?.checks ?? {}
  const checkNames = [
    ...CHECK_ORDER.filter((k) => k in checks),
    ...Object.keys(checks).filter((k) => !CHECK_ORDER.includes(k)),
  ]
  // disabled（如 MINERU_ENABLED=false）是中性状态：不参与异常计数
  const isBad = (v: string) => v !== 'ok' && !v.startsWith('disabled')
  const failures = checkNames.filter((k) => isBad(checks[k]))
  const errors = Object.entries(stats.data?.errors ?? {})

  return (
    <div className="h-full overflow-y-auto">
      <div className="page-shell">
        <div className="mt-2 flex flex-wrap items-center justify-end gap-3">
          {ready.data ? (
            ready.data.ready ? (
              <Badge tone="ok">全部依赖正常</Badge>
            ) : (
              <DepTooltip failures={depFailures(ready.data.checks)}>
                <Badge tone="warn" className="cursor-help">
                  <TriangleAlert className="size-3" aria-hidden />
                  {`${failures.length} 项异常`}
                </Badge>
              </DepTooltip>
            )
          ) : null}
        </div>

        {ready.isError ? (
          <p className="mt-4 rounded-lg bg-critical-soft px-3 py-2 text-[13px] text-critical">
            无法连接后端：{(ready.error as Error).message}
          </p>
        ) : null}

        <div className="page-grid mt-6 items-start">
          <section className="col-span-12 overflow-hidden rounded-lg border border-hairline bg-surface lg:col-span-6">
            <div className="flex items-center gap-2 border-b border-hairline px-4 py-3">
              <h2 className="text-[13px] font-medium text-ink">依赖探针</h2>
              {ready.isFetching && !ready.isError ? (
                <span className="ml-auto">
                  <Spinner />
                </span>
              ) : null}
            </div>
            {checkNames.length ? (
              <ul>
                {checkNames.map((name) => {
                  const value = checks[name]
                  const ok = value === 'ok'
                  const disabled = value.startsWith('disabled')
                  return (
                    <li
                      key={name}
                      className="flex items-start gap-3 border-b border-hairline px-4 py-2 last:border-b-0"
                    >
                      <span
                        aria-hidden
                        className={cn(
                          'mt-2 size-2 shrink-0 rounded-full',
                          ok ? 'bg-ok' : disabled ? 'bg-hairline-strong' : 'bg-critical',
                        )}
                      />
                      <span className="min-w-0 flex-1">
                        <span className="block text-[12.5px] font-medium text-ink">{name}</span>
                        {!ok ? (
                          <span
                            className={cn(
                              'mt-0.5 block break-words text-[11.5px] leading-relaxed',
                              disabled ? 'text-ink-3' : 'text-critical',
                            )}
                          >
                            {value}
                          </span>
                        ) : null}
                      </span>
                      <span
                        className={cn(
                          'shrink-0 text-[12px]',
                          ok || disabled ? 'text-ink-3' : 'text-critical',
                        )}
                      >
                        {ok ? 'ok' : disabled ? '未启用' : '异常'}
                      </span>
                    </li>
                  )
                })}
              </ul>
            ) : ready.isFetching ? (
              <p className="px-4 py-6 text-[13px] text-ink-3">探测中…</p>
            ) : (
              <p className="px-4 py-6 text-[13px] text-ink-3">暂无探针结果。</p>
            )}
          </section>

          <section className="col-span-12 overflow-hidden rounded-lg border border-hairline bg-surface lg:col-span-6">
            <div className="flex items-center gap-2 border-b border-hairline px-4 py-3">
              <h2 className="text-[13px] font-medium text-ink">索引规模</h2>
              {stats.isFetching && !stats.isError ? (
                <span className="ml-auto">
                  <Spinner />
                </span>
              ) : null}
            </div>
            <dl>
              <Row label="文档数" value={formatCount(stats.data?.documents)} mono />
              <Row label="分块数" value={formatCount(stats.data?.chunks)} mono />
              <Row label="向量库条目" value={formatCount(stats.data?.qdrant_points)} mono />
              <Row label="关键词库条目" value={formatCount(stats.data?.es_documents)} mono />
            </dl>

            <div className="flex items-center gap-2 border-y border-hairline px-4 py-3">
              <h2 className="text-[13px] font-medium text-ink">检索配置</h2>
            </div>
            <dl>
              <Row label="生成模型" value={stats.data?.config.llm_model ?? '—'} mono />
              <Row label="向量模型" value={stats.data?.config.embedding_model ?? '—'} mono />
              <Row label="精排模型" value={stats.data?.config.rerank_model ?? '—'} mono />
              <Row
                label="召回 → 精排"
                value={`${stats.data?.config.retrieval.retrieve_top_k ?? '—'} → ${
                  stats.data?.config.retrieval.rerank_top_k ?? '—'
                }`}
                mono
              />
              <Row label="RRF k" value={String(stats.data?.config.retrieval.rrf_k ?? '—')} mono />
              <Row
                label="权限过滤"
                value={stats.data?.config.acl_enabled ? '已启用' : '未启用'}
              />
            </dl>
          </section>
        </div>

        {errors.length > 0 ? (
          <section className="col-span-12 mt-4 overflow-hidden rounded-lg border border-critical/30 bg-critical-soft">
            <div className="flex items-center gap-2 border-b border-critical/20 px-4 py-3">
              <AlertTriangle className="size-4 text-critical" aria-hidden />
              <h2 className="text-[13px] font-medium text-critical">依赖异常</h2>
            </div>
            <ul className="px-4 py-3">
              {errors.map(([name, message]) => (
                <li key={name} className="mb-2 last:mb-0">
                  <span className="tnum text-[12.5px] font-medium text-critical">{name}</span>
                  <p className="mt-0.5 break-words text-[11.5px] leading-relaxed text-ink-2">
                    {message}
                  </p>
                </li>
              ))}
            </ul>
          </section>
        ) : null}
      </div>
    </div>
  )
}
