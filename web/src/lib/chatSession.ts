/**
 * 当前会话 id 的本地记忆。
 *
 * 只存 id 不存消息 —— 历史的唯一事实源在服务端（spec.md RQ-1），
 * localStorage 清了也只是「忘了我聊到哪」，不会丢数据。
 */

const KEY = 'kb.current_conversation'

export function loadCurrentConversationId(): string | null {
  try {
    return localStorage.getItem(KEY)
  } catch {
    return null
  }
}

export function saveCurrentConversationId(id: string | null): void {
  try {
    if (id) localStorage.setItem(KEY, id)
    else localStorage.removeItem(KEY)
  } catch {
    /* 隐私模式等场景写不进去，仅影响「刷新恢复」，不影响使用 */
  }
}
