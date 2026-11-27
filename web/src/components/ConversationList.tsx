import { useCallback, useEffect, useState } from 'react'
import { Trash2 } from 'lucide-react'
import { api } from '../lib/api'
import { loadCurrentConversationId } from '../lib/chatSession'
import { cn } from '../lib/utils'
import {
  emitConversationDeleted,
  emitConversationsChanged,
  onConversationsChanged,
} from '../lib/conversationBus'
import type { ConversationSummary } from '../lib/types'

/**
 * 侧栏底部的会话区：新对话 + 历史会话列表（切换、删除）。
 *
 * 展示归侧栏、状态归 ChatPage：当前会话 id 从 localStorage 读，
 * 列表在自己挂载时拉一次，之后靠 conversationBus 的事件刷新。
 */
export default function ConversationList({
  onNew,
  onSelect,
}: {
  onNew: () => void
  onSelect: (id: string) => void
}) {
  const [items, setItems] = useState<ConversationSummary[]>([])
  const [loading, setLoading] = useState(true)
  const [currentId, setCurrentId] = useState<string | null>(() => loadCurrentConversationId())

  const refresh = useCallback(async () => {
    try {
      setItems(await api.conversations.list())
    } catch {
      setItems([])
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    void refresh()
    // 当前会话变化（首轮发问建会话 / 恢复 / 新对话）→ 刷新高亮和列表
    return onConversationsChanged(() => {
      setCurrentId(loadCurrentConversationId())
      void refresh()
    })
  }, [refresh])

  const remove = (id: string) => {
    setItems((prev) => prev.filter((c) => c.id !== id))
    void api.conversations.remove(id).catch(() => {})
    emitConversationDeleted(id)
    emitConversationsChanged()
  }

  return (
    <div className="flex min-h-0 flex-1 flex-col gap-2">
      <button
        type="button"
        onClick={onNew}
        className="flex h-10 shrink-0 items-center gap-2 rounded-full bg-white/10 px-4 text-[14px] font-medium text-white transition-[background-color] duration-150 hover:bg-white/15"
      >
        <img
          src="/enterprise-ui/icon-new-chat.png"
          alt=""
          aria-hidden
          className="size-4 shrink-0 object-contain brightness-0 invert"
        />
        新对话
      </button>

      <p className="shrink-0 px-1 pt-1 text-[12px] font-medium tracking-wide text-white/45">
        历史会话
      </p>

      <div className="min-h-0 flex-1 overflow-y-auto" aria-label="历史会话列表">
        {loading ? (
          <p className="px-1 py-2 text-[12.5px] text-white/45">加载中…</p>
        ) : items.length === 0 ? (
          <p className="px-1 py-2 text-[12.5px] leading-relaxed text-white/45">
            还没有历史会话。
          </p>
        ) : (
          <ul className="flex flex-col gap-0.5 pr-0.5">
            {items.map((c) => {
              const active = c.id === currentId
              return (
                <li key={c.id} className="group relative">
                  <button
                    type="button"
                    onClick={() => onSelect(c.id)}
                    className={cn(
                      'block w-full rounded-[10px] px-3 py-2 pr-8 text-left transition-colors duration-150',
                      active ? 'bg-white/15' : 'hover:bg-white/8',
                    )}
                  >
                    <span
                      className={cn(
                        'block truncate text-[13px]',
                        active ? 'font-medium text-white' : 'text-white/75',
                      )}
                    >
                      {c.title}
                    </span>
                    <span className="mt-0.5 block text-[11px] text-white/40">
                      {c.message_count} 条消息 · {c.updated_at.slice(5, 16)}
                    </span>
                  </button>
                  <button
                    type="button"
                    aria-label={`删除会话：${c.title}`}
                    onClick={(e) => {
                      e.stopPropagation()
                      remove(c.id)
                    }}
                    /* 触屏没有 hover：桌面收起、窄屏常显 */
                    className="absolute right-1.5 top-1/2 -translate-y-1/2 rounded-full p-2 text-white/40 transition-[opacity,background-color,color] duration-150 hover:bg-white/10 hover:text-white focus-visible:opacity-100 sm:opacity-0 sm:group-hover:opacity-100 sm:focus-visible:opacity-100"
                  >
                    <Trash2 className="size-3" aria-hidden />
                  </button>
                </li>
              )
            })}
          </ul>
        )}
      </div>
    </div>
  )
}
