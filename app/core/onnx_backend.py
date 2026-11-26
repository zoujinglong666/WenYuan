"""本地 ONNX 推理后端（纯 CPU，无容器、无 GPU）。

为什么需要它
------------
生产路径是 TEI（text-embeddings-inference）容器 + GPU。但内网交付常常
是「一台机器、一份手册」：没有容器编排、没有 GPU，甚至没有出网权限。
这时候检索链路必须**真的跑起来**才能压测、才能评估。

模型本身是**同一款** bge-m3 / bge-reranker-v2-m3，只是换成了 int8 量化
的 ONNX 导出（542MB vs 2.1GB fp32），CPU 上快一个量级，
检索质量损失在评估集上可测（见 scripts/eval.py）。

与 TEI 的差异
------------
- TEI 走 HTTP，ONNX 在进程内 —— 省掉一次网络往返，也省掉一个容器
- int8 量化：向量有微小偏差，评估集上会体现出来，不藏着
- 线程数由 onnxruntime 按核数自动决定，可用 ``ONNX_NUM_THREADS`` 覆盖
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import numpy as np

from app.config import settings

logger = logging.getLogger(__name__)


def _resolve(path_or_rel: str, base: str | None = None) -> Path:
    """解析模型路径：相对 ``LOCAL_MODEL_DIR``，或直接给绝对路径。"""
    p = Path(path_or_rel)
    if p.is_absolute():
        return p
    root = Path(base or settings.local_model_dir)
    return root / p


class _OnnxModel:
    """ONNX 会话 + 分词器的公共壳。

    输入名与输出名在运行时探查 —— 不同导出版本的签名不一样
    （bge-m3 只要 input_ids/attention_mask，reranker 还要 token_type_ids），
    写死名字迟早对不上某个版本。
    """

    def __init__(
        self,
        model_path: Path,
        tokenizer_path: Path,
        *,
        max_length: int,
        num_threads: int = 0,
    ) -> None:
        import onnxruntime as ort
        from tokenizers import Tokenizer

        if not model_path.exists():
            raise FileNotFoundError(
                f"ONNX 模型不存在: {model_path}\n"
                f"先运行: HF_ENDPOINT=https://hf-mirror.com python -m scripts.fetch_models"
            )
        if not tokenizer_path.exists():
            raise FileNotFoundError(f"分词器不存在: {tokenizer_path}")

        so = ort.SessionOptions()
        if num_threads > 0:
            so.intra_op_num_threads = num_threads
            so.inter_op_num_threads = 1
        self.session = ort.InferenceSession(
            str(model_path), sess_options=so, providers=["CPUExecutionProvider"]
        )
        self.input_names = {i.name for i in self.session.get_inputs()}
        self.output_names = [o.name for o in self.session.get_outputs()]

        self.tokenizer = Tokenizer.from_file(str(tokenizer_path))
        self.tokenizer.enable_truncation(max_length=max_length)
        pad_id = self.tokenizer.token_to_id("<pad>")
        self.pad_id = pad_id if pad_id is not None else 0
        self.tokenizer.enable_padding(pad_id=self.pad_id, pad_token="<pad>")

        logger.info(
            "ONNX 模型已加载: %s（输入 %s，输出 %s）",
            model_path.name,
            sorted(self.input_names),
            self.output_names,
        )

    def encode_batch(
        self, texts: list[str], pairs: list[str] | None = None
    ) -> dict[str, np.ndarray]:
        """分词 → 张量。返回的字典键与模型声明的输入名对齐。

        ``pairs`` 与 texts 等长：给交叉编码器用（XLMRoberta 的 pair
        编码由 post_processor 模板控制，见 :class:`OnnxReranker`）。
        """
        encodings = self.tokenizer.encode_batch(texts)
        input_ids = np.array([e.ids for e in encodings], dtype=np.int64)
        attention_mask = np.array([e.attention_mask for e in encodings], dtype=np.int64)

        if pairs is not None:
            # 版本兼容：部分 tokenizers 的 encode_batch 不支持 pairs 参数，
            # 逐条 encode(text, pair=...) 走同一个 post_processor 模板；
            # 逐条编码时 tokenizer 级 padding 不生效，手动补齐到等长
            encs = [self.tokenizer.encode(t, pair=p) for t, p in zip(texts, pairs)]
            max_len = max(len(e.ids) for e in encs)
            input_ids = np.array(
                [e.ids + [self.pad_id] * (max_len - len(e.ids)) for e in encs], dtype=np.int64
            )
            attention_mask = np.array(
                [e.attention_mask + [0] * (max_len - len(e.ids)) for e in encs], dtype=np.int64
            )

        feed: dict[str, np.ndarray] = {
            "input_ids": input_ids,
            "attention_mask": attention_mask,
        }
        if "token_type_ids" in self.input_names:
            feed["token_type_ids"] = np.zeros_like(input_ids)
        # 只喂模型真的声明了的输入 —— 多喂会报错
        return {k: v for k, v in feed.items() if k in self.input_names}


class OnnxEmbedder:
    """bge-m3 稠密向量（CLS 池化 + L2 归一化）。

    bge-m3 官方用法：query 侧加检索指令前缀，passage 侧不加。
    前缀在 :class:`app.core.embeddings.EmbeddingService` 里统一处理，
    这里只负责编码。
    """

    def __init__(self) -> None:
        self._model: _OnnxModel | None = None

    @property
    def model(self) -> _OnnxModel:
        if self._model is None:
            self._model = _OnnxModel(
                _resolve(settings.onnx_embedding_model_file),
                _resolve(settings.onnx_embedding_tokenizer_file),
                max_length=settings.onnx_max_seq_length,
                num_threads=settings.onnx_num_threads,
            )
        return self._model

    def encode(self, texts: list[str]) -> np.ndarray:
        """返回 [N, dim]，每行已 L2 归一化。"""
        if not texts:
            return np.zeros((0, settings.embedding_dim), dtype=np.float32)

        model = self.model
        out: list[np.ndarray] = []
        batch_size = max(1, settings.embedding_batch_size)
        for start in range(0, len(texts), batch_size):
            batch = texts[start : start + batch_size]
            feed = model.encode_batch(batch)
            outputs = model.session.run(None, feed)
            # 优先用 sentence_embedding / pooler_output，否则退回 CLS
            hidden = outputs[0]
            if hidden.ndim == 3:
                hidden = hidden[:, 0, :]  # CLS
            out.append(np.asarray(hidden, dtype=np.float32))

        vectors = np.vstack(out)
        norms = np.linalg.norm(vectors, axis=1, keepdims=True)
        norms[norms == 0] = 1.0
        return vectors / norms


class OnnxReranker:
    """bge-reranker-v2-m3 交叉编码器。

    输入是 (query, doc) 对，输出一个 logit —— 越大越相关。
    分数是 raw logit，只用于相对排序。

    ``model_file`` / ``tokenizer_file`` 可覆盖 —— 评估时要对比
    不同 reranker 的效果，不能把模型路径写死在配置里。
    """

    def __init__(
        self,
        model_file: str | None = None,
        tokenizer_file: str | None = None,
    ) -> None:
        self._model_file = model_file or settings.onnx_rerank_model_file
        self._tokenizer_file = tokenizer_file or settings.onnx_rerank_tokenizer_file
        self._model: _OnnxModel | None = None

    @property
    def model(self) -> _OnnxModel:
        if self._model is None:
            self._model = _OnnxModel(
                _resolve(self._model_file),
                _resolve(self._tokenizer_file),
                max_length=settings.onnx_max_seq_length,
                num_threads=settings.onnx_num_threads,
            )
            self._install_pair_template()
        return self._model

    def _install_pair_template(self) -> None:
        """给 tokenizer 装上 XLMRoberta 的 (query, doc) 编码模板。

        bge-reranker-v2-m3 训练时的 pair 输入是 `<s> query </s></s> doc </s>`。
        tokenizers 库 from_file 加载的裸 tokenizer 对 (query, doc) 编码时
        缺少 query 后的 `</s></s>` 双分隔符 —— query 和 doc 直接粘连成
        模型从未见过的输入分布，打分全负且语义颠倒（实测：体检问句对
        体检段落的分比婚假段落还低）。必须在加载后显式装模板。
        """
        from tokenizers.processors import TemplateProcessing

        tok = self._model.tokenizer  # type: ignore[union-attr]
        cls_id = tok.token_to_id("<s>")
        sep_id = tok.token_to_id("</s>")
        if cls_id is None or sep_id is None:
            logger.warning("tokenizer 缺少 <s>/</s> 特殊符号，pair 模板未安装")
            return
        tok.post_processor = TemplateProcessing(
            single="<s> $A </s>",
            pair="<s> $A </s> </s> $B:1 </s>",
            special_tokens=[("<s>", cls_id), ("</s>", sep_id)],
        )

    def compute(self, query: str, documents: list[str]) -> list[float]:
        if not documents:
            return []

        model = self.model

        scores: list[float] = []
        batch_size = max(1, settings.rerank_batch_size)
        for start in range(0, len(documents), batch_size):
            batch = documents[start : start + batch_size]
            feed = model.encode_batch([query] * len(batch), pairs=batch)
            outputs = model.session.run(None, feed)
            logits = np.asarray(outputs[0], dtype=np.float32)
            if logits.ndim == 2 and logits.shape[1] == 1:
                logits = logits[:, 0]
            elif logits.ndim == 3:
                # 序列分类头输出：取 CLS 位置
                logits = logits[:, 0, 0]
            scores.extend(float(x) for x in logits.tolist())
        return scores

    def rank(self, query: str, documents: list[str], top_n: int = 8) -> list[tuple[int, float]]:
        """返回 [(原始下标, 分数)]，按分数降序。"""
        scores = self.compute(query, documents)
        ranked = sorted(enumerate(scores), key=lambda x: x[1], reverse=True)
        return [(int(i), float(s)) for i, s in ranked[:top_n]]


_embedder: OnnxEmbedder | None = None
_reranker: OnnxReranker | None = None


def get_onnx_embedder() -> OnnxEmbedder:
    global _embedder
    if _embedder is None:
        _embedder = OnnxEmbedder()
    return _embedder


def get_onnx_reranker() -> OnnxReranker:
    global _reranker
    if _reranker is None:
        _reranker = OnnxReranker()
    return _reranker


def warmup() -> dict[str, Any]:
    """预热：把两个模型都加载并跑一次，暴露"模型加载失败"这类硬错误。"""
    emb = get_onnx_embedder().encode(["预热测试"])
    rr = get_onnx_reranker().compute("预热测试", ["预热文档"])
    return {"embedding_dim": int(emb.shape[1]), "rerank_score": rr[0] if rr else None}
