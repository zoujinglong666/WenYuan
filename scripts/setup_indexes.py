"""初始化索引：Qdrant collection + ES index + PostgreSQL 表。"""

from __future__ import annotations

import asyncio
import logging

from app.indexing.es_store import ESStore
from app.indexing.qdrant_store import QdrantStore
from app.storage.doc_repo import DocumentRepository

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger(__name__)


async def main() -> None:
    qdrant = QdrantStore()
    await qdrant.ensure_collection()
    logger.info("Qdrant collection 就绪: %s", qdrant.collection)

    es = ESStore()
    await es.ensure_index()
    logger.info("ES 索引就绪: %s", es.index)
    await es.close()

    await DocumentRepository().init_schema()
    logger.info("PostgreSQL 表结构就绪")


if __name__ == "__main__":
    asyncio.run(main())
