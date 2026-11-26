"""存储后端工厂：按配置返回文档登记表与对象存储。

生产默认 PostgreSQL + MinIO；``DOC_BACKEND=local`` / ``OBJECT_BACKEND=local``
切到 SQLite + 本地文件系统。接口一致，调用方无感知。
"""

from __future__ import annotations

import logging
from typing import Any

from app.config import settings

logger = logging.getLogger(__name__)

_document_repo: Any = None
_object_store: Any = None
_conversation_repo: Any = None


def get_conversation_repo() -> Any:
    """会话仓库。目前只有 local（SQLite）实现；生产切 PostgreSQL 时
    在这里按 settings 分流，路由层无感（见 docs/spec/spec.md RQ-1 非目标）。"""
    global _conversation_repo
    if _conversation_repo is None:
        from app.storage.conversation_repo import ConversationRepository

        _conversation_repo = ConversationRepository()
        logger.info("会话库后端: local（SQLite）")
    return _conversation_repo


def get_document_repo() -> Any:
    global _document_repo
    if _document_repo is None:
        if settings.doc_backend == "local":
            from app.storage.local_doc_repo import LocalDocumentRepository

            _document_repo = LocalDocumentRepository()
            logger.info("文档库后端: local（SQLite，非 PostgreSQL）")
        else:
            from app.storage.doc_repo import DocumentRepository

            _document_repo = DocumentRepository()
            logger.info("文档库后端: postgres @ %s", settings.postgres_host)
    return _document_repo


def get_object_store() -> Any:
    global _object_store
    if _object_store is None:
        if settings.object_backend == "local":
            from app.storage.local_object_store import LocalObjectStore

            _object_store = LocalObjectStore()
            logger.info("对象存储后端: local（本地目录，非 MinIO）")
        else:
            from app.storage.object_store import ObjectStore

            _object_store = ObjectStore()
            logger.info("对象存储后端: minio @ %s", settings.minio_endpoint)
    return _object_store


def reset_stores() -> None:
    global _document_repo, _object_store, _conversation_repo
    _document_repo = None
    _object_store = None
    _conversation_repo = None
