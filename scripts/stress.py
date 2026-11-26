"""压测 —— 用真实文档、真实查询、真实后端。

测什么
------
1. **检索延迟分位**（p50/p95/p99）：并发 1/2/4/8/16 各跑一轮，
   看延迟随并发怎么劣化 —— 单看平均延迟会掩盖排队。
2. **吞吐**（QPS）：并发下成功完成的查询数/秒。
3. **阶段耗时**：HTTP 模式解析响应里的 ``retrieval.timings``，
   把「召回」和「精排」的分位也摊开 —— 端到端延迟涨了，
   得知道是召回变慢还是精排在排队。
4. **内存**：进程 RSS 变化。``inproc`` 模式下这就是检索服务本身；
   ``http`` 模式下只是压测客户端，别拿它当服务端内存结论。
5. **摄入各段耗时**：``--mode ingest`` 上传真实文档，
   按 archive / parse / chunk / embed / index 分摊到毫秒。

两种目标
--------
- ``--target http``：打真实 FastAPI（默认）。测的是部署形态 ——
  单端口内网部署下用户真正面对的那条链路。
- ``--target inproc``：进程内直调检索器。用来把「检索本身」和
  「HTTP + JSON」的开销拆开。仅支持 search 模式。

三种模式
--------
- ``--mode search``：POST /v1/search，只检索不生成（默认）。
- ``--mode chat``：POST /v1/chat，全链路含 LLM 生成。
- ``--mode ingest``：POST /v1/documents 逐个上传 --ingest-dir 下的文档。
  摄入是重解析 + 重 GPU 的流水线，先看单发基线，不做并发档位扫描。

用法::

    python -m scripts.stress --base-url http://127.0.0.1:8088 --levels 1,2,4,8,16
    python -m scripts.stress --target inproc
    python -m scripts.stress --mode ingest --ingest-dir data/samples --ingest-repeat 3
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import statistics
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import httpx  # noqa: E402

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# ingest 模式接受的文件后缀，与 parsing/registry 支持的范围对齐
INGEST_SUFFIXES = {".md", ".txt", ".pdf", ".docx", ".pptx", ".xlsx"}


def percentile(values: list[float], p: float) -> float:
    """最近邻分位。空列表返回 0 —— 没有样本时不假装有数字。"""
    if not values:
        return 0.0
    s = sorted(values)
    idx = min(len(s) - 1, int(round((p / 100) * (len(s) - 1))))
    return s[idx]


def _stage_stats(vals: list[float]) -> dict[str, float]:
    """一组耗时样本的三分位摘要，报告 JSON 与终端输出共用同一口径。"""
    return {
        "p50": round(percentile(vals, 50), 1),
        "p95": round(percentile(vals, 95), 1),
        "max": round(max(vals), 1),
    }


@dataclass
class LevelResult:
    """一个并发档位的完整观测。"""

    concurrency: int
    requests: int = 0  # 发出的请求总数（含失败）
    errors: int = 0
    latencies_ms: list[float] = field(default_factory=list)  # 仅成功请求
    stage_ms: dict[str, list[float]] = field(default_factory=dict)  # 服务端各阶段耗时
    error_samples: list[str] = field(default_factory=list)  # 前几条错误原文，排障用
    wall_s: float = 0.0
    rss_before_mb: float = 0.0
    rss_after_mb: float = 0.0
    rss_peak_mb: float = 0.0

    @property
    def completed(self) -> int:
        return self.requests - self.errors

    @property
    def qps(self) -> float:
        return self.completed / self.wall_s if self.wall_s > 0 else 0.0

    def pct(self, p: float) -> float:
        return percentile(self.latencies_ms, p)

    def stage_pct(self, stage: str, p: float) -> float:
        return percentile(self.stage_ms.get(stage, []), p)


@dataclass
class IngestResult:
    """一次文档上传的摄入分段耗时。"""

    filename: str
    size_kb: float
    status: str
    parser: str
    chunk_count: int
    total_ms: float
    timings_ms: dict[str, float] = field(default_factory=dict)
    skipped: bool = False
    error: str | None = None


# ---------------- 内存探针 ----------------


def rss_mb() -> float:
    """进程常驻内存（MB）。拿不到就返回 0。"""
    try:
        import psutil

        return psutil.Process().memory_info().rss / 1024 / 1024
    except Exception:  # noqa: BLE001
        try:
            import resource  # POSIX

            return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024
        except Exception:  # noqa: BLE001
            return 0.0


# ---------------- 目标实现 ----------------


def _collect_stage_timings(stage_ms: dict[str, list[float]], payload: dict) -> None:
    """把响应里的 retrieval.timings 按阶段累积，最后统一算分位。"""
    timings = ((payload.get("retrieval") or {}).get("timings")) or {}
    for key, val in timings.items():
        if isinstance(val, (int, float)):
            stage_ms.setdefault(key, []).append(float(val))


async def http_call(
    client: httpx.AsyncClient,
    base_url: str,
    mode: str,
    query: str,
    top_k: int,
    identity: dict,
    stage_ms: dict[str, list[float]] | None = None,
) -> None:
    """发一次 HTTP 请求；search/chat 模式顺手收集服务端阶段耗时。"""
    if mode == "chat":
        resp = await client.post(
            f"{base_url}/v1/chat",
            json={
                "question": query,
                "user_id": identity["user_id"],
                "departments": identity["departments"],
                "roles": identity["roles"],
                "history": [],
                "doc_ids": None,
            },
        )
    else:
        resp = await client.post(
            f"{base_url}/v1/search",
            json={
                "query": query,
                "top_k": top_k,
                "user_id": identity["user_id"],
                "departments": identity["departments"],
                "roles": identity["roles"],
            },
        )
    resp.raise_for_status()
    if stage_ms is not None:
        try:
            _collect_stage_timings(stage_ms, resp.json())
        except Exception:  # noqa: BLE001
            pass  # 响应体解析失败不影响压测本身，端到端延迟已经记到了


async def inproc_search(query: str, top_k: int, identity: dict) -> None:
    """进程内直跑一遍「双路召回 → RRF」，把 HTTP 开销剥出去。"""
    from app.config import settings
    from app.core.embeddings import get_embedding_service
    from app.indexing.factory import get_keyword_store, get_vector_store
    from app.indexing.rrf import rrf_merge
    from app.retrieval.acl import UserContext, build_acl_ids

    user = UserContext(
        user_id=identity["user_id"],
        departments=identity["departments"],
        roles=identity["roles"],
    )
    acl_ids = build_acl_ids(user)
    emb = get_embedding_service()
    vector = await emb.embed_query(query)
    bm25 = await get_keyword_store().search(query, top_k=top_k, acl_ids=acl_ids)
    dense = await get_vector_store().search(vector, top_k=top_k, acl_ids=acl_ids)
    rrf_merge([bm25, dense], source_names=["bm25", "dense"], k=settings.rrf_k)


# ---------------- 并发档位 ----------------


async def run_level(
    target: str,
    mode: str,
    base_url: str,
    queries: list[str],
    concurrency: int,
    per_worker: int,
    top_k: int,
    identity: dict,
    timeout: float,
) -> LevelResult:
    """跑一个并发档位：并发位 × 每位请求数，Semaphore 控制在档位并发。"""
    result = LevelResult(concurrency=concurrency)
    result.rss_before_mb = rss_mb()
    peak = result.rss_before_mb

    async with httpx.AsyncClient(timeout=timeout) as client:
        _sem = asyncio.Semaphore(concurrency)

        async def one(query: str) -> None:
            nonlocal peak
            async with _sem:
                t0 = time.perf_counter()
                try:
                    if target == "inproc":
                        await inproc_search(query, top_k, identity)
                    else:
                        await http_call(
                            client, base_url, mode, query, top_k, identity, result.stage_ms
                        )
                    # 只把成功请求计入延迟分位；失败延迟混进去会把 p95 变成「超时分布」
                    result.latencies_ms.append((time.perf_counter() - t0) * 1000)
                except Exception as exc:  # noqa: BLE001
                    result.errors += 1
                    if len(result.error_samples) < 3:
                        result.error_samples.append(f"{type(exc).__name__}: {exc}"[:200])
                finally:
                    result.requests += 1
                    cur = rss_mb()
                    if cur > peak:
                        peak = cur

        t0 = time.perf_counter()
        tasks = [one(queries[i % len(queries)]) for i in range(concurrency * per_worker)]
        await asyncio.gather(*tasks)
        result.wall_s = time.perf_counter() - t0

    result.rss_after_mb = rss_mb()
    result.rss_peak_mb = peak
    return result


async def warmup(
    target: str,
    mode: str,
    base_url: str,
    queries: list[str],
    top_k: int,
    identity: dict,
    n: int,
    timeout: float,
) -> None:
    """预热：首请求往往在等模型加载 / 连接池建立，不预热会污染 p50。

    失败也继续 —— 预热只是尽力而为，真正的错误让正式请求去暴露。
    """
    if n <= 0 or not queries:
        return
    print(f"预热 {n} 次请求（不计入统计）…")
    async with httpx.AsyncClient(timeout=timeout) as client:
        for i in range(n):
            try:
                if target == "inproc":
                    await inproc_search(queries[0], top_k, identity)
                else:
                    await http_call(
                        client, base_url, mode, queries[i % len(queries)], top_k, identity
                    )
            except Exception as exc:  # noqa: BLE001
                print(f"  预热请求失败（继续）: {type(exc).__name__}: {exc}"[:160])


# ---------------- ingest 模式 ----------------


async def run_ingest(
    base_url: str,
    ingest_dir: Path,
    repeat: int,
    timeout: float,
    incremental: bool,
) -> list[IngestResult]:
    """逐个上传文档，收集解析/切块/向量化/入库的分段耗时。

    顺序执行：摄入是重 IO + 重 GPU 流水线，先建立单发基线；
    并发摄入的容量规划等基线清楚后再谈。

    ``incremental`` 关掉 force —— 第二次起会命中 content-hash 短路，
    顺便量出「同一份文档重复上传」的真实成本（应该接近 0）。
    """
    files = sorted(
        p for p in ingest_dir.iterdir() if p.suffix.lower() in INGEST_SUFFIXES and p.is_file()
    )
    if not files:
        supported = ", ".join(sorted(INGEST_SUFFIXES))
        print(f"目录里没有可摄入的文档（支持 {supported}）: {ingest_dir}", file=sys.stderr)
        return []

    print(f"摄入基线 · {len(files)} 个文档 × {repeat} 次 · force={'否（增量短路）' if incremental else '是'}")
    print("-" * 78)

    results: list[IngestResult] = []
    async with httpx.AsyncClient(timeout=timeout) as client:
        for path in files:
            data = path.read_bytes()
            size_kb = len(data) / 1024
            for i in range(repeat):
                t0 = time.perf_counter()
                try:
                    resp = await client.post(
                        f"{base_url}/v1/documents",
                        files={"file": (path.name, data)},
                        data={"acl_ids": "public", "force": "false" if incremental else "true"},
                    )
                    resp.raise_for_status()
                    body = resp.json()
                    timings = {k: float(v) for k, v in (body.get("timings") or {}).items()}
                    results.append(
                        IngestResult(
                            filename=path.name,
                            size_kb=size_kb,
                            status=body.get("status", "?"),
                            parser=body.get("parser", ""),
                            chunk_count=body.get("chunk_count", 0),
                            total_ms=(time.perf_counter() - t0) * 1000,
                            timings_ms=timings,
                            skipped=body.get("skipped", False),
                        )
                    )
                    r = results[-1]
                    t = r.timings_ms
                    print(
                        f"{r.filename:<36} #{i + 1} {r.status:<6} chunks={r.chunk_count:<4} "
                        f"parse {t.get('parse', 0):>7.0f}ms  embed {t.get('embed', 0):>7.0f}ms  "
                        f"index {t.get('index', 0):>7.0f}ms  总计 {r.total_ms:>7.0f}ms"
                        + ("  [增量短路]" if r.skipped else "")
                    )
                except Exception as exc:  # noqa: BLE001
                    results.append(
                        IngestResult(
                            filename=path.name,
                            size_kb=size_kb,
                            status="error",
                            parser="",
                            chunk_count=0,
                            total_ms=(time.perf_counter() - t0) * 1000,
                            error=f"{type(exc).__name__}: {exc}"[:200],
                        )
                    )
                    print(f"{path.name:<36} #{i + 1} 失败: {results[-1].error}")
    return results


def print_ingest_summary(results: list[IngestResult]) -> dict:
    """汇总各段耗时：跳过增量短路的样本（它们量的是查重，不是管线）。"""
    full = [r for r in results if r.status == "ready" and not r.skipped]
    if not full:
        print("\n没有成功的完整摄入样本可汇总。")
        return {}

    stages = ["archive", "parse", "chunk", "embed", "index"]
    print("-" * 78)
    print(f"{'阶段':<10} {'p50':>10} {'p95':>10} {'max':>10}   （{len(full)} 个完整样本）")
    for s in stages:
        vals = [r.timings_ms[s] for r in full if s in r.timings_ms]
        if vals:
            p50, p95, mx = percentile(vals, 50), percentile(vals, 95), max(vals)
            print(f"{s:<10} {p50:>8.0f}ms {p95:>8.0f}ms {mx:>8.0f}ms")
    totals = [r.total_ms for r in full]
    chunks = [r.chunk_count for r in full]
    t50, t95, tmax = percentile(totals, 50), percentile(totals, 95), max(totals)
    print(f"{'端到端':<10} {t50:>8.0f}ms {t95:>8.0f}ms {tmax:>8.0f}ms")

    mean_chunks = max(statistics.mean(chunks), 1)
    embed_per = statistics.mean([r.timings_ms.get("embed", 0) for r in full]) / mean_chunks
    index_per = statistics.mean([r.timings_ms.get("index", 0) for r in full]) / mean_chunks
    print(
        f"\n每 chunk 分摊：embed {embed_per:.0f}ms  index {index_per:.0f}ms  "
        f"（均值 chunk/文档 = {statistics.mean(chunks):.1f}）"
    )

    skipped = [r for r in results if r.skipped]
    if skipped:
        st = [r.total_ms for r in skipped]
        print(f"增量短路样本 {len(skipped)} 个：端到端 p50 {percentile(st, 50):.0f}ms —— 重复上传应该停在这个量级")

    return {
        "files": len({r.filename for r in full}),
        "full_ingests": len(full),
        "skipped": len(skipped),
        "errors": sum(1 for r in results if r.status == "error"),
        "stage_ms": {
            s: _stage_stats([r.timings_ms[s] for r in full if s in r.timings_ms])
            for s in stages
            if any(s in r.timings_ms for r in full)
        },
        "total_ms": _stage_stats(totals),
        "avg_chunks_per_doc": round(statistics.mean(chunks), 1),
        "detail": [
            {
                "filename": r.filename,
                "status": r.status,
                "skipped": r.skipped,
                "parser": r.parser,
                "chunk_count": r.chunk_count,
                "total_ms": round(r.total_ms, 1),
                "timings_ms": {k: round(v, 1) for k, v in r.timings_ms.items()},
                "error": r.error,
            }
            for r in results
        ],
    }


# ---------------- 查询装载 ----------------


def load_queries(golden_dir: Path | None = None) -> list[str]:
    """从金标集目录收集真实查询（所有 *.jsonl 的 query 字段，去重保序）。"""
    d = golden_dir or (PROJECT_ROOT / "data" / "goldens")
    if not d.is_dir():
        return []
    out: list[str] = []
    seen: set[str] = set()
    for path in sorted(d.glob("*.jsonl")):
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    item = json.loads(line)
                except json.JSONDecodeError:
                    continue
                q = item.get("query")
                if q and q not in seen:
                    seen.add(q)
                    out.append(q)
    return out


def load_queries_file(path: Path) -> list[str]:
    """每行一条查询的纯文本文件；jsonl 也能读（取 query 字段）。"""
    out: list[str] = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            if line.startswith("{"):
                try:
                    q = json.loads(line).get("query")
                except json.JSONDecodeError:
                    q = None
                if q:
                    out.append(q)
            else:
                out.append(line)
    return out


# ---------------- 主流程 ----------------


async def main() -> int:
    ap = argparse.ArgumentParser(description="压测")
    ap.add_argument("--base-url", default="http://127.0.0.1:8088")
    ap.add_argument("--target", choices=["http", "inproc"], default="http")
    ap.add_argument("--mode", choices=["search", "chat", "ingest"], default="search")
    ap.add_argument("--levels", default="1,2,4,8,16", help="并发档位，逗号分隔")
    ap.add_argument("--per-worker", type=int, default=8, help="每并发位发多少请求")
    ap.add_argument("--top-k", type=int, default=50)
    ap.add_argument("--timeout", type=float, default=120.0)
    ap.add_argument("--warmup", type=int, default=2, help="正式计时前的预热请求数（不计入统计）")
    ap.add_argument("--user-id", default="stress")
    ap.add_argument("--departments", default="")
    ap.add_argument("--roles", default="")
    ap.add_argument("--queries", default="", help="逗号分隔的查询列表，覆盖金标集")
    ap.add_argument("--queries-file", default="", help="每行一条查询的文件，覆盖金标集")
    ap.add_argument("--ingest-dir", default=str(PROJECT_ROOT / "data" / "samples"))
    ap.add_argument("--ingest-repeat", type=int, default=3, help="ingest 模式：每个文档上传几次")
    ap.add_argument("--incremental", action="store_true", help="ingest 模式：关掉 force，量增量短路路径")
    ap.add_argument("--out", default="data/stress_report.json")
    args = ap.parse_args()

    if args.target == "inproc" and args.mode != "search":
        print("inproc 目标只支持 search 模式（chat/ingest 走 HTTP 才有意义）", file=sys.stderr)
        return 2

    os.chdir(PROJECT_ROOT)  # --out 相对路径按项目根解析，和 docker 里行为一致

    # ---- ingest 模式：不需要查询，需要文档 ----
    if args.mode == "ingest":
        ingest_dir = Path(args.ingest_dir)
        if not ingest_dir.is_dir():
            print(f"摄入目录不存在: {ingest_dir}", file=sys.stderr)
            return 2
        print("=" * 78)
        print(f"压测 · target={args.target} · mode=ingest · dir={args.ingest_dir}")
        print("=" * 78)
        results = await run_ingest(
            args.base_url, ingest_dir, args.ingest_repeat, args.timeout, args.incremental
        )
        summary = print_ingest_summary(results)
        report = {
            "target": args.target,
            "mode": "ingest",
            "base_url": args.base_url,
            "ingest_dir": args.ingest_dir,
            "ingest_repeat": args.ingest_repeat,
            "incremental": args.incremental,
            "summary": summary,
        }
        _write_report(args.out, report)
        return 0 if results and summary else 1

    # ---- search/chat 模式：装载查询 ----
    if args.queries_file:
        qf = Path(args.queries_file)
        if not qf.is_file():
            print(f"查询文件不存在: {qf}", file=sys.stderr)
            return 2
        queries = load_queries_file(qf)
    elif args.queries:
        queries = [q.strip() for q in args.queries.split(",") if q.strip()]
    else:
        queries = load_queries()
    if not queries:
        print(
            "没有可用查询 —— data/goldens/*.jsonl 缺失或为空；"
            "可用 --queries '问题1,问题2' 或 --queries-file 文件 指定",
            file=sys.stderr,
        )
        return 2

    levels = [int(x) for x in args.levels.split(",") if x.strip()]
    identity = {
        "user_id": args.user_id,
        "departments": [d for d in args.departments.split(",") if d],
        "roles": [r for r in args.roles.split(",") if r],
    }

    print("=" * 78)
    print(f"压测 · target={args.target} · mode={args.mode} · 查询数={len(queries)}")
    print(f"每档请求数 = 并发 × {args.per_worker} · top_k={args.top_k} · 预热 {args.warmup}")
    print("=" * 78)

    await warmup(
        args.target,
        args.mode,
        args.base_url,
        queries,
        args.top_k,
        identity,
        args.warmup,
        args.timeout,
    )

    results: list[LevelResult] = []
    for c in levels:
        r = await run_level(
            args.target,
            args.mode,
            args.base_url,
            queries,
            c,
            args.per_worker,
            args.top_k,
            identity,
            args.timeout,
        )
        results.append(r)
        line = (
            f"并发 {r.concurrency:>3} | "
            f"p50 {r.pct(50):>8.1f}ms  p95 {r.pct(95):>8.1f}ms  p99 {r.pct(99):>8.1f}ms  "
            f"max {max(r.latencies_ms or [0]):>8.1f}ms | "
            f"{r.qps:>6.2f} QPS | 错误 {r.errors}/{r.requests}"
        )
        if r.stage_ms:
            stages = "  ".join(f"{k} p95 {r.stage_pct(k, 95):.0f}ms" for k in sorted(r.stage_ms))
            line += f" | {stages}"
        rss_label = "RSS(服务)" if args.target == "inproc" else "RSS(客户端)"
        line += f" | {rss_label} {r.rss_before_mb:.0f}→{r.rss_peak_mb:.0f}MB"
        print(line)
        for sample in r.error_samples:
            print(f"    └ 错误样本: {sample}")

    rss_scope = "server-inproc" if args.target == "inproc" else "stress-client-http"
    report = {
        "target": args.target,
        "mode": args.mode,
        "base_url": args.base_url,
        "queries": len(queries),
        "per_worker": args.per_worker,
        "top_k": args.top_k,
        "warmup": args.warmup,
        "rss_scope": rss_scope,
        "levels": [
            {
                "concurrency": r.concurrency,
                "requests": r.requests,
                "errors": r.errors,
                "completed": r.completed,
                "wall_s": round(r.wall_s, 3),
                "qps": round(r.qps, 2),
                "latency_ms": {
                    "p50": round(r.pct(50), 2),
                    "p95": round(r.pct(95), 2),
                    "p99": round(r.pct(99), 2),
                    "max": round(max(r.latencies_ms or [0]), 2),
                    "mean": round(statistics.mean(r.latencies_ms or [0]), 2),
                },
                "stage_ms": {
                    k: {
                        "p50": round(percentile(v, 50), 2),
                        "p95": round(percentile(v, 95), 2),
                        "max": round(max(v), 2),
                    }
                    for k, v in sorted(r.stage_ms.items())
                },
                "error_samples": r.error_samples,
                "rss_mb": {
                    "before": round(r.rss_before_mb, 1),
                    "after": round(r.rss_after_mb, 1),
                    "peak": round(r.rss_peak_mb, 1),
                },
            }
            for r in results
        ],
    }
    _write_report(args.out, report)
    # 全部请求失败 = 服务没起来或全超时，退出码要让 CI 能看出来
    return 0 if any(r.completed > 0 for r in results) else 1


def _write_report(out: str, report: dict) -> None:
    os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
    with open(out, "w", encoding="utf-8") as fh:
        json.dump(report, fh, ensure_ascii=False, indent=2)
    print(f"\n报告已写入: {out}")


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
