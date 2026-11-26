"""命令行导入文档。

用法::

    python -m scripts.ingest --path ./docs/handbook.pdf
    python -m scripts.ingest --path ./docs/ --acl public,dept:finance
    python -m scripts.ingest --path ./docs/ --recursive --prefer mineru
"""

from __future__ import annotations

import argparse
import asyncio
import logging
from pathlib import Path

from app.ingestion.pipeline import IngestionPipeline

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger(__name__)

SUPPORTED = {".pdf", ".docx", ".pptx", ".xlsx", ".md", ".txt", ".csv", ".json", ".html"}


async def ingest_path(
    pipeline: IngestionPipeline,
    path: Path,
    acl_ids: list[str],
    prefer_parser: str | None,
    force: bool,
) -> None:
    files = [path] if path.is_file() else sorted(path.rglob("*"))

    for file in files:
        if not file.is_file() or file.suffix.lower() not in SUPPORTED:
            continue
        data = file.read_bytes()
        logger.info("摄入 %s (%d bytes)", file.name, len(data))
        result = await pipeline.ingest(
            data,
            file.name,
            acl_ids=acl_ids,
            prefer_parser=prefer_parser,
            force=force,
        )
        logger.info(
            "  → %s: status=%s chunks=%d parser=%s%s",
            result.doc_id,
            result.status,
            result.chunk_count,
            result.parser,
            " (内容未变，已跳过)" if result.skipped else "",
        )
        if result.error:
            logger.error("  ! %s", result.error)


def main() -> None:
    parser = argparse.ArgumentParser(description="企业知识库文档导入")
    parser.add_argument("--path", required=True, help="文件或目录路径")
    parser.add_argument(
        "--acl",
        default="public",
        help="逗号分隔的可见主体，默认 public",
    )
    parser.add_argument(
        "--prefer",
        choices=["mineru", "office"],
        default=None,
        help="强制使用指定解析器",
    )
    parser.add_argument("--force", action="store_true", help="强制重新解析（忽略内容 hash）")
    args = parser.parse_args()

    path = Path(args.path)
    if not path.exists():
        logger.error("路径不存在: %s", path)
        raise SystemExit(1)

    acl_ids = [a.strip() for a in args.acl.split(",") if a.strip()] or ["public"]

    pipeline = IngestionPipeline()
    asyncio.run(
        ingest_path(pipeline, path, acl_ids, args.prefer, args.force)
    )


if __name__ == "__main__":
    main()
