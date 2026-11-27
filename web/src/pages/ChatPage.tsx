import { useCallback, useEffect, useRef, useState } from 'react'
import type { KeyboardEvent } from 'react'
import { useNavigate } from 'react-router-dom'
import { Square } from 'lucide-react'
import { api, chatStream } from '../lib/api'
import { loadCurrentConversationId, saveCurrentConversationId } from '../lib/chatSession'
import { getActiveStream, setActiveStream, subscribeStream } from '../lib/chatStreamState'
import {
  consumeNewConversationRequest,
  emitConversationsChanged,
  onConversationDeleted,
  onConversationSelected,
  onNewConversationRequest,
} from '../lib/conversationBus'
import type { Citation, Message } from '../lib/types'
import CitationList from '../components/CitationList'
import MessageBubble from '../components/MessageBubble'
import type { RailStage } from '../components/RetrievalRail'
import Spinner from '../components/ui/Spinner'

interface Turn {
  id: string
  role: 'user' | 'assistant'
  content: string
  citations: Citation[]
  /** 命中敏感词被拒绝（spec RQ-2）—— 展示为警示条而不是普通回答 */
  refused?: boolean
}

/**
 * 当前高亮的引用。
 *
 * 必须是 (轮次, 序号) 二元组，不能只存序号 —— 每一轮的引用都从 [1] 开始
 * 编号，只存全局序号的话，点第二轮的 [3] 会把第一轮的 [3] 一起点亮。
 */
interface ActiveCitation {
  turnId: string
  index: number
}

/** 快捷提问：icon 用 enterprise-icons 语义最近的图标 */
const EXAMPLES = [
  { text: '近期合同的审批流程是怎样的？', icon: '/enterprise-ui/icon-folder.png' },
  { text: '新员工入职需要办理哪些手续？', icon: '/enterprise-ui/icon-user.png' },
  { text: '如何配置智慧化部署计划？', icon: '/enterprise-ui/icon-sliders.png' },
]

/** 功能卡：仅跳转到真实存在的路由（快速问答即本页，聚焦输入框） */
const FEATURE_CARDS = [
  {
    title: '快速问答',
    description: '基于企业知识库，秒级获取精准答案',
    icon: '/enterprise-ui/card-fast.png',
    route: '/chat',
  },
  {
    title: '知识沉淀',
    description: '统一管理企业文档，构建专属知识库',
    icon: '/enterprise-ui/card-knowledge.png',
    route: '/documents',
  },
  {
    title: '持续优化',
    description: '检索调优与效果评估，不断提升问答质量',
    icon: '/enterprise-ui/card-optimize.png',
    route: '/search',
  },
] as const

/** 流式过程中给用户的一句话进度，替代原来的检索轨道大图 */
const STAGE_TEXT: Record<RailStage, string> = {
  idle: '',
  rewrite: '正在理解问题',
  retrieve: '正在检索知识库',
  rerank: '正在整理依据',
  compose: '正在整理依据',
  generate: '正在生成回答',
  done: '',
}

/** 思考指示：三点错拍脉冲表示「活着」，阶段文字随进度淡入切换，不硬跳 */
function Thinking({ stage }: { stage: RailStage }) {
  const text = STAGE_TEXT[stage] || '正在检索知识库'
  return (
    <div className="flex items-center gap-2 py-0.5 text-ink-3" aria-live="polite">
      <span className="flex items-end gap-1" aria-hidden>
        {[0, 1, 2].map((i) => (
          <span key={i} className="thinking-dot" style={{ animationDelay: `${i * 0.16}s` }} />
        ))}
      </span>
      <span key={text} className="stage-text text-[13px]">
        {text}
        <span className="animate-pulse">…</span>
      </span>
    </div>
  )
}

