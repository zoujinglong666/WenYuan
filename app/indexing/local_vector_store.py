"""本地向量存储（numpy 暴力余弦检索）—— 没有 Qdrant 时的离线替身。

为什么需要它
------------
内网交付常常是「一台机器 + 一份手册」：没有容器编排、没有 GPU，
但检索链路必须真的能跑起来、能被压测。本模块用 numpy 做暴力余弦检索，
接口与 :class:`~app.indexing.qdrant_store.QdrantStore` 完全一致，
通过 ``VECTOR_BACKEND=local`` 启用。

生产仍然是 Qdrant（可水平扩展、可持久化到磁盘集群）。
本实现的定位是**单进程内网落地与压测**，不是分布式向量库。

数据落在 ``LOCAL_STORE_DIR`` 下：
    vectors.npy   float32 [N, dim]，行序与 chunks.jsonl 一致
    chunks.jsonl  每行一个 chunk payload
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import numpy as np

from app.config import settings

logger = logging.getLogger(__name__)


class LocalVectorStore:
    """进程内向量索引。线程/协程共用一份只读快照，写入时整体重建。"""

    def __init__(self, store_dir: str | None = None) -> None:
        self.dir = Path(store_dir or settings.local_store_dir)
        self.vectors_path = self.dir / "vectors.npy"
        self.chunks_path = self.dir / "chunks.jsonl"

        self._vectors: np.ndarray | None = None
        self._payloads: list[dict[str, Any]] = []
        self._loaded = False

    # ---------------- 持久化 ----------------

    def _load(self) -> None:
        if self._loaded:
            return
        self._loaded = True
        if not self.chunks_path.exists() or not self.vectors_path.exists():
            self._vectors = np.zeros((0, settings.embedding_dim), dtype=np.float32)
            self._payloads = []
            return
        payloads: list[dict[str, Any]] = []
        with self.chunks_path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    payloads.append(json.loads(line))
        vectors = np.load(self.vectors_path)
        self._payloads = payloads
        self._vectors = vectors.astype(np.float32, copy=False)
        logger.info("本地向量库已加载: %d 条", len(payloads))

    def _persist(self) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        vectors = self._vectors if self._vectors is not None else np.zeros((0, 1), np.float32)
        np.save(self.vectors_path, vectors)
        with self.chunks_path.open("w", encoding="utf-8") as fh:
            for payload in self._payloads:
                fh.write(json.dumps(payload, ensure_ascii=False) + "\n")

    # ---------------- 写 ----------------

    async def ensure_collection(self) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        self._load()

    async def upsert(
        self, chunks: list[dict[str, Any]], vectors: list[list[float]]
    ) -> None:
        if not chunks:
            return
        self._load()
        assert self._vectors is not None

        new_ids = {c["chunk_id"] for c in chunks}
        # 同 id 覆盖
        keep = [i for i, p in enumerate(self._payloads) if p.get("chunk_id") not in new_ids]
        kept_vectors = self._vectors[keep] if len(keep) else np.zeros((0, self._vectors.shape[1]), np.float32)
        kept_payloads = [self._payloads[i] for i in keep]

        new_vectors = np.asarray(vectors, dtype=np.float32)
        self._vectors = np.vstack([kept_vectors, new_vectors]) if len(kept_payloads) else new_vectors
        self._payloads = kept_payloads + list(chunks)
        self._persist()

    async def delete_by_doc(self, doc_id: str) -> None:
        self._load()
        assert self._vectors is not None
        keep = [i for i, p in enumerate(self._payloads) if p.get("doc_id") != doc_id]
        if len(keep) == len(self._payloads):
            return
        self._vectors = self._vectors[keep]
        self._payloads = [self._payloads[i] for i in keep]
        self._persist()

    # ---------------- 读 ----------------

    async def search(
        self,
        query_vector: list[float],
        *,
        top_k: int,
        acl_ids: list[str],
        doc_ids: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        """余弦检索。acl_ids 为空直接返回空（默认拒绝策略）。"""
        if not acl_ids:
            return []
        self._load()
        assert self._vectors is not None
        if not self._payloads:
            return []

        q = np.asarray(query_vector, dtype=np.float32)
        norm = float(np.linalg.norm(q))
        if norm > 0:
            q = q / norm

        scores = self._vectors @ q
        acl_set = set(acl_ids)
        doc_set = set(doc_ids) if doc_ids else None

        ranked: list[tuple[float, int]] = []
        for idx, payload in enumerate(self._payloads):
            if not acl_set.intersection(payload.get("acl_ids") or []):
                continue
            if doc_set is not None and payload.get("doc_id") not in doc_set:
                continue
            ranked.append((float(scores[idx]), idx))

        ranked.sort(key=lambda item: item[0], reverse=True)
        return [
            {
                "chunk_id": self._payloads[idx].get("chunk_id", ""),
                "score": score,
                "payload": self._payloads[idx],
            }
            for score, idx in ranked[:top_k]
        ]

    async def count(self) -> int:
        self._load()
        return len(self._payloads)

    async def get_chunk(self, chunk_id: str, *, acl_ids: list[str]) -> dict[str, Any] | None:
        """按 chunk_id 取原文，**带 ACL 过滤**。

        与 :meth:`QdrantStore.get_chunk` 语义一致：
        acl_ids 为空直接返回 None（默认拒绝），无权访问与不存在等价。
        """
        if not acl_ids:
            return None
        self._load()
        acl_set = set(acl_ids)
        for payload in self._payloads:
            if payload.get("chunk_id") != chunk_id:
                continue
            if not acl_set.intersection(payload.get("acl_ids") or []):
                return None
            return {"chunk_id": chunk_id, "payload": payload}
        return None

    async def ping(self) -> None:
        """存活探针：本地实现没有网络依赖，能加载就算活着。"""
        self._load()

    async def close(self) -> None:
        return None
