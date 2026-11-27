import { useEffect, useState } from 'react'
import { ChevronDown, ChevronRight } from 'lucide-react'
import type { RetrievalTrace } from '../lib/types'
import { cn, formatMs } from '../lib/utils'

export type RailStage =
  | 'idle'
  | 'rewrite'
  | 'retrieve'
  | 'rerank'
  | 'compose'
  | 'generate'
  | 'done'

export interface RetrievalRailProps {
  stage: RailStage
  trace?: RetrievalTrace | null
  queries?: string[]
  /**
   * `chat` 是完整问答链路（改写 → 双通道 → 融合 → 精排 → 上下文 → 生成）；
   * `search` 只画检索段（查询 → 双通道 → 融合 → 精排）—— 检索调试页不调用模型，
   * 画上「改写 / 生成」会是假信息。
   */
  mode?: 'chat' | 'search'
  /** 用户中途叫停：已点亮的站点保留，但不再算作「已完成」。 */
  stopped?: boolean
  /** 链路出错中断：与「停止」区分开 —— 一个是用户叫停，一个是失败。 */
  failed?: boolean
  className?: string
}

/**
 * 线路几何 —— 照地铁图的画法：直线与 45° 折线，不用曲线。
 *
 * 曲线会让分叉汇流读成一个「椭圆环」；折线才读得出「一条线分成两条、
 * 两条又并成一条」。
 *
 * 两条规则同时成立：
 * 1. **线随容器缩放**：轨道横贯内容区，不缩在中间当摆件。
 *    线宽用 non-scaling-stroke 固定，站点圆点与文字是 HTML，
 *    字号不随线一起变大 —— 地图放大，站名不会变成标题。
 * 2. **窄屏压缩**：手机屏放不下七站，压缩几何把同一条线压进 350 单位，
 *    而不是把「生成」推出屏幕让人横着拖。
 */
const MAIN_Y = 78
const LANE_UP_Y = 40
const LANE_DOWN_Y = 116
const VIEW_H = 152

interface Geometry {
  viewW: number
  trunk: number
  fork: number
  knee: number
  lane: number
  laneEnd: number
  fusion: number
  rerank: number
  context: number
  generate: number
}

/** 桌面：整条横贯，七站均匀铺开 */
const WIDE: Geometry = {
  viewW: 700,
  trunk: 46,
  fork: 104,
  knee: 142,
  lane: 200,
  laneEnd: 258,
  fusion: 296,
  rerank: 410,
  context: 525,
  generate: 640,
}

/** 窄屏：同一条线压进 350 单位，间距刚好不打架 */
const COMPACT: Geometry = {
  viewW: 350,
  trunk: 22,
  fork: 58,
  knee: 96,
  lane: 110,
  laneEnd: 124,
  fusion: 162,
  rerank: 214,
  context: 264,
  generate: 314,
}

/** 检索模式：起点是「查询」，终点是「精排」—— 两端都得有站点，不能悬空 */
const SEARCH_WIDE: Geometry = {
  viewW: 700,
  trunk: 46,
  fork: 110,
  knee: 148,
  lane: 210,
  laneEnd: 272,
  fusion: 310,
  rerank: 640,
  context: 640,
  generate: 640,
}

const SEARCH_COMPACT: Geometry = {
  viewW: 350,
  trunk: 22,
  fork: 58,
  knee: 96,
  lane: 108,
  laneEnd: 122,
  fusion: 160,
  rerank: 314,
  context: 314,
  generate: 314,
}

const STAGE_ORDER: RailStage[] = [
  'idle',
  'rewrite',
  'retrieve',
  'rerank',
  'compose',
  'generate',
  'done',
]

const STAGE_LABEL: Record<RailStage, string> = {
  idle: '待命',
  rewrite: '改写',
  retrieve: '召回',
  rerank: '精排',
  compose: '组装',
  generate: '生成',
  done: '完成',
}

/** 窄屏判定：跟着视口走，轨道所在容器宽度与视口同阶 */
function useCompactLayout() {
  const [compact, setCompact] = useState(
    () => typeof window !== 'undefined' && window.matchMedia('(max-width: 639px)').matches,
  )
  useEffect(() => {
    const mq = window.matchMedia('(max-width: 639px)')
    const onChange = () => setCompact(mq.matches)
    mq.addEventListener('change', onChange)
    return () => mq.removeEventListener('change', onChange)
  }, [])
  return compact
}