export default function ChatPage() {
  const [turns, setTurns] = useState<Turn[]>([])
  const [input, setInput] = useState('')
  const [stage, setStage] = useState<RailStage>('idle')
  const [streaming, setStreaming] = useState(false)
  /** 正在流式输出的那一轮 —— 只有它该显示光标 / 进度。 */
  const [streamingTurnId, setStreamingTurnId] = useState<string | null>(null)
  const [aborted, setAborted] = useState(false)
  const [failed, setFailed] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [activeCitation, setActiveCitation] = useState<ActiveCitation | null>(null)

  // ---- 会话持久化（spec.md RQ-1）----
  /** 当前会话。null = 还没产生第一轮（首次发问后由服务端建会话回传 id） */
  const [conversationId, setConversationId] = useState<string | null>(null)
  const [restoring, setRestoring] = useState(false)

  const navigate = useNavigate()
  const inputRef = useRef<HTMLTextAreaElement>(null)
  const scrollRef = useRef<HTMLDivElement>(null)
  const abortRef = useRef<AbortController | null>(null)
  /** 轮次序号：比 Date.now() 可靠 —— 同一毫秒里发两轮会撞 id。 */
  const turnSeq = useRef(0)
  /** 本轮是否收到了 done 事件：没收说明流被截断了。 */
  const doneRef = useRef(false)
  const abortedRef = useRef(false)

  /** 从服务端拉取会话详情并重建消息流；404 / 网络失败静默回落新会话。 */
  const restoreConversation = useCallback(async (id: string) => {
    setRestoring(true)
    try {
      const detail = await api.conversations.get(id)
      // 存量消息用独立 id 前缀，和实时轮次（question-N）永不冲突
      setTurns(
        detail.messages.map((m) => ({
          id: `stored-${m.seq}`,
          role: m.role,
          content: m.content,
          citations: m.citations ?? [],
          // 会话表没有 refused 字段，按拒绝文案特征还原警示样式
          refused: m.role === 'assistant' && m.content.startsWith('您的提问包含不文明用语'),
        })),
      )
      setConversationId(detail.id)
      turnSeq.current = detail.messages.length
      saveCurrentConversationId(detail.id)
      setError(null)
      setFailed(false)
      setAborted(false)
      // 侧栏列表要刷新高亮
      emitConversationsChanged()
    } catch {
      // 会话没了（被删/换了账号）—— 只当没有当前会话，不弹错误
      saveCurrentConversationId(null)
      setConversationId(null)
      setTurns([])
    } finally {
      setRestoring(false)
    }
  }, [])

  const newConversation = useCallback(() => {
    abortRef.current?.abort()
    abortRef.current = null
    saveCurrentConversationId(null)
    setConversationId(null)
    setTurns([])
    setError(null)
    setFailed(false)
    setAborted(false)
    setActiveCitation(null)
    inputRef.current?.focus()
    emitConversationsChanged()
  }, [])

  // 挂载时凭 localStorage 的会话 id 恢复历史 —— 切页 / 刷新都不丢。
  // 例外：侧栏刚点过「新对话」—— 标志位优先，直接清空而不是恢复旧会话。
  // 若同一会话还在后台流式中，先拉已有的部分，流结束后由订阅回调自动补拉。
  useEffect(() => {
    if (consumeNewConversationRequest()) {
      newConversation()
      return
    }
    const saved = loadCurrentConversationId()
    if (saved && getActiveStream() === null) void restoreConversation(saved)

    return subscribeStream(() => {
      // 后台流式结束 → 如果最新会话和当前显示的不一致，补拉一次
      if (getActiveStream() !== null) return
      const latest = loadCurrentConversationId()
      if (latest && latest !== conversationId) void restoreConversation(latest)
    })
  }, [restoreConversation, conversationId, newConversation])

  // 侧栏动作落到这里：点选历史会话 → 恢复；点「新对话」→ 清空
  useEffect(() => {
    return onConversationSelected((id) => void restoreConversation(id))
  }, [restoreConversation])

  useEffect(() => {
    return onNewConversationRequest(() => {
      newConversation()
      // 挂载场景标志位由 mount 分支消费；这里消费掉防止下次挂载误清
      consumeNewConversationRequest()
    })
  }, [newConversation])

  // 当前会话在侧栏被删除 → 本页同步清空
  useEffect(() => {
    return onConversationDeleted((id) => {
      if (id === conversationId) newConversation()
    })
  }, [conversationId, newConversation])

  useEffect(() => {
    // 空态不要滚动 —— 否则会把欢迎语顶出视口
    if (turns.length === 0) return
    const el = scrollRef.current
    if (el) el.scrollTop = el.scrollHeight
  }, [turns])

  // 输入框随内容自动长高，封顶后内部滚动
  useEffect(() => {
    const el = inputRef.current
    if (!el) return
    el.style.height = 'auto'
    el.style.height = `${Math.min(el.scrollHeight, 160)}px`
  }, [input])

  // 离开页面**不中止**在途请求（企业级行为）：流在后台继续生成并落库，
  // 卸载后 React 的 setState 是静默 no-op，无泄漏；done 回调里的
  // localStorage 写入是全局副作用，照常执行。切回来时自动补拉完整历史。
  // 主动中止只通过「停止生成」按钮（stop），那同样会落库已生成部分。
  useEffect(() => {
    return () => {
      abortRef.current = null
    }
  }, [])

  const send = useCallback(
    async (raw: string) => {
      const question = raw.trim()
      if (!question || streaming) return

      const history: Message[] = turns.map((t) => ({
        role: t.role,
        content: t.content,
      }))

      turnSeq.current += 1
      const seq = turnSeq.current
      const answerId = `answer-${seq}`

      setTurns((prev) => [
        ...prev,
        { id: `question-${seq}`, role: 'user', content: question, citations: [] },
        { id: answerId, role: 'assistant', content: '', citations: [] },
      ])
      setInput('')
      setError(null)
      setActiveCitation(null)
      setAborted(false)
      setFailed(false)
      setStage('rewrite')
      setStreaming(true)
      setStreamingTurnId(answerId)

      doneRef.current = false
      abortedRef.current = false
      // 标记在途流（哪个会话正在生成）。切页/刷新时恢复逻辑据此判断要不要等流结束再补拉
      setActiveStream(conversationId ?? 'pending')

      const controller = new AbortController()
      abortRef.current = controller

      try {
        await chatStream(
          {
            question,
            history,
            doc_ids: null,
            conversation_id: conversationId,
          },
          (event) => {
            switch (event.type) {
              case 'trace':
                setStage(event.stage as RailStage)
                break
              case 'delta':
                // 后端的阶段只到 compose（检索链路），生成不是一个节点。
                // 首个答案增量到达 = 真的开始生成了。
                setStage((s) => (s === 'generate' || s === 'done' ? s : 'generate'))
                setTurns((prev) =>
                  prev.map((t) =>
                    t.id === answerId ? { ...t, content: t.content + event.text } : t,
                  ),
                )
                break
              case 'citations':
                setTurns((prev) =>
                  prev.map((t) =>
                    t.id === answerId ? { ...t, citations: event.citations } : t,
                  ),
                )
                break
              case 'refused':
                // 敏感词拒绝（spec RQ-2）：后端不发 delta，直接给完整提示文案
                doneRef.current = true
                setStage('done')
                setTurns((prev) =>
                  prev.map((t) =>
                    t.id === answerId ? { ...t, content: event.message, refused: true } : t,
                  ),
                )
                break
              case 'done':
                doneRef.current = true
                setStage('done')
                if (event.refused) {
                  setTurns((prev) =>
                    prev.map((t) =>
                      t.id === answerId
                        ? { ...t, content: event.answer ?? t.content, refused: true }
                        : t,
                    ),
                  )
                }
                // 首轮发问时服务端会新建会话并回传 id —— 立即记住它，
                // 之后切页 / 刷新就能凭它恢复
                if (event.conversation_id) {
                  setConversationId(event.conversation_id)
                  saveCurrentConversationId(event.conversation_id)
                  // 首轮的标题就是问题 —— 侧栏列表加一条
                  emitConversationsChanged()
                }
                break
              case 'error':
                doneRef.current = true
                setError(event.message)
                setFailed(true)
                break
            }
          },
          controller.signal,
        )
      } catch (err) {
        const e = err as Error
        if (e.name !== 'AbortError') {
          setError(e.message)
          setFailed(true)
        }
      } finally {
        setStreaming(false)
        setStreamingTurnId(null)
        setStage('idle')
        abortRef.current = null
        // 流结束了却没收到 done —— 连接被截断。
        if (!doneRef.current && !abortedRef.current) setFailed(true)
        // 无论正常结束还是被截断：后台流已收尾（后端已落库），通知恢复逻辑可以补拉
        setActiveStream(null)
      }
    },
    [turns, streaming, conversationId],
  )

  const stop = () => {
    abortRef.current?.abort()
    abortRef.current = null
    abortedRef.current = true
    setStreaming(false)
    setAborted(true)
  }

  const handleKeyDown = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) {
      e.preventDefault()
      void send(input)
    }
  }

  const welcome = turns.length === 0 && !restoring

  /** 胶囊输入框：欢迎页大号（hero），会话中紧凑（dock 固定底部） */
  const composer = (variant: 'hero' | 'dock') => (
    <form
      onSubmit={(e) => {
        e.preventDefault()
        void send(input)
      }}
    >
      <div
        className={
          variant === 'hero'
            ? 'flex min-h-[96px] items-center gap-3 rounded-full border-[1.5px] border-accent-line bg-surface py-3 pl-8 pr-3 shadow-input transition-colors focus-within:border-accent'
            : 'flex items-end gap-2 rounded-full border border-hairline bg-surface p-2 pl-4 shadow-lift transition-colors focus-within:border-accent-line'
        }
      >
        <textarea
          ref={inputRef}
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={handleKeyDown}
          rows={1}
          aria-label="输入问题"
          placeholder="输入问题，获取企业知识库的答案..."
          /* ≥16px：小于它 iOS 聚焦输入框时会自动放大页面 */
          className={
            variant === 'hero'
              ? 'max-h-40 min-h-[44px] flex-1 resize-none bg-transparent py-2 text-[16px] leading-relaxed text-ink placeholder:text-ink-3 focus-visible:outline-none lg:text-[15px]'
              : 'max-h-40 flex-1 resize-none bg-transparent py-2 text-[16px] leading-relaxed text-ink placeholder:text-ink-3 focus-visible:outline-none lg:text-[14.5px]'
          }
        />
        {streaming ? (
          <button
            type="button"
            onClick={stop}
            aria-label="停止生成"
            className="grid size-10 shrink-0 place-items-center rounded-full bg-ink text-paper transition-[background-color,transform] duration-150 ease-out hover:opacity-85 active:scale-[0.92]"
          >
            <Square className="size-3 fill-current" aria-hidden />
          </button>
        ) : (
          <button
            type="submit"
            disabled={!input.trim()}
            aria-label="发送"
            className="grid size-10 shrink-0 place-items-center rounded-full bg-accent text-on-accent transition-[background-color,transform,opacity] duration-150 ease-out hover:bg-accent-hover active:bg-accent-active active:scale-[0.92] disabled:opacity-40"
          >
            <img
              src="/enterprise-ui/icon-send.png"
              alt=""
              aria-hidden
              className="size-4 object-contain brightness-0 invert"
            />
          </button>
        )}
      </div>
    </form>
  )

  return (
    <div className="flex h-full flex-col">
      {welcome ? (
        /* ---- 欢迎布局：Hero + 胶囊输入框 + 快捷提问 + 功能卡 ---- */
        <div className="min-h-0 flex-1 overflow-y-auto">
          <div className="mx-auto flex min-h-full w-full max-w-[1360px] flex-col justify-center px-6 py-10 xl:px-10">
            {/* Hero：左标题右主视觉 */}
            <div className="grid items-center gap-8 lg:grid-cols-[minmax(0,1fr)_auto]">
              <div className="min-w-0">
                <h2 className="text-[clamp(32px,3vw,58px)] font-bold leading-[1.18] tracking-tight text-ink xl:whitespace-nowrap">
                  智能问答 · <span className="text-accent">企业知识随时可用</span>
                </h2>
                <p className="mt-3 text-[clamp(14px,1vw,19px)] text-ink-2 xl:mt-4">
                  基于企业知识库的智能问答，快速获取准确、可信的专业信息
                </p>
              </div>
              <img
                src="/enterprise-ui/hero-search.png"
                alt=""
                aria-hidden
                className="hidden w-[360px] shrink-0 object-contain lg:block 2xl:w-[440px]"
              />
            </div>

            <div className="mt-6 2xl:mt-8">{composer('hero')}</div>

            {/* 快捷提问：点击填入输入框，不自动发送 */}
            <div className="mt-6 flex flex-wrap items-center gap-3 2xl:mt-8">
              <span className="text-[14px] font-medium text-ink-2">您可以尝试提问：</span>
              {EXAMPLES.map((q) => (
                <button
                  key={q.text}
                  type="button"
                  onClick={() => {
                    setInput(q.text)
                    inputRef.current?.focus()
                  }}
                  className="inline-flex items-center gap-2 rounded-full border border-hairline bg-surface px-4 py-2 text-[13.5px] text-ink-2 shadow-card transition-[border-color,color,transform] duration-150 ease-out hover:border-accent-line hover:text-accent active:scale-[0.97]"
                >
                  <img src={q.icon} alt="" aria-hidden className="size-4 object-contain" />
                  {q.text}
                </button>
              ))}
            </div>

            {/* 功能卡：仅真实路由可点击 */}
            <div className="mt-8 grid gap-4 sm:grid-cols-2 lg:grid-cols-3 2xl:mt-10 2xl:gap-8">
              {FEATURE_CARDS.map((card) => (
                <button
                  key={card.title}
                  type="button"
                  onClick={() => {
                    if (card.route === '/chat') {
                      inputRef.current?.focus()
                    } else {
                      navigate(card.route)
                    }
                  }}
                  className="group flex items-center gap-4 rounded-[24px] border border-hairline bg-surface p-4 text-left shadow-card transition-[transform,box-shadow] duration-200 ease-out hover:-translate-y-0.5 hover:shadow-lift xl:gap-4 xl:p-6"
                >
                  <img
                    src={card.icon}
                    alt=""
                    aria-hidden
                    className="size-16 shrink-0 object-contain 2xl:size-16"
                  />
                  <span className="min-w-0">
                    <span className="block text-[17px] font-semibold text-ink 2xl:text-[19px]">
                      {card.title}
                    </span>
                    <span className="mt-1 block text-[13px] leading-relaxed text-ink-2 2xl:text-[14px]">
                      {card.description}
                    </span>
                  </span>
                </button>
              ))}
            </div>
          </div>
        </div>
      ) : (
        /* ---- 会话布局：消息区滚动 + 输入区固定底部 ---- */
        <>
          {/* 会话工具条：新对话常驻可见（侧栏窄屏收进抽屉后，这里仍是入口） */}
          <div className="flex h-10 shrink-0 items-center justify-end border-b border-hairline px-4 lg:px-6">
            <button
              type="button"
              onClick={newConversation}
              className="inline-flex items-center gap-2 rounded-full px-3 py-2 text-[13px] text-ink-2 transition-[background-color,color,transform] duration-150 ease-out hover:bg-sunken hover:text-ink active:scale-[0.97]"
            >
              <img
                src="/enterprise-ui/icon-new-chat.png"
                alt=""
                aria-hidden
                className="size-4 object-contain"
              />
              新对话
            </button>
          </div>

          <div
            ref={scrollRef}
            className="min-h-0 flex-1 overflow-y-auto overscroll-contain"
            role="log"
            aria-live="polite"
            aria-label="对话记录"
            tabIndex={0}
          >
            <div className="mx-auto w-full max-w-3xl px-4 py-6 lg:px-6">
              {restoring ? (
                <div className="flex min-h-full items-center justify-center">
                  <p className="flex items-center gap-2 text-[13px] text-ink-3">
                    <Spinner />
                    正在载入会话…
                  </p>
                </div>
              ) : (
                <div className="space-y-6">
                  {turns.map((t) => {
                    // 高亮只在本轮内生效 —— 别的轮的 [3] 不该跟着亮
                    const turnActive =
                      activeCitation?.turnId === t.id ? activeCitation.index : null

                    if (t.role === 'user') {
                      return <MessageBubble key={t.id} role="user" content={t.content} />
                    }

                    return (
                      <div key={t.id} className="space-y-3">
                        {t.content ? (
                          <MessageBubble
                            role="assistant"
                            content={t.content}
                            citations={t.citations}
                            streaming={streamingTurnId === t.id}
                            activeCitation={turnActive}
                            refused={t.refused}
                            onCitationClick={(index) =>
                              setActiveCitation({ turnId: t.id, index })
                            }
                          />
                        ) : streamingTurnId === t.id ? (
                          <Thinking stage={stage} />
                        ) : (
                          // 空轮也要有交代 —— 什么都不渲染等于「这条回答凭空消失」
                          <p className="text-[13px] text-ink-3">本轮未产出内容。</p>
                        )}
                        {t.citations.length > 0 ? (
                          <CitationList
                            citations={t.citations}
                            activeIndex={turnActive}
                            onSelect={(index) => setActiveCitation({ turnId: t.id, index })}
                          />
                        ) : null}
                      </div>
                    )
                  })}
                </div>
              )}
            </div>
          </div>

          {/* pb 安全区：iOS Home 指示条不能压住输入框 */}
          <div className="shrink-0 bg-paper px-4 pb-[max(0.75rem,env(safe-area-inset-bottom))] lg:px-6">
            <div className="mx-auto w-full max-w-3xl">
              {error ? (
                <p
                  role="alert"
                  className="mb-2 rounded-[12px] bg-critical-soft px-3 py-2 text-[13px] text-critical"
                >
                  {error}
                </p>
              ) : null}

              {aborted ? (
                <p className="mb-2 text-center text-[12px] text-ink-3">已停止生成</p>
              ) : failed && !streaming ? (
                <p className="mb-2 text-center text-[12px] text-ink-3">回答生成中断，请重试</p>
              ) : null}

              {composer('dock')}
            </div>
          </div>
        </>
      )}
    </div>
  )
}
