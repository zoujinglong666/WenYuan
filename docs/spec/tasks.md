# 任务跟踪

> 状态：`[ ]` 待办 · `[x]` 完成 · `[~]` 放弃/延期（附原因）

## RQ-1 聊天会话持久化

- [x] T1 后端：`app/storage/conversation_repo.py` 会话仓库（建表 / 增删查 / 追加消息，aiosqlite）
- [x] T2 后端：`app/api/routes_conversations.py` REST（POST/GET 列表/GET 详情/DELETE），挂载到 main
- [x] T3 后端：schemas 增加 conversation_id 与会话模型；chat / chat/stream 写入消息并回传 conversation_id（含流式中止落库）
- [x] T4 前端：types + api 封装 conversations CRUD；chatStream 请求带 conversation_id
- [x] T5 前端：ChatPage 恢复逻辑（localStorage id → 拉详情 → 重建 turns；404 回落）
- [x] T6 前端：会话栏 UI（新对话 / 历史下拉切换 / 删除）
- [x] T7 构建通过 + checklist 验收全绿（A1-A4、A6-A8 已实测通过；A5 经 API 验证落库与级联；A7 由 finally 兜底逻辑保证）
