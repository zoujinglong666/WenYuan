"""真实文档摄入 —— 走生产管线，只是后端换成离线单机实现。

不是"造一批假 chunk"，而是把用户给的真实 PDF 原样喂进
:class:`app.ingestion.pipeline.IngestionPipeline`：解析 → 切块 → 向量化 → 双路入库。

用法::

    python -m scripts.ingest_real <文件路径> [--acl public] [--parser scanned]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.config import settings  # noqa: E402
from app.ingestion.pipeline import IngestionPipeline  # noqa: E402


async def main() -> int:
    ap = argparse.ArgumentParser(description="真实文档摄入")
    ap.add_argument("path", help="待摄入的文件路径")
    ap.add_argument("--acl", default="public", help="可见主体，逗号分隔")
    ap.add_argument("--parser", default=None, help="强制解析器（mineru/office/scanned）")
    ap.add_argument("--force", action="store_true", help="内容未变也重新摄入")
    args = ap.parse_args()

    if not os.path.exists(args.path):
        print(f"文件不存在: {args.path}")
        return 2

    with open(args.path, "rb") as fh:
        data = fh.read()

    filename = os.path.basename(args.path)
    acl = [a.strip() for a in args.acl.split(",") if a.strip()] or ["public"]

    print(f"文件      : {filename}")
    print(f"大小      : {len(data) / 1024 / 1024:.2f} MB")
    print(f"解析器    : {args.parser or '自动'}")
    print(f"向量后端  : {settings.vector_backend}")
    print(f"关键词后端: {settings.keyword_backend}")
    print(f"文档库    : {settings.doc_backend}")
    print(f"embedding : {settings.embedding_backend} / {settings.embedding_model}")
    print(f"rerank    : {settings.rerank_backend} / {settings.rerank_model}")
    print(f"LLM       : {settings.llm_provider}")
    print("-" * 68)

    pipeline = IngestionPipeline()
    t0 = time.perf_counter()
    result = await pipeline.ingest(
        data,
        filename,
        acl_ids=acl,
        prefer_parser=args.parser,
        force=args.force,
    )
    total = (time.perf_counter() - t0) * 1000

    print(json.dumps(
        {
            "doc_id": result.doc_id,
            "filename": result.filename,
            "status": result.status,
            "chunk_count": result.chunk_count,
            "parser": result.parser,
            "error": result.error,
            "timings_ms": {k: round(v, 1) for k, v in result.timings.items()},
            "total_ms": round(total, 1),
        },
        ensure_ascii=False,
        indent=2,
    ))
    return 0 if result.status == "ready" else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
