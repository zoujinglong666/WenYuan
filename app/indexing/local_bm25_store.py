"""本地 BM25 关键词存储 —— 没有 Elasticsearch 时的离线替身。

这一路不可替代：合同编号（HT-2024-0873）、制度名称、产品型号这类
"必须字面命中"的查询，纯向量检索经常召不回或召错。

分词规则与 ES 内置 ``cjk`` analyzer 对齐：
- CJK 连续片段切**二元组**（bigram）——「住宿标准」→ 住宿 / 宿标 / 标准
- 拉丁字母与数字整体保留并小写

字段权重与 ES 映射保持一致：正文 ×3、标题路径 ×2、文档标题 ×1.5、父块 ×1。

接口与 :class:`~app.indexing.es_store.ESStore` 一致，
通过 ``KEYWORD_BACKEND=local`` 启用。生产仍然是 Elasticsearch。
"""

from __future__ import annotations

import json
import logging
import math
import re
from pathlib import Path
from typing import Any

from app.config import settings

logger = logging.getLogger(__name__)

#: BM25 参数（与 Elasticsearch 默认接近）
K1 = 1.2
B = 0.75

#: 字段权重
FIELD_WEIGHTS: list[tuple[str, float]] = [
    ("text", 3.0),
    ("heading_text", 2.0),
    ("title", 1.5),
    ("parent_text", 1.0),
]

_CJK = r"一-鿿㐀-䶿"
_TOKEN_RE = re.compile(rf"[{_CJK}]+|[A-Za-z0-9]+")
_CJK_ONLY = re.compile(rf"^[{_CJK}]+$")


def tokenize(text: str) -> list[str]:
    """CJK 二元组 + 拉丁词 —— 与 ES ``cjk`` analyzer 同构。"""
    tokens: list[str] = []
    for segment in _TOKEN_RE.findall(text or ""):
        if _CJK_ONLY.match(segment):
            if len(segment) == 1:
                tokens.append(segment)
            else:
                tokens.extend(segment[i : i + 2] for i in range(len(segment) - 1))
        else:
            tokens.append(segment.lower())
    return tokens


class LocalBM25Store:
    """进程内 BM25 索引。倒排表在首次查询时构建，写入后失效重建。"""

    def __init__(self, store_dir: str | None = None) -> None:
        self.dir = Path(store_dir or settings.local_store_dir)
        # 与向量库各存一份 payload：两份文件内容相同，但互不覆盖，
        # 避免写入顺序不同导致索引与向量对不上。
        self.chunks_path = self.dir / "chunks_bm25.jsonl"

        self._payloads: list[dict[str, Any]] = []
        self._loaded = False
        self._index: dict[str, list[tuple[int, float]]] | None = None
        self._doc_len: list[float] = []
        self._avg_len: float = 0.0

    # ---------------- 持久化 ----------------

    def _load(self) -> None:
        if self._loaded:
            return
        self._loaded = True
        if not self.chunks_path.exists():
            self._payloads = []
            return
        payloads: list[dict[str, Any]] = []
        with self.chunks_path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    payloads.append(json.loads(line))
        self._payloads = payloads
        logger.info("本地关键词库已加载: %d 条", len(payloads))

    def _persist(self) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        with self.chunks_path.open("w", encoding="utf-8") as fh:
            for payload in self._payloads:
                fh.write(json.dumps(payload, ensure_ascii=False) + "\n")
        self._index = None

    # ---------------- 索引 ----------------

    def _build_index(self) -> None:
        if self._index is not None:
            return
        index: dict[str, list[tuple[int, float]]] = {}
        lengths: list[float] = []
        for doc_idx, payload in enumerate(self._payloads):
            weighted: dict[str, float] = {}
            for field, weight in FIELD_WEIGHTS:
                value = payload.get(field)
                if not value:
                    continue
                for token in tokenize(str(value)):
                    weighted[token] = weighted.get(token, 0.0) + weight
            length = sum(weighted.values())
            lengths.append(length)
            for token, weight in weighted.items():
                index.setdefault(token, []).append((doc_idx, weight))

        self._index = index
        self._doc_len = lengths
        self._avg_len = (sum(lengths) / len(lengths)) if lengths else 0.0

    # ---------------- 写 ----------------

    async def ensure_index(self) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        self._load()

    async def bulk_index(self, chunks: list[dict[str, Any]]) -> None:
        if not chunks:
            return
        self._load()
        new_ids = {c["chunk_id"] for c in chunks}
        keep = [p for p in self._payloads if p.get("chunk_id") not in new_ids]
        self._payloads = keep + list(chunks)
        self._persist()

    async def delete_by_doc(self, doc_id: str) -> None:
        self._load()
        before = len(self._payloads)
        self._payloads = [p for p in self._payloads if p.get("doc_id") != doc_id]
        if len(self._payloads) != before:
            self._persist()

    async def refresh(self) -> None:
        self._index = None

    # ---------------- 读 ----------------

    async def search(
        self,
        query_text: str,
        *,
        top_k: int,
        acl_ids: list[str],
        doc_ids: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        """BM25 召回。acl_ids 为空直接返回空（默认拒绝策略）。"""
        if not acl_ids:
            return []
        self._load()
        if not self._payloads:
            return []
        self._build_index()
        assert self._index is not None

        tokens = tokenize(query_text)
        if not tokens:
            return []

        scores: dict[int, float] = {}
        for token in set(tokens):
            postings = self._index.get(token)
            if not postings:
                continue
            df = len(postings)
            idf = math.log(1 + (len(self._payloads) - df + 0.5) / (df + 0.5))
            for doc_idx, weight in postings:
                tf = weight
                doc_len = self._doc_len[doc_idx] or 1.0
                denom = tf + K1 * (1 - B + B * doc_len / (self._avg_len or 1.0))
                scores[doc_idx] = scores.get(doc_idx, 0.0) + idf * (tf * (K1 + 1)) / denom

        acl_set = set(acl_ids)
        doc_set = set(doc_ids) if doc_ids else None
        ranked = [
            (score, idx)
            for idx, score in scores.items()
            if acl_set.intersection(self._payloads[idx].get("acl_ids") or [])
            and (doc_set is None or self._payloads[idx].get("doc_id") in doc_set)
        ]
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

    async def ping(self) -> None:
        """存活探针：本地实现没有网络依赖，能加载就算活着。"""
        self._load()

    async def close(self) -> None:
        return None