export default function RetrievalRail({
  stage,
  trace,
  queries,
  mode = 'chat',
  stopped = false,
  failed = false,
  className,
}: RetrievalRailProps) {
  const [expanded, setExpanded] = useState(false)
  const compact = useCompactLayout()

  const isChat = mode === 'chat'
  const g = isChat
    ? compact
      ? COMPACT
      : WIDE
    : compact
      ? SEARCH_COMPACT
      : SEARCH_WIDE

  /**
   * 站点点亮的语义 = 「列车已到达这一站」。
   *
   * 后端 `_NODE_STAGE` 只发 rewrite / retrieve / rerank / compose 四个阶段；
   * 「生成」不是后端节点，而是**首个答案增量到达**时前端自己推进的阶段
   * （见 ChatPage）。所以：
   * - 上下文在 compose 点亮（上下文就是 compose 组装出来的）
   * - 生成在 generate 点亮（= 已经吐字了）
   *
   * 早先写成 reachContext = at('generate') 会让「上下文」永远不亮 ——
   * 站点本身没错，是映射表错了。
   */
  const at = (s: RailStage) => STAGE_ORDER.indexOf(stage) >= STAGE_ORDER.indexOf(s)
  const reachRewrite = at('rewrite')
  const reachRetrieve = at('retrieve')
  const reachFusion = at('rerank')
  const reachRerank = at('compose')
  const reachContext = at('compose')
  const reachGenerate = at('generate')

  const idle = stage === 'idle'

  const static_ = 'var(--hairline-strong)'
  const accent = (lit: boolean) => (lit ? 'var(--accent)' : static_)
  const bm25Line = (lit: boolean) => (lit ? 'var(--bm25)' : static_)
  const vectorLine = (lit: boolean) => (lit ? 'var(--vector)' : static_)

  const timings = trace?.timings ?? {}
  const hasTimings = Object.keys(timings).length > 0

  // 改写后的查询既可能单独传入，也可能只存在于 trace 里
  const effectiveQueries = queries ?? trace?.queries ?? []

  const value = (n: number | undefined | null) => (n == null ? '—' : String(n))

  /* ---------------- 线路（直线 + 45° 折线） ---------------- */

  const trunk = `M${g.trunk} ${MAIN_Y} H${g.fork}`
  const laneUp = `M${g.fork} ${MAIN_Y} L${g.knee} ${LANE_UP_Y} H${g.laneEnd}`
  const laneDown = `M${g.fork} ${MAIN_Y} L${g.knee} ${LANE_DOWN_Y} H${g.laneEnd}`
  const mergeUp = `M${g.laneEnd} ${LANE_UP_Y} L${g.fusion} ${MAIN_Y}`
  const mergeDown = `M${g.laneEnd} ${LANE_DOWN_Y} L${g.fusion} ${MAIN_Y}`
  const mainEnd = isChat ? g.generate : g.rerank
  const main = `M${g.fusion} ${MAIN_Y} H${mainEnd}`

  /* ---------------- 站点 ---------------- */

  const stations: {
    key: string
    x: number
    y: number
    valueY: number
    labelY: number
    label: string
    text: string
    color: string
    lit: boolean
    ring?: boolean
  }[] = [
    {
      key: 'entry',
      x: g.trunk,
      y: MAIN_Y,
      valueY: MAIN_Y - 16,
      labelY: MAIN_Y + 22,
      label: isChat ? '改写' : '查询',
      text: value(effectiveQueries.length || null),
      color: 'var(--accent)',
      lit: isChat ? reachRewrite : reachRetrieve,
    },
    {
      key: 'bm25',
      x: g.lane,
      y: LANE_UP_Y,
      valueY: LANE_UP_Y - 14,
      labelY: LANE_UP_Y + 20,
      label: '关键词',
      text: value(trace?.bm25_hits),
      color: 'var(--bm25)',
      lit: reachRetrieve,
    },
    {
      key: 'dense',
      x: g.lane,
      y: LANE_DOWN_Y,
      valueY: LANE_DOWN_Y - 14,
      labelY: LANE_DOWN_Y + 18,
      label: '语义',
      text: value(trace?.dense_hits),
      color: 'var(--vector)',
      lit: reachRetrieve,
    },
    {
      key: 'fusion',
      x: g.fusion,
      y: MAIN_Y,
      valueY: MAIN_Y - 16,
      labelY: MAIN_Y + 22,
      label: '融合',
      text: value(trace?.merged),
      color: 'var(--accent)',
      lit: reachFusion,
      ring: true,
    },
    {
      key: 'rerank',
      x: g.rerank,
      y: MAIN_Y,
      valueY: MAIN_Y - 16,
      labelY: MAIN_Y + 22,
      label: '精排',
      text: value(trace?.reranked),
      color: 'var(--accent)',
      lit: reachRerank,
    },
  ]

  if (isChat) {
    stations.push(
      {
        key: 'context',
        x: g.context,
        y: MAIN_Y,
        valueY: MAIN_Y - 16,
        labelY: MAIN_Y + 22,
        label: '上下文',
        text: value(trace?.context_blocks),
        color: 'var(--accent)',
        lit: reachContext,
      },
      {
        key: 'generate',
        x: g.generate,
        y: MAIN_Y,
        valueY: MAIN_Y - 16,
        labelY: MAIN_Y + 22,
        label: '生成',
        text: timings.generate != null ? formatMs(timings.generate) : '—',
        color: 'var(--accent)',
        lit: reachGenerate,
      },
    )
  }

  /**
   * 站点数字对辅助技术是不可见的（站点标注整体 aria-hidden），
   * 所以计数必须并进 SVG 的 aria-label —— 否则读屏用户只知道
   * 「走了一条线」，不知道召回了几条。
   */
  const stationSummary = stations.map((s) => `${s.label} ${s.text}`).join('，')

  const pulseKeys: Record<string, boolean> = {
    entry: stage === 'rewrite' || (stage === 'retrieve' && !isChat),
    bm25: stage === 'retrieve',
    dense: stage === 'retrieve',
    rerank: stage === 'rerank',
    context: stage === 'compose',
    generate: stage === 'generate',
  }

  // 百分比定位：HTML 标注与 SVG 线路共用同一个盒子，天然对齐
  const px = (x: number) => `${(x / g.viewW) * 100}%`
  const py = (y: number) => `${(y / VIEW_H) * 100}%`

  return (
    <section className={cn('rounded-lg border border-hairline bg-surface', className)}>
      <div className="flex items-center justify-between gap-3 border-b border-hairline px-4 py-2">
        <div className="flex min-w-0 items-center gap-2">
          <span
            aria-hidden
            className={cn(
              'size-2 shrink-0 rounded-full',
              failed
                ? 'bg-critical'
                : stopped
                  ? 'bg-hairline-strong'
                  : stage === 'done'
                    ? 'bg-accent'
                    : 'animate-pulse bg-accent',
            )}
          />
          <h2 className="text-[13px] font-medium text-ink">检索轨道</h2>
          <span className="text-[12px] text-ink-3">{STAGE_LABEL[stage]}</span>
        </div>
        <span className="tnum text-[12px] text-ink-3">
          {failed
            ? '已中断'
            : stopped
              ? '已停止'
              : idle
                ? isChat
                  ? '输入问题后点亮'
                  : '输入查询后点亮'
                : stage === 'done'
                  ? '本次检索已完成'
                  : '进行中'}
        </span>
      </div>

      <div className="px-2 py-3">
        <div className="relative w-full" style={{ aspectRatio: `${g.viewW} / ${VIEW_H}` }}>
          <svg
            viewBox={`0 0 ${g.viewW} ${VIEW_H}`}
            className="absolute inset-0 block h-full w-full"
            role="img"
            aria-label={
              isChat
                ? `检索轨道：改写 → 关键词与语义双通道 → 融合 → 精排 → 上下文 → 生成。当前阶段 ${STAGE_LABEL[stage]}。各站计数：${stationSummary}`
                : `检索轨道：查询 → 关键词与语义双通道 → 融合 → 精排。当前阶段 ${STAGE_LABEL[stage]}。各站计数：${stationSummary}`
            }
          >
            {/* 未点亮的区间：细线，只交代几何 */}
            <g fill="none" strokeLinecap="butt" strokeWidth="2" vectorEffect="non-scaling-stroke">
              <path d={trunk} stroke={static_} />
              <path d={laneUp} stroke={static_} />
              <path d={laneDown} stroke={static_} />
              <path d={mergeUp} stroke={static_} />
              <path d={mergeDown} stroke={static_} />
              <path d={main} stroke={static_} />
            </g>

            {/* 点亮的区间 */}
            <g fill="none" strokeLinecap="butt" strokeWidth="3" vectorEffect="non-scaling-stroke">
              <path
                d={trunk}
                stroke={accent(reachRewrite)}
                className={stage === 'rewrite' || (stage === 'retrieve' && !isChat) ? 'rail-flow' : undefined}
              />
              <path
                d={laneUp}
                stroke={bm25Line(reachRetrieve)}
                className={stage === 'retrieve' ? 'rail-flow' : undefined}
              />
              <path
                d={laneDown}
                stroke={vectorLine(reachRetrieve)}
                className={stage === 'retrieve' ? 'rail-flow' : undefined}
              />
              <path d={mergeUp} stroke={bm25Line(reachRetrieve)} />
              <path d={mergeDown} stroke={vectorLine(reachRetrieve)} />
              <path
                d={main}
                stroke={accent(reachFusion)}
                strokeOpacity={reachRerank ? 1 : 0.5}
                className={stage === 'rerank' ? 'rail-flow' : undefined}
              />
            </g>
          </svg>

          {/* 站点与文字：HTML 标注，字号不随线路缩放 */}
          <div aria-hidden className="pointer-events-none absolute inset-0">
            {stations.map((s) => (
              <div key={s.key}>
                {pulseKeys[s.key] ? (
                  <span
                    className="rail-halo absolute rounded-full"
                    style={{
                      left: px(s.x),
                      top: py(s.y),
                      width: 22,
                      height: 22,
                      marginLeft: -11,
                      marginTop: -11,
                      background: s.color,
                    }}
                  />
                ) : null}

                {s.ring ? (
                  <>
                    <span
                      className="absolute rounded-full border-2"
                      style={{
                        left: px(s.x),
                        top: py(s.y),
                        width: 13,
                        height: 13,
                        marginLeft: -6.5,
                        marginTop: -6.5,
                        background: 'var(--surface)',
                        borderColor: accent(s.lit),
                      }}
                    />
                    <span
                      className="absolute rounded-full"
                      style={{
                        left: px(s.x),
                        top: py(s.y),
                        width: 4,
                        height: 4,
                        marginLeft: -2,
                        marginTop: -2,
                        background: accent(s.lit),
                      }}
                    />
                  </>
                ) : (
                  <span
                    className="absolute rounded-full border-2"
                    style={{
                      left: px(s.x),
                      top: py(s.y),
                      width: 10,
                      height: 10,
                      marginLeft: -5,
                      marginTop: -5,
                      background: s.lit ? s.color : 'var(--surface)',
                      borderColor: s.lit ? s.color : static_,
                    }}
                  />
                )}

                <span
                  className={cn(
                    'tnum absolute whitespace-nowrap text-[12px] leading-none',
                    s.lit ? 'text-ink' : 'text-ink-3',
                  )}
                  style={{ left: px(s.x), top: py(s.valueY), transform: 'translate(-50%, -50%)' }}
                >
                  {s.text}
                </span>

                <span
                  className="absolute whitespace-nowrap text-[11px] leading-none text-ink-3"
                  style={{ left: px(s.x), top: py(s.labelY), transform: 'translate(-50%, -50%)' }}
                >
                  {s.label}
                </span>
              </div>
            ))}
          </div>
        </div>
      </div>

      {/* 解释层：默认收起，需要时才展开 */}
      <div className="border-t border-hairline">
        <button
          type="button"
          onClick={() => setExpanded((v) => !v)}
          className="flex w-full items-center gap-2 px-4 py-2 text-[12px] text-ink-2 transition-colors hover:text-ink"
          aria-expanded={expanded}
        >
          {expanded ? (
            <ChevronDown className="size-3" aria-hidden />
          ) : (
            <ChevronRight className="size-3" aria-hidden />
          )}
          各阶段耗时
        </button>

        {expanded ? (
          <div className="space-y-3 px-4 pb-3">
            {hasTimings ? (
              <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-2 text-[12px]">
                {(
                  [
                    ['改写', timings.rewrite],
                    ['召回', timings.retrieve],
                    ['精排', timings.rerank],
                    ['生成', timings.generate],
                    ['检索合计', timings.retrieve_total],
                  ] as [string, number | undefined][]
                )
                  .filter(([, v]) => v != null)
                  .map(([k, v]) => (
                    <div key={k} className="contents">
                      <dt className="text-ink-2">{k}</dt>
                      <dd className="tnum text-right text-ink">{formatMs(v)}</dd>
                    </div>
                  ))}
              </dl>
            ) : (
              <p className="text-[12px] text-ink-3">尚未执行检索。</p>
            )}

            {effectiveQueries.length > 0 ? (
              <div>
                <p className="mb-2 text-[12px] text-ink-2">实际检索查询</p>
                <ul className="space-y-1">
                  {effectiveQueries.map((q, i) => (
                    <li key={`${q}-${i}`} className="text-[12px] leading-relaxed text-ink">
                      <span className="tnum mr-2 text-ink-3">{i + 1}</span>
                      {q}
                    </li>
                  ))}
                </ul>
              </div>
            ) : null}
          </div>
        ) : null}
      </div>
    </section>
  )
}
