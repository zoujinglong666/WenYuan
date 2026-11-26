"""检索评估 —— 只测检索，不碰生成。

为什么分开
----------
召回不准的时候去调 Prompt 是最常见的误判：改 Prompt 改半天，
Recall@5 纹丝不动，因为病根在召回段而不是生成段。
所以这里**只评检索**：BM25 / 向量 / RRF 融合 / 精排，逐层拆开看。

分通道报告的意义
----------------
    bm25_recall   纯关键词能召回多少
    dense_recall  纯语义能召回多少
    rrf_recall    融合后能召回多少
    rerank_recall 精排后还能剩多少

看哪一层掉得最狠，就知道该修哪一层：
BM25 掉 → 分词或字段权重；dense 掉 → embedding 模型或指令前缀；
RRF 掉 → 融合参数；rerank 掉 → 精排模型或截断长度。

用法::

    python -m scripts.eval --gold data/goldens/handbook.jsonl
    python -m scripts.eval --gold ... --reranker bge-reranker-base --k 1,3,5,10
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import time
from dataclasses import dataclass, field

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.config import settings  # noqa: E402
from app.core.embeddings import get_embedding_service  # noqa: E402
from app.indexing.factory import get_keyword_store, get_vector_store  # noqa: E402
from app.indexing.rrf import rrf_merge  # noqa: E402
from app.retrieval.acl import UserContext, build_acl_ids  # noqa: E402


# ---------------- 指标 ----------------


def recall_at_k(ranked_ids: list[str], gold: set[str], k: int) -> float:
    if not gold:
        return 0.0
    hit = len(set(ranked_ids[:k]) & gold)
    return hit / len(gold)


def reciprocal_rank(ranked_ids: list[str], gold: set[str]) -> float:
    for i, cid in enumerate(ranked_ids, start=1):
        if cid in gold:
            return 1.0 / i
    return 0.0


def ndcg_at_k(ranked_ids: list[str], gold: set[str], k: int) -> float:
    if not gold:
        return 0.0
    dcg = sum(
        1.0 / math_log2(i + 1)
        for i, cid in enumerate(ranked_ids[:k])
        if cid in gold
    )
    idcg = sum(1.0 / math_log2(i + 1) for i in range(min(len(gold), k)))
    return dcg / idcg if idcg > 0 else 0.0


def math_log2(x: float) -> float:
    import math

    return math.log2(x)


@dataclass
class QueryResult:
    qid: str
    query: str
    gold_size: int
    gold_ids: set[str] = field(default_factory=set)
    bm25_ids: list[str] = field(default_factory=list)
    dense_ids: list[str] = field(default_factory=list)
    rrf_ids: list[str] = field(default_factory=list)
    rerank_ids: list[str] = field(default_factory=list)
    ms_embed: float = 0.0
    ms_bm25: float = 0.0
    ms_dense: float = 0.0
    ms_rrf: float = 0.0
    ms_rerank: float = 0.0


# ---------------- 金标解析 ----------------


async def resolve_gold(
    entries: list[dict], vector_store, keyword_store
) -> list[tuple[dict, set[str]]]:
    """把金标条目解析成具体的 chunk_id 集合。

    支持两种指定方式：
    - ``chunk_ids``：直接给 id（最精确）
    - ``heading_contains`` + ``pages``：按标题路径与页码定位（解析结果变了也稳）
    """
    # 收集索引里全部 chunk 的 payload，用于按标题/页码定位
    payload_by_id: dict[str, dict] = {}
    for payload in getattr(vector_store, "_payloads", []) or []:
        payload_by_id[payload.get("chunk_id", "")] = payload
    if not payload_by_id:
        for payload in getattr(keyword_store, "_payloads", []) or []:
            payload_by_id.setdefault(payload.get("chunk_id", ""), payload)

    resolved: list[tuple[dict, set[str]]] = []
    for entry in entries:
        gold: set[str] = set()
        for cid in entry.get("chunk_ids") or []:
            gold.add(cid)

        heading = entry.get("heading_contains")
        pages = set(entry.get("pages") or [])
        if heading or pages:
            for cid, payload in payload_by_id.items():
                hp = payload.get("heading_text") or ""
                page = payload.get("page")
                if heading and heading not in hp:
                    continue
                if pages and page not in pages:
                    continue
                gold.add(cid)

        resolved.append((entry, gold))
    return resolved


# ---------------- 主流程 ----------------


async def run(args: argparse.Namespace) -> int:
    user = UserContext(
        user_id=args.user_id,
        departments=args.departments,
        roles=args.roles,
    )
    acl_ids = build_acl_ids(user)
    if not acl_ids:
        print("没有可见主体，按默认拒绝策略评估不到任何东西", file=sys.stderr)
        return 2

    vector_store = get_vector_store()
    keyword_store = get_keyword_store()
    embeddings = get_embedding_service()

    with open(args.gold, encoding="utf-8") as fh:
        entries = [json.loads(line) for line in fh if line.strip()]

    resolved = await resolve_gold(entries, vector_store, keyword_store)
    empty = [e for e, g in resolved if not g]
    if empty:
        print(f"警告: {len(empty)} 条金标没解析到任何 chunk，已跳过", file=sys.stderr)
        for e in empty:
            print(f"  - {e.get('qid')}: {e.get('query')}", file=sys.stderr)
    resolved = [(e, g) for e, g in resolved if g]

    # 精排器
    reranker = None
    if args.reranker != "none":
        from app.core.onnx_backend import OnnxReranker

        model_file, tok_file = _reranker_paths(args.reranker)
        reranker = OnnxReranker(model_file=model_file, tokenizer_file=tok_file)
        print(f"精排模型: {args.reranker} ({model_file})")

    results: list[QueryResult] = []

    for entry, gold in resolved:
        q = entry["query"]
        r = QueryResult(qid=entry.get("qid", "?"), query=q, gold_size=len(gold), gold_ids=gold)

        t0 = time.perf_counter()
        vector = await embeddings.embed_query(q)
        r.ms_embed = (time.perf_counter() - t0) * 1000

        t0 = time.perf_counter()
        bm25 = await keyword_store.search(
            q, top_k=args.top_k, acl_ids=acl_ids, doc_ids=entry.get("doc_ids")
        )
        r.ms_bm25 = (time.perf_counter() - t0) * 1000

        t0 = time.perf_counter()
        dense = await vector_store.search(
            vector, top_k=args.top_k, acl_ids=acl_ids, doc_ids=entry.get("doc_ids")
        )
        r.ms_dense = (time.perf_counter() - t0) * 1000

        t0 = time.perf_counter()
        merged = rrf_merge(
            [bm25, dense], source_names=["bm25", "dense"], k=settings.rrf_k
        )
        r.ms_rrf = (time.perf_counter() - t0) * 1000

        r.bm25_ids = [c["chunk_id"] for c in bm25]
        r.dense_ids = [c["chunk_id"] for c in dense]
        r.rrf_ids = [c["chunk_id"] for c in merged]

        if reranker is not None and merged:
            docs = [
                f"{(c.get('payload') or {}).get('heading_text', '')}\n"
                f"{(c.get('payload') or {}).get('text', '')}".strip()
                for c in merged
            ]
            t0 = time.perf_counter()
            ranked = await reranker.rank(q, docs, top_n=args.rerank_top_k)
            r.ms_rerank = (time.perf_counter() - t0) * 1000
            r.rerank_ids = [merged[i]["chunk_id"] for i, _ in ranked]
        elif merged:
            r.rerank_ids = r.rrf_ids[: args.rerank_top_k]

        results.append(r)

    report = _summarize(results, args.k_list)
    _print_report(report, results, args)

    if args.out:
        os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
        with open(args.out, "w", encoding="utf-8") as fh:
            json.dump(report, fh, ensure_ascii=False, indent=2)
        print(f"\n报告已写入: {args.out}")
    return 0


def _reranker_paths(name: str) -> tuple[str, str]:
    if name == "jina-reranker-v2-base-multilingual":
        return (
            "jina-reranker-v2-base-multilingual/model_int8.onnx",
            "jina-reranker-v2-base-multilingual/tokenizer.json",
        )
    if name == "bge-reranker-base":
        return ("bge-reranker-base/model_int8.onnx", "bge-reranker-base/tokenizer.json")
    return (name, settings.onnx_rerank_tokenizer_file)


def _summarize(results: list[QueryResult], k_list: list[int]) -> dict:
    out: dict = {"queries": len(results), "metrics": {}, "latency_ms": {}}

    for k in k_list:
        out["metrics"][f"recall@{k}"] = {
            "bm25": statistics.mean(recall_at_k(r.bm25_ids, r.gold_ids, k) for r in results),
            "dense": statistics.mean(recall_at_k(r.dense_ids, r.gold_ids, k) for r in results),
            "rrf": statistics.mean(recall_at_k(r.rrf_ids, r.gold_ids, k) for r in results),
            "rerank": statistics.mean(recall_at_k(r.rerank_ids, r.gold_ids, k) for r in results),
        }
        out["metrics"][f"ndcg@{k}"] = {
            "bm25": statistics.mean(ndcg_at_k(r.bm25_ids, r.gold_ids, k) for r in results),
            "dense": statistics.mean(ndcg_at_k(r.dense_ids, r.gold_ids, k) for r in results),
            "rrf": statistics.mean(ndcg_at_k(r.rrf_ids, r.gold_ids, k) for r in results),
            "rerank": statistics.mean(ndcg_at_k(r.rerank_ids, r.gold_ids, k) for r in results),
        }

    out["metrics"]["mrr"] = {
        "bm25": statistics.mean(reciprocal_rank(r.bm25_ids, r.gold_ids) for r in results),
        "dense": statistics.mean(reciprocal_rank(r.dense_ids, r.gold_ids) for r in results),
        "rrf": statistics.mean(reciprocal_rank(r.rrf_ids, r.gold_ids) for r in results),
        "rerank": statistics.mean(reciprocal_rank(r.rerank_ids, r.gold_ids) for r in results),
    }

    def pct(values: list[float], p: float) -> float:
        if not values:
            return 0.0
        s = sorted(values)
        idx = min(len(s) - 1, int(round((p / 100) * (len(s) - 1))))
        return s[idx]

    for name, attr in (
        ("embed", "ms_embed"),
        ("bm25", "ms_bm25"),
        ("dense", "ms_dense"),
        ("rrf", "ms_rrf"),
        ("rerank", "ms_rerank"),
    ):
        vals = [getattr(r, attr) for r in results if getattr(r, attr) > 0]
        if vals:
            out["latency_ms"][name] = {
                "mean": round(statistics.mean(vals), 2),
                "p50": round(pct(vals, 50), 2),
                "p95": round(pct(vals, 95), 2),
                "max": round(max(vals), 2),
            }
    return out


def _print_report(report: dict, results: list[QueryResult], args) -> None:
    print("=" * 78)
    print(f"检索评估 · {report['queries']} 条查询 · top_k={args.top_k} · rerank_top_k={args.rerank_top_k}")
    print("=" * 78)

    for k in args.k_list:
        rc = report["metrics"][f"recall@{k}"]
        nd = report["metrics"][f"ndcg@{k}"]
        print(f"\nRecall@{k} / nDCG@{k}")
        print(f"  {'通道':<10}{'Recall':>10}{'nDCG':>10}")
        for ch in ("bm25", "dense", "rrf", "rerank"):
            print(
                f"  {ch:<10}{rc[ch]*100:>9.1f}%{nd[ch]*100:>9.1f}%"
            )

    mrr = report["metrics"]["mrr"]
    print("\nMRR")
    print(f"  {'通道':<10}{'MRR':>10}")
    for ch in ("bm25", "dense", "rrf", "rerank"):
        print(f"  {ch:<10}{mrr[ch]:>10.4f}")

    if report["latency_ms"]:
        print("\n各阶段耗时 (ms)")
        print(f"  {'阶段':<10}{'均值':>10}{'P50':>10}{'P95':>10}{'最大':>10}")
        for name, v in report["latency_ms"].items():
            print(
                f"  {name:<10}{v['mean']:>10.2f}{v['p50']:>10.2f}"
                f"{v['p95']:>10.2f}{v['max']:>10.2f}"
            )

    misses = [
        r for r in results if recall_at_k(r.rerank_ids, r.gold_ids, 5) < 1.0
    ]
    if misses:
        print(f"\n未召回（rerank@5 < 1.0）：{len(misses)} 条")
        for r in misses[:12]:
            print(f"  [{r.qid}] {r.query}")
            print(f"        gold={len(r.gold_ids)} 命中@5={recall_at_k(r.rerank_ids, r.gold_ids, 5):.2f}")


def main() -> int:
    ap = argparse.ArgumentParser(description="检索评估")
    ap.add_argument("--gold", required=True, help="金标 JSONL 路径")
    ap.add_argument("--top-k", type=int, default=50, help="每路召回条数")
    ap.add_argument("--rerank-top-k", type=int, default=8, help="精排保留条数")
    ap.add_argument("--k-list", default="1,3,5,10", help="要报告的 K 列表")
    ap.add_argument(
        "--reranker",
        default="jina-reranker-v2-base-multilingual",
        help="精排模型名（jina-reranker-v2-base-multilingual / bge-reranker-base / none）",
    )
    ap.add_argument("--user-id", default="evaluator")
    ap.add_argument("--departments", default="")
    ap.add_argument("--roles", default="")
    ap.add_argument("--out", default="", help="报告 JSON 输出路径")
    args = ap.parse_args()

    args.k_list = [int(x) for x in args.k_list.split(",") if x.strip()]
    args.departments = [d for d in args.departments.split(",") if d]
    args.roles = [r for r in args.roles.split(",") if r]
    return asyncio.run(run(args))


if __name__ == "__main__":
    sys.exit(main())
