/**
 * 在途流式问答的全局状态。
 *
 * ChatPage 卸载不再中止流 —— 回答在后台继续生成并落库（spec.md RQ-1）。
 * 这里记录「当前有哪个会话还在流式」，让重挂载的页面知道：
 * 等后台流结束后要补拉一次历史，而不是显示一份缺了最后一轮的旧数据。
 */

let activeConversationId: string | null = null
const listeners = new Set<() => void>()

export function setActiveStream(conversationId: string | null): void {
  activeConversationId = conversationId
  listeners.forEach((l) => l())
}

export function getActiveStream(): string | null {
  return activeConversationId
}

export function subscribeStream(listener: () => void): () => void {
  listeners.add(listener)
  return () => listeners.delete(listener)
}
