"""会话 REST：聊天历史的服务端存储入口。

归属校验统一走「不存在即 404」：把「别人的会话」和「没有这个会话」
返回成同一个响应，避免探知他人会话 id 是否有效（与 chunk 溯源同思路）。
契约见 docs/spec/spec.md RQ-1。

v2.0：身份一律来自 Bearer token —— user_id 不再接受客户端传参。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query

from app.api.deps import AuthedUser, get_current_user
from app.api.schemas import ConversationDetail, ConversationSummary, StoredMessage
from app.storage.factory import get_conversation_repo

router = APIRouter(prefix="/v1/conversations", tags=["conversations"])


@router.post("", response_model=ConversationSummary)
async def create_conversation(user: AuthedUser = Depends(get_current_user)) -> ConversationSummary:
    row = await get_conversation_repo().create(user_id=user.user_id)
    return ConversationSummary(**row)


@router.get("", response_model=list[ConversationSummary])
async def list_conversations(
    limit: int = Query(default=50, ge=1, le=100),
    user: AuthedUser = Depends(get_current_user),
) -> list[ConversationSummary]:
    rows = await get_conversation_repo().list(user_id=user.user_id, limit=limit)
    return [ConversationSummary(**r) for r in rows]


@router.get("/{conv_id}", response_model=ConversationDetail)
async def get_conversation(
    conv_id: str,
    user: AuthedUser = Depends(get_current_user),
) -> ConversationDetail:
    row = await get_conversation_repo().get(conv_id, user_id=user.user_id)
    if row is None:
        raise HTTPException(status_code=404, detail="会话不存在，或你无权查看")
    messages = [StoredMessage(**m) for m in row.pop("messages")]
    return ConversationDetail(**row, messages=messages)


@router.delete("/{conv_id}")
async def delete_conversation(
    conv_id: str,
    user: AuthedUser = Depends(get_current_user),
) -> dict[str, str]:
    deleted = await get_conversation_repo().delete(conv_id, user_id=user.user_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="会话不存在，或你无权删除")
    return {"status": "deleted", "conversation_id": conv_id}
