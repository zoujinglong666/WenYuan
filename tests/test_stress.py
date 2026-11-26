"""scripts/stress.py 的单元测试 —— 只测纯函数与数据结构，不碰网络和外部服务。

压测脚本本身依赖真实后端，但它的「统计口径」必须先被测住：
分位数算错，压测结论就是错的；查询装载漏了文件，压测就静默少测一半场景。
"""

from __future__ import annotations

import json
from pathlib import Path

from scripts.stress import (
    LevelResult,
    _collect_stage_timings,
    load_queries,
    load_queries_file,
    percentile,
)


# ---------------- percentile ----------------


class TestPercentile:
    def test_empty_returns_zero(self):
        # 没有样本时返回 0 而不是抛异常 —— 报表不该因为一个空档位而中断
        assert percentile([], 50) == 0.0

    def test_single_value(self):
        assert percentile([42.0], 0) == 42.0
        assert percentile([42.0], 50) == 42.0
        assert percentile([42.0], 100) == 42.0

    def test_nearest_rank(self):
        vals = [10.0, 20.0, 30.0, 40.0, 50.0]
        assert percentile(vals, 50) == 30.0
        assert percentile(vals, 0) == 10.0
        assert percentile(vals, 100) == 50.0

    def test_does_not_mutate_input(self):
        vals = [30.0, 10.0, 20.0]
        percentile(vals, 50)
        assert vals == [30.0, 10.0, 20.0]


# ---------------- LevelResult 口径 ----------------


class TestLevelResult:
    def test_qps_counts_success_only(self):
        # 10 个请求 2 个失败：吞吐按完成数算，失败请求不该给 QPS 注水
        r = LevelResult(concurrency=4, requests=10, errors=2, wall_s=4.0)
        assert r.completed == 8
        assert r.qps == 2.0

    def test_qps_zero_wall(self):
        r = LevelResult(concurrency=1, requests=0, errors=0, wall_s=0.0)
        assert r.qps == 0.0

    def test_pct_delegates(self):
        r = LevelResult(concurrency=1, requests=3, latencies_ms=[100.0, 200.0, 300.0])
        assert r.pct(50) == 200.0
        assert r.pct(95) == 300.0

    def test_stage_pct_missing_stage(self):
        r = LevelResult(concurrency=1, requests=1)
        assert r.stage_pct("retrieve", 95) == 0.0


# ---------------- 阶段耗时收集 ----------------


class TestCollectStageTimings:
    def test_collects_numeric_timings(self):
        stage_ms: dict[str, list[float]] = {}
        _collect_stage_timings(
            stage_ms,
            {"retrieval": {"timings": {"retrieve": 12.5, "rerank": 30.0}}},
        )
        assert stage_ms["retrieve"] == [12.5]
        assert stage_ms["rerank"] == [30.0]

    def test_tolerates_missing_or_non_numeric(self):
        stage_ms: dict[str, list[float]] = {}
        _collect_stage_timings(stage_ms, {})
        _collect_stage_timings(stage_ms, {"retrieval": {"timings": {"retrieve": "bad"}}})
        assert stage_ms == {}


# ---------------- 查询装载 ----------------


class TestLoadQueries:
    def test_reads_all_jsonl_and_dedupes(self, tmp_path: Path):
        (tmp_path / "a.jsonl").write_text(
            "\n".join(
                [
                    json.dumps({"qid": "q1", "query": "差旅住宿标准是多少钱一晚"}),
                    json.dumps({"qid": "q2", "query": "年假怎么算"}),
                ]
            ),
            encoding="utf-8",
        )
        (tmp_path / "b.jsonl").write_text(
            "\n".join(
                [
                    json.dumps({"qid": "q3", "query": "年假怎么算"}),  # 跨文件去重
                    json.dumps({"qid": "q4", "query": "合同编号 HT-2024-0873 是什么"}),
                ]
            ),
            encoding="utf-8",
        )
        queries = load_queries(tmp_path)
        assert queries == ["差旅住宿标准是多少钱一晚", "年假怎么算", "合同编号 HT-2024-0873 是什么"]

    def test_skips_bad_lines_and_empty_query(self, tmp_path: Path):
        (tmp_path / "a.jsonl").write_text(
            "\n".join(
                [
                    "不是json的一行",
                    json.dumps({"qid": "q1"}),  # 没有 query 字段
                    json.dumps({"qid": "q2", "query": "有效查询"}),
                    "",
                ]
            ),
            encoding="utf-8",
        )
        assert load_queries(tmp_path) == ["有效查询"]

    def test_missing_dir_returns_empty(self, tmp_path: Path):
        assert load_queries(tmp_path / "nope") == []

    def test_defaults_to_project_goldens(self):
        # 项目里已有 retrieval_golden.jsonl —— 默认路径必须能读到它，
        # 这是之前硬编码 handbook.jsonl 导致压测直接退出的教训
        queries = load_queries()
        assert len(queries) >= 5


class TestLoadQueriesFile:
    def test_plain_lines(self, tmp_path: Path):
        f = tmp_path / "q.txt"
        f.write_text("第一个问题\n\n第二个问题\n", encoding="utf-8")
        assert load_queries_file(f) == ["第一个问题", "第二个问题"]

    def test_jsonl_lines(self, tmp_path: Path):
        f = tmp_path / "q.jsonl"
        f.write_text(json.dumps({"query": "金标查询"}), encoding="utf-8")
        assert load_queries_file(f) == ["金标查询"]
