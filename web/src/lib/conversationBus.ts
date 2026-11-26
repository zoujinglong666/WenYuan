/**
 * 会话总线：侧栏的 ConversationList 和聊天页 ChatPage 之间的轻量联动。
 *
 * 会话状态（当前 id、轮次、流式）归 ChatPage 所有；侧栏只负责展示列表和
 * 发起动作。用模块级事件而不是把状态上提到 AppShell —— 上提会迫使
 * ChatPage 的大部分流式逻辑跟着搬家，得不偿失。
 */

const changedListeners = new Set<() => void>()
const deletedListeners = new Set<(id: string) => void>()
const newListeners = new Set<() => void>()

/** 会话集合变化（新建 / 改标题）—— 列表重新拉取 */
export function emitConversationsChanged() {
  changedListeners.forEach((fn) => fn())
}
export function onConversationsChanged(fn: () => void): () => void {
  changedListeners.add(fn)
  return () => changedListeners.delete(fn)
}

/** 侧栏点选了某个历史会话 —— ChatPage 恢复它 */
const selectListeners = new Set<(id: string) => void>()
export function emitConversationSelected(id: string) {
  selectListeners.forEach((fn) => fn(id))
}
export function onConversationSelected(fn: (id: string) => void): () => void {
  selectListeners.add(fn)
  return () => selectListeners.delete(fn)
}

/** 某个会话被删除 —— ChatPage 据此判断当前会话是否需要清空 */
export function emitConversationDeleted(id: string) {
  deletedListeners.forEach((fn) => fn(id))
}
export function onConversationDeleted(fn: (id: string) => void): () => void {
  deletedListeners.add(fn)
  return () => deletedListeners.delete(fn)
}

/**
 * 「新对话」按钮在侧栏，动作却要落在 ChatPage —— 包括跨页场景
 * （在 /documents 点新对话要先跳 /chat 再重置）。挂载时序不定，
 * 用「标志位 + 订阅」双通道：已挂载走订阅，后挂载在 mount 时消费标志位。
 */
export function requestNewConversation() {
  newRequested = true
  newListeners.forEach((fn) => fn())
}
export function onNewConversationRequest(fn: () => void): () => void {
  newListeners.add(fn)
  return () => newListeners.delete(fn)
}

let newRequested = false
export function markNewConversationRequested() {
  newRequested = true
}
export function consumeNewConversationRequest(): boolean {
  const v = newRequested
  newRequested = false
  return v
}
