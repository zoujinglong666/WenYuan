"""Qdrant 向量存储（语义召回通道）。

ACL 语义
--------
每条 chunk 携带 ``acl_ids``（可见主体列表）。用户也携带一组主体。
**可见 = 两者有交集**。检索时把用户主体下推为 Qdrant filter 条件，
无权 chunk 根本不进入候选集 —— 绝不能"先召回再过滤"。
"""

from __future__ import annotations

import logging
from typing import Any

from qdrant_client import AsyncQdrantClient, models

from app.config import settings

logger = logging.getLogger(__name__)


class QdrantStore:
    def __init__(self, url: str | None = None, collection: str | None = None) -> None:
        self.client = AsyncQdrantClient(url=url or settings.qdrant_url)
        self.collection = collection or settings.qdrant_collection

    async def ensure_collection(self) -> None:
        exists = await self.client.collection_exists(self.collection)
        if exists:
            return
        await self.client.create_collection(
            collection_name=self.collection,
            vectors_config=models.VectorParams(
                size=settings.qdrant_vector_size,
                distance=models.Distance.COSINE,
            ),
            # 开启写入优化：小批量高频写入场景明显提速
            optimizers_config=models.OptimizersConfigDiff(default_segment_number=2),
        )
        # 关键词索引：ACL 过滤与按 doc_id 定位都依赖它
        for field, schema in (
            ("acl_ids", models.PayloadSchemaType.KEYWORD),
            ("doc_id", models.PayloadSchemaType.KEYWORD),
            ("parent_id", models.PayloadSchemaType.KEYWORD),
            ("chunk_id", models.PayloadSchemaType.KEYWORD),
        ):
            await self.client.create_payload_index(
                collection_name=self.collection,
                field_name=field,
                field_schema=schema,
                wait=True,
            )
        logger.info("已创建 Qdrant collection: %s", self.collection)

    async def upsert(
        self, chunks: list[dict[str, Any]], vectors: list[list[float]]
    ) -> None:
        if not chunks:
            return
        points = [
            models.PointStruct(
                id=chunk["chunk_id"],
                vector=vector,
                payload=chunk,
            )
            for chunk, vector in zip(chunks, vectors, strict=True)
        ]
        await self.client.upsert(
            collection_name=self.collection,
            points=points,
            wait=True,
        )

    async def delete_by_doc(self, doc_id: str) -> None:
        """文档重解析前先清干净，避免新旧 chunk 混杂。"""
        await self.client.delete(
            collection_name=self.collection,
            points_selector=models.FilterSelector(
                filter=models.Filter(
                    must=[
                        models.FieldCondition(
                            key="doc_id",
                            match=models.MatchValue(value=doc_id),
                        )
                    ]
                )
            ),
            wait=True,
        )

    async def search(
        self,
        query_vector: list[float],
        *,
        top_k: int,
        acl_ids: list[str],
        doc_ids: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        """语义召回。acl_ids 为空时直接返回空（默认拒绝策略）。"""
        if not acl_ids:
            return []

        must: list[models.Condition] = [
            models.FieldCondition(
                key="acl_ids",
                match=models.MatchAny(any=acl_ids),
            )
        ]
        if doc_ids:
            must.append(
                models.FieldCondition(
                    key="doc_id",
                    match=models.MatchAny(any=doc_ids),
                )
            )

        result = await self.client.search(
            collection_name=self.collection,
            query_vector=query_vector,
            limit=top_k,
            query_filter=models.Filter(must=must),
            with_payload=True,
        )
        return [
            {"chunk_id": p.id, "score": p.score, "payload": p.payload or {}}
            for p in result
        ]

    async def get_chunk(
        self, chunk_id: str, *, acl_ids: list[str]
    ) -> dict[str, Any] | None:
        """按 chunk_id 取原文，**带 ACL 过滤**。

        引用溯源要能回到出处，但回原文这条路不能绕过权限：
        acl_ids 为空直接返回 None（默认拒绝），
        无权访问的 chunk 与不存在等价。
        """
        if not acl_ids:
            return None

        result = await self.client.scroll(
            collection_name=self.collection,
            scroll_filter=models.Filter(
                must=[
                    models.FieldCondition(
                        key="chunk_id",
                        match=models.MatchValue(value=chunk_id),
                    ),
                    models.FieldCondition(
                        key="acl_ids",
                        match=models.MatchAny(any=acl_ids),
                    ),
                ]
            ),
            limit=1,
            with_payload=True,
        )
        points = result[0] if isinstance(result, tuple) else result.points
        if not points:
            return None
        payload = points[0].payload or {}
        return {"chunk_id": chunk_id, "payload": payload}

    async def ping(self) -> None:
        """存活探针：连不上就抛异常，由 /ready 记成失败原因。"""
        await self.client.get_collections()

    async def count(self) -> int:
        """索引里的点数（向量条数）。"""
        info = await self.client.get_collection(self.collection)
        return info.points_count or 0

    async def close(self) -> None:
        await self.client.close()
