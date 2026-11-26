"""Elasticsearch 关键词存储（BM25 召回通道）。

这一路不可替代：合同编号（HT-2024-0873）、制度名称、产品型号这类
"必须字面命中"的查询，纯向量检索经常召不回或召错。

分词器用 ES 内置的 ``cjk`` analyzer（中文二元切分），不依赖 IK 等插件 —— 内网环境
拉第三方插件镜像成本高，内置方案开箱即用且对中文 BM25 足够好。

ACL 语义与 Qdrant 一致：``acl_ids`` 作为 keyword 字段进 filter 子句，前置过滤。
"""

from __future__ import annotations

import logging
from typing import Any

from elasticsearch import AsyncElasticsearch

from app.config import settings

logger = logging.getLogger(__name__)

MAPPING: dict[str, Any] = {
    "mappings": {
        "properties": {
            "chunk_id": {"type": "keyword"},
            "doc_id": {"type": "keyword"},
            "parent_id": {"type": "keyword"},
            "parent_text": {"type": "text", "analyzer": "cjk"},
            "text": {"type": "text", "analyzer": "cjk"},
            "filename": {"type": "keyword"},
            "title": {"type": "text", "analyzer": "cjk"},
            "heading_path": {"type": "keyword"},
            "heading_text": {"type": "text", "analyzer": "cjk"},
            "acl_ids": {"type": "keyword"},
            "is_table": {"type": "boolean"},
            "page": {"type": "integer"},
            "chunk_index": {"type": "integer"},
            "parent_index": {"type": "integer"},
        }
    },
    "settings": {
        "number_of_shards": 1,
        "number_of_replicas": 0,
        "refresh_interval": "5s",
    },
}


class ESStore:
    def __init__(self, url: str | None = None, index: str | None = None) -> None:
        self.client = AsyncElasticsearch(url or settings.elasticsearch_url, request_timeout=60)
        self.index = index or settings.elasticsearch_index

    async def ensure_index(self) -> None:
        if await self.client.indices.exists(index=self.index):
            return
        await self.client.indices.create(index=self.index, **MAPPING)
        logger.info("已创建 ES 索引: %s", self.index)

    async def bulk_index(self, chunks: list[dict[str, Any]]) -> None:
        if not chunks:
            return
        operations: list[dict[str, Any]] = []
        for chunk in chunks:
            operations.append({"index": {"_index": self.index, "_id": chunk["chunk_id"]}})
            operations.append(chunk)
        await self.client.bulk(operations=operations, refresh=False)

    async def delete_by_doc(self, doc_id: str) -> None:
        await self.client.delete_by_query(
            index=self.index,
            query={"term": {"doc_id": doc_id}},
            refresh=False,
            conflicts="proceed",
        )

    async def refresh(self) -> None:
        await self.client.indices.refresh(index=self.index)

    async def search(
        self,
        query_text: str,
        *,
        top_k: int,
        acl_ids: list[str],
        doc_ids: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        """BM25 召回。acl_ids 为空时直接返回空（默认拒绝策略）。"""
        if not acl_ids:
            return []

        filters: list[dict[str, Any]] = [{"terms": {"acl_ids": acl_ids}}]
        if doc_ids:
            filters.append({"terms": {"doc_id": doc_ids}})

        body = {
            "query": {
                "bool": {
                    "must": [
                        {
                            "multi_match": {
                                "query": query_text,
                                # 正文权重最高，标题与标题路径次之
                                "fields": [
                                    "text^3",
                                    "heading_text^2",
                                    "title^1.5",
                                    "parent_text",
                                ],
                                "type": "best_fields",
                                "operator": "or",
                            }
                        }
                    ],
                    "filter": filters,
                }
            },
            "size": top_k,
            "_source": True,
        }

        resp = await self.client.search(index=self.index, **body)
        hits = resp["hits"]["hits"]
        return [
            {
                "chunk_id": h["_id"],
                "score": h.get("_score") or 0.0,
                "payload": h.get("_source", {}),
            }
            for h in hits
        ]

    async def ping(self) -> None:
        """存活探针：连不上就抛异常，由 /ready 记成失败原因。"""
        await self.client.info()

    async def count(self) -> int:
        """索引里的文档数。"""
        resp = await self.client.count(index=self.index)
        return int(resp["count"])

    async def close(self) -> None:
        await self.client.close()
