"""下载离线推理所需模型（hf-mirror 镜像）。

背景：本机无 GPU、无 Docker，生产路径依赖 TEI / vLLM 容器。为了在纯 CPU
环境下做**真实**的全链路压测，这里拉取 ONNX 量化版模型：

- ``Xenova/bge-m3`` 的 ``onnx/model_int8.onnx`` —— 与生产同款 bge-m3，
  int8 量化后 542MB，CPU 上比 fp32 快一个量级，检索质量损失可忽略。
- ``Xenova/bge-reranker-v2-m3`` 的 ``onnx/model_int8.onnx`` —— 同款交叉编码器。

FP32 版 bge-m3（2.1GB）在开发期被出口配额限流，量化版是等效替代。

用法::

    HF_ENDPOINT=https://hf-mirror.com python -m scripts.fetch_models
"""

from __future__ import annotations

import os
import sys
import time

os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")

from huggingface_hub import hf_hub_download  # noqa: E402

# (repo_id, filename, 本地目录名)
TARGETS: list[tuple[str, str, str]] = [
    # ---- bge-m3：稠密向量（检索用） ----
    ("Xenova/bge-m3", "onnx/model_int8.onnx", "bge-m3"),
    ("Xenova/bge-m3", "tokenizer.json", "bge-m3"),
    ("Xenova/bge-m3", "tokenizer_config.json", "bge-m3"),
    ("Xenova/bge-m3", "special_tokens_map.json", "bge-m3"),
    ("Xenova/bge-m3", "sentencepiece.bpe.model", "bge-m3"),
    ("Xenova/bge-m3", "config.json", "bge-m3"),
    # ---- reranker 候选 A：jina-reranker-v2-base-multilingual（多语言，强） ----
    ("jinaai/jina-reranker-v2-base-multilingual", "onnx/model_int8.onnx", "jina-reranker-v2-base-multilingual"),
    ("jinaai/jina-reranker-v2-base-multilingual", "tokenizer.json", "jina-reranker-v2-base-multilingual"),
    ("jinaai/jina-reranker-v2-base-multilingual", "tokenizer_config.json", "jina-reranker-v2-base-multilingual"),
    ("jinaai/jina-reranker-v2-base-multilingual", "special_tokens_map.json", "jina-reranker-v2-base-multilingual"),
    ("jinaai/jina-reranker-v2-base-multilingual", "config.json", "jina-reranker-v2-base-multilingual"),
    # ---- reranker 候选 B：bge-reranker-base（bge 系，中文强） ----
    ("Xenova/bge-reranker-base", "onnx/model_int8.onnx", "bge-reranker-base"),
    ("Xenova/bge-reranker-base", "tokenizer.json", "bge-reranker-base"),
    ("Xenova/bge-reranker-base", "tokenizer_config.json", "bge-reranker-base"),
    ("Xenova/bge-reranker-base", "special_tokens_map.json", "bge-reranker-base"),
    ("Xenova/bge-reranker-base", "sentencepiece.bpe.model", "bge-reranker-base"),
    ("Xenova/bge-reranker-base", "config.json", "bge-reranker-base"),
]

LOCAL_ROOT = os.path.join("data", "models")


def main() -> int:
    os.makedirs(LOCAL_ROOT, exist_ok=True)
    failures: list[tuple[str, str]] = []

    for repo, filename, subdir in TARGETS:
        dest_dir = os.path.join(LOCAL_ROOT, subdir)
        os.makedirs(dest_dir, exist_ok=True)
        # 已经是最终文件（不是 .incomplete 缓存）就跳过
        final = os.path.join(dest_dir, os.path.basename(filename))
        if os.path.exists(final) and os.path.getsize(final) > 1024:
            print(f"[skip] {repo}/{filename} -> {final}", flush=True)
            continue

        t0 = time.time()
        try:
            cached = hf_hub_download(repo_id=repo, filename=filename, cache_dir=os.path.join(LOCAL_ROOT, "_hf"))
        except Exception as exc:  # noqa: BLE001
            print(f"[FAIL] {repo}/{filename}: {type(exc).__name__}: {exc}", flush=True)
            failures.append((repo, filename))
            continue

        # hf_hub_download 返回 symlink 指向 blobs/ 实体文件；复制一份到稳定路径
        real = os.path.realpath(cached)
        import shutil

        shutil.copyfile(real, final)
        size_mb = os.path.getsize(final) / 1024 / 1024
        print(f"[ok]   {repo}/{filename} -> {final}  {size_mb:.1f}MB  {time.time() - t0:.1f}s", flush=True)

    if failures:
        print(f"\n{len(failures)} 个文件下载失败:", flush=True)
        for repo, filename in failures:
            print(f"  - {repo}/{filename}", flush=True)
        return 1

    print("\n全部模型就绪:", LOCAL_ROOT, flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
