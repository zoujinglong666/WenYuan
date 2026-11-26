"""RRF 融合算法单元测试。"""

from __future__ import annotations

from app.indexing.rrf import rrf_merge


def test_rrf_ranks_consensus_higher():
    """两路都命中的文档应排在只被一路命中的文档之前。"""
    bm25 = [{"chunk_id": "a", "payload": {}}, {"chunk_id": "b", "payload": {}}]
    dense = [{"chunk_id": "b", "payload": {}}, {"chunk_id": "c", "payload": {}}]

    merged = rrf_merge([bm25, dense], source_names=["bm25", "dense"])

    assert merged[0]["chunk_id"] == "b", "两路都命中的 b 应排第一"
    assert set(merged[0]["sources"]) == {"bm25", "dense"}


def test_rrf_score_formula():
    hits = [{"chunk_id": "x", "payload": {}}]
    merged = rrf_merge([hits], source_names=["bm25"], k=60)
    assert abs(merged[0]["rrf_score"] - 1 / 61) < 1e-6


def test_rrf_merges_duplicates_and_keeps_payload():
    hits_a = [{"chunk_id": "x", "payload": {"text": "原文"}}]
    hits_b = [{"chunk_id": "x", "payload": {"text": "原文"}}]
    merged = rrf_merge([hits_a, hits_b], source_names=["bm25", "dense"])

    assert len(merged) == 1
    assert merged[0]["payload"]["text"] == "原文"
    assert abs(merged[0]["rrf_score"] - 2 / 61) < 1e-6


def test_rrf_empty_inputs():
    assert rrf_merge([], source_names=[]) == []
    assert rrf_merge([[], []], source_names=["a", "b"]) == []


def test_rrf_top_n_truncates():
    hits = [{"chunk_id": f"c{i}", "payload": {}} for i in range(20)]
    merged = rrf_merge([hits], source_names=["bm25"], top_n=5)
    assert len(merged) == 5
    assert merged[0]["chunk_id"] == "c0"
