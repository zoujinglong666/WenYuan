"""Embedding 服务。

内网默认走 TEI (text-embeddings-inference) 的 OpenAI 兼容端点；
local 后端用于无 GPU 的开发机，通过 sentence-transformers 在进程内加载模型。
"""

from __future__ import annotations

import asyncio
import logging
from functools import lru_cache

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

# bge 系列官方检索指令：query 侧加前缀，passage 侧不加。
# 漏掉这行会明显掉点，是最常见的 embedding 用法错误。
BGE_QUERY_INSTRUCTION = "Represent this sentence for searching relevant passages: "


class EmbeddingService:
    """统一的 embedding 接口，对上层屏蔽后端差异。"""

    def __init__(self) -> None:
        self._local_model = None
        self._client: httpx.AsyncClient | None = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=settings.embedding_base_url.rstrip("/"),
                timeout=settings.embedding_timeout,
            )
        return self._client

    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        """批量向量化文本。空列表直接返回，避免无谓的网络往返。

        内部按 ``embedding_batch_size`` 切批，避免单次请求过大被 TEI 拒绝。
        """
        if not texts:
            return []
        if settings.embedding_backend == "onnx":
            return await asyncio.to_thread(self._embed_onnx, texts)
        if settings.embedding_backend == "local":
            return await asyncio.to_thread(self._embed_local, texts)

        vectors: list[list[float]] = []
        batch_size = max(1, settings.embedding_batch_size)
        for start in range(0, len(texts), batch_size):
            batch = texts[start : start + batch_size]
            vectors.extend(await self._embed_tei(batch))
        return vectors

    async def embed_query(self, text: str) -> list[float]:
        query = text
        if settings.embedding_use_query_instruction:
            query = f"{BGE_QUERY_INSTRUCTION}{text}"
        vectors = await self.embed_documents([query])
        return vectors[0] if vectors else [0.0] * settings.embedding_dim

    # ---------------- TEI ----------------

    async def _embed_tei(self, texts: list[str]) -> list[list[float]]:
        client = await self._get_client()
        payload = {
            "model": settings.embedding_model,
            "input": texts,
            "encoding_format": "float",
        }
        last_exc: Exception | None = None
        for attempt in range(3):
            try:
                resp = await client.post("/v1/embeddings", json=payload)
                resp.raise_for_status()
                data = resp.json()
                # OpenAI 兼容格式：按 index 排序返回
                items = sorted(data["data"], key=lambda d: d["index"])
                return [item["embedding"] for item in items]
            except Exception as exc:  # noqa: BLE001
                last_exc = exc
                logger.warning("embedding 调用失败，第 %s 次重试: %s", attempt + 1, exc)
                await asyncio.sleep(1.5 * (attempt + 1))
        raise RuntimeError(f"embedding 服务不可用: {last_exc}")

    # ---------------- local ----------------

    def _embed_onnx(self, texts: list[str]) -> list[list[float]]:
        """ONNX Runtime（int8 量化，进程内 CPU 推理）。"""
        from app.core.onnx_backend import get_onnx_embedder

        vectors = get_onnx_embedder().encode(texts)
        return [vec.tolist() for vec in vectors]

    def _embed_local(self, texts: list[str]) -> list[list[float]]:
        model = self._get_local_model()
        # bge-m3 官方用法：query 加 instruction 前缀，passage 不加
        encoded = model.encode(
            texts,
            batch_size=settings.embedding_batch_size,
            normalize_embeddings=True,
            show_progress_bar=False,
        )
        return [vec.tolist() for vec in encoded]

    def _get_local_model(self):
        if self._local_model is None:
            from sentence_transformers import SentenceTransformer

            logger.info("加载本地 embedding 模型: %s", settings.embedding_model)
            self._local_model = SentenceTransformer(settings.embedding_model)
        return self._local_model


@lru_cache
def get_embedding_service() -> EmbeddingService:
    return EmbeddingService()
