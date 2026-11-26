"""RRF（Reciprocal Rank Fusion）融合算法。

刻意做成无 I/O 依赖的纯函数模块 —— 它是整个检索链路里最该被测试覆盖的
算法之一，不应该因为 qdrant / elasticsearch 客户端没装就跑不了单测。

为什么用 RRF 而不是加权归一化
---------------------------
两路引擎的分数尺度完全不同（BM25 是无上界的正数，余弦相似度是 [-1,1]）。
手工加权需要针对每个语料调权重，换个数据集就得重调 —— 这是没人愿意维护的参数。
RRF 只看**排名**：``score(d) = Σ 1 / (k + rank_i(d))``，无需任何调参，
对分数尺度不敏感，是目前最稳的主流融合方式（k 取 60）。
"""

from __future__ import annotations

from typing import Any


def rrf_merge(
    ranked_lists: list[list[dict[str, Any]]],
    *,
    source_names: list[str] | None = None,
    k: int = 60,
    top_n: int | None = None,
) -> list[dict[str, Any]]:
    """把多个「已按分数降序」的候选列表融合成一个。

    Args:
        ranked_lists: 每个列表内按相关性降序，每项至少含 ``chunk_id``。
        source_names: 每个列表的来源名，用于在结果里标注命中通道。
        k: RRF 常数，越大越平滑（默认 60）。
        top_n: 截断长度，None 表示不截断。

    Returns:
        按 ``rrf_score`` 降序的列表，每项形如::

            {"chunk_id": ..., "rrf_score": ..., "sources": [...], "payload": {...}}
    """
    names = source_names or [f"list{i}" for i in range(len(ranked_lists))]
    scores: dict[str, float] = {}
    payloads: dict[str, dict[str, Any]] = {}
    sources: dict[str, list[str]] = {}

    for name, hits in zip(names, ranked_lists, strict=False):
        for rank, hit in enumerate(hits, start=1):
            chunk_id = hit["chunk_id"]
            scores[chunk_id] = scores.get(chunk_id, 0.0) + 1.0 / (k + rank)
            payloads.setdefault(chunk_id, hit.get("payload") or {})
            hits_by = sources.setdefault(chunk_id, [])
            if name not in hits_by:
                hits_by.append(name)

    merged = [
        {
            "chunk_id": chunk_id,
            "rrf_score": round(score, 6),
            "sources": sources.get(chunk_id, []),
            "payload": payloads.get(chunk_id, {}),
        }
        for chunk_id, score in scores.items()
    ]
    merged.sort(key=lambda x: x["rrf_score"], reverse=True)
    return merged[:top_n] if top_n else merged
