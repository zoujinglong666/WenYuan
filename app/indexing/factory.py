"""索引后端工厂：按配置返回向量库与关键词库。

设计约束：**生产路径一行不改**。默认仍是 Qdrant + Elasticsearch；
只有在 ``VECTOR_BACKEND=local`` / ``KEYWORD_BACKEND=local`` 时才切到
单机离线实现。两者接口一致，调用方（管线、检索器、接口层）无感知。

为什么不直接把离线实现塞进 QdrantStore 里
------------------------------------------
因为那会让"生产用了哪个引擎"这件事变得不可读 —— 出问题时没人知道
自己跑的是真的 Qdrant 还是替身。显式的工厂 + 显式的配置项，
让"当前跑在什么上面"永远是一行日志的事。
"""

from __future__ import annotations

import logging
from typing import Any, Protocol

from app.config import settings

logger = logging.getLogger(__name__)


class VectorStoreProtocol(Protocol):
    """向量库最小接口。"""

    async def ensure_collection(self) -> None: ...
    async def upsert(self, chunks: list[dict[str, Any]], vectors: list[list[float]]) -> None: ...
    async def delete_by_doc(self, doc_id: str) -> None: ...
    async def search(
        self,
        query_vector: list[float],
        *,
        top_k: int,
        acl_ids: list[str],
        doc_ids: list[str] | None = None,
    ) -> list[dict[str, Any]]: ...
    async def count(self) -> int: ...
    async def close(self) -> None: ...


class KeywordStoreProtocol(Protocol):
    """关键词库最小接口。"""

    async def ensure_index(self) -> None: ...
    async def bulk_index(self, chunks: list[dict[str, Any]]) -> None: ...
    async def delete_by_doc(self, doc_id: str) -> None: ...
    async def refresh(self) -> None: ...
    async def search(
        self,
        query_text: str,
        *,
        top_k: int,
        acl_ids: list[str],
        doc_ids: list[str] | None = None,
    ) -> list[dict[str, Any]]: ...
    async def close(self) -> None: ...


_vector_store: Any = None
_keyword_store: Any = None


def get_vector_store() -> Any:
    """返回当前配置的向量库实例（进程内单例）。"""
    global _vector_store
    if _vector_store is None:
        if settings.vector_backend == "local":
            from app.indexing.local_vector_store import LocalVectorStore

            _vector_store = LocalVectorStore()
            logger.info("向量库后端: local（单机离线实现，非 Qdrant）")
        else:
            from app.indexing.qdrant_store import QdrantStore

            _vector_store = QdrantStore()
            logger.info("向量库后端: qdrant @ %s", settings.qdrant_url)
    return _vector_store


def get_keyword_store() -> Any:
    """返回当前配置的关键词库实例（进程内单例）。"""
    global _keyword_store
    if _keyword_store is None:
        if settings.keyword_backend == "local":
            from app.indexing.local_bm25_store import LocalBM25Store

            _keyword_store = LocalBM25Store()
            logger.info("关键词库后端: local（单机离线实现，非 Elasticsearch）")
        else:
            from app.indexing.es_store import ESStore

            _keyword_store = ESStore()
            logger.info("关键词库后端: elasticsearch @ %s", settings.elasticsearch_url)
    return _keyword_store


def reset_stores() -> None:
    """清空单例 —— 测试与压测脚本切换配置时用。"""
    global _vector_store, _keyword_store
    _vector_store = None
    _keyword_store = None
