"""全局配置。所有配置项均可用环境变量覆盖，见 .env.example。

.env 的加载路径锚定到**项目根目录**，而不是当前工作目录：
从 IDE、任务计划、别的目录启动服务时配置不丢 —— 否则
DOC_BACKEND 会静默回落 postgres 默认值，然后 psycopg 在
Windows 上抛出与配置无关的 ProactorEventLoop 错误，极难排查。
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, computed_field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

LLMProvider = Literal["local", "deepseek", "openai", "extractive"]
VectorBackend = Literal["qdrant", "local"]
KeywordBackend = Literal["elasticsearch", "local"]
DocBackend = Literal["postgres", "local"]
ObjectBackend = Literal["minio", "local"]
EmbeddingBackend = Literal["tei", "local", "onnx"]
RerankBackend = Literal["tei", "local", "onnx"]

#: 项目根目录（app/ 的上一级）—— .env 与相对路径配置都以它为基准
PROJECT_ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(PROJECT_ROOT / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    @field_validator(
        "local_model_dir", "local_store_dir", "frontend_dist_dir", mode="before"
    )
    @classmethod
    def _anchor_relative_paths(cls, v: str) -> str:
        """相对路径锚定到项目根 —— 理由同 env_file：启动目录不该改变数据落在哪。

        绝对路径（容器内挂载、外置盘）与 ~ 开头路径原样通过。
        """
        s = str(v)
        if s.startswith("~") or Path(s).is_absolute():
            return s
        return str(PROJECT_ROOT / Path(s))

    # ---------------- 应用 ----------------
    app_env: Literal["dev", "staging", "prod"] = "dev"
    log_level: str = "INFO"
    api_host: str = "0.0.0.0"
    api_port: int = 8088
    api_key: str = "change_me_in_prod"
    #: 是否校验 API Key。开发态关闭方便本地联调；生产必须置 true，
    #: 并由网关 / SSO 接管真实身份（浏览器端的 key 只是最低限度的一道门）。
    api_key_enabled: bool = False
    #: 前端构建产物目录，由 FastAPI 直接托管（单端口内网部署）
    frontend_dist_dir: str = "web/dist"
    #: 启动时初始化索引的超时秒数。超时只告警，不阻断服务启动 ——
    #: 下游暂时不可达时，应用仍要能起来并由 /ready 暴露问题。
    startup_init_timeout: float = 15.0
    #: /ready 单个依赖探针的超时秒数。健康检查必须快 ——
    #: 探针挂一分钟，运维根本等不到结果。
    readiness_probe_timeout: float = 5.0
    #: LLM 探针独立超时。公网生成式 API 的往返延迟普遍高于内网依赖，
    #: 沿用通用 5s 会常态性误报「llm 超时」。
    llm_probe_timeout: float = 15.0

    # ---------------- LLM（可插拔 provider） ----------------
    #: ``extractive`` = 抽取式回答：没有生成模型时，从检索到的原文中
    #: 摘出与问题相关的句子并标注 [n] 出处。不编造、不改写。
    llm_provider: LLMProvider = "local"
    llm_base_url: str = "http://vllm:8000/v1"
    llm_model: str = "qwen3-32b"
    llm_api_key: str = "EMPTY"

    #: 抽取式回答最多摘几句
    extractive_max_sentences: int = 6
    #: 抽取式回答每条依据的最大字数
    extractive_max_sentence_chars: int = 220

    # 升级模型：复杂推理/多跳问题。留空则不启用分级路由
    llm_fallback_provider: str = ""
    llm_fallback_base_url: str = ""
    llm_fallback_model: str = ""
    llm_fallback_api_key: str = ""

    llm_temperature: float = 0.1
    llm_max_tokens: int = 2048
    llm_timeout: float = 120.0
    llm_thinking: bool = False
    #: 生成时携带的最近对话轮数（理解「第2条」「上面那个」这类追问指代）
    llm_history_turns: int = 2

    # ---------------- 内容安全 ----------------
    #: 入口敏感词过滤：命中直接拒绝，不进检索、不调模型（spec RQ-2）
    enable_sensitive_filter: bool = True
    sensitive_words_file: str = "data/sensitive_words.txt"

    # DeepSeek 专用
    deepseek_api_key: str = ""
    deepseek_base_url: str = "https://api.deepseek.com"
    deepseek_peak_hours_utc: str = "1-4,6-10"

    # ---------------- Embedding / Rerank ----------------
    embedding_backend: EmbeddingBackend = "tei"
    embedding_base_url: str = "http://tei-embed:80"
    embedding_model: str = "BAAI/bge-m3"
    embedding_dim: int = 1024
    embedding_batch_size: int = 16
    embedding_timeout: float = 60.0
    embedding_use_query_instruction: bool = True

    rerank_backend: RerankBackend = "tei"
    rerank_base_url: str = "http://tei-rerank:80"
    rerank_model: str = "BAAI/bge-reranker-v2-m3"
    rerank_batch_size: int = 8
    rerank_timeout: float = 60.0

    # ---------------- 本地 ONNX 推理（无 GPU 时的 CPU 路径） ----------------
    #: 本地模型根目录（``scripts/fetch_models.py`` 下载到这里）
    local_model_dir: str = "data/models"
    #: ONNX 量化 embedding 模型
    onnx_embedding_model_file: str = "bge-m3/model_int8.onnx"
    onnx_embedding_tokenizer_file: str = "bge-m3/tokenizer.json"
    #: ONNX 量化 reranker 模型
    onnx_rerank_model_file: str = "bge-reranker-v2-m3/model_int8.onnx"
    onnx_rerank_tokenizer_file: str = "bge-reranker-v2-m3/tokenizer.json"
    #: ONNX 输入截断长度。bge-m3 支持 8192，但 CPU 上没必要拉满。
    onnx_max_seq_length: int = 512
    #: ONNX 推理线程数，0 = 由 onnxruntime 按 CPU 核数自动决定。
    #: 离线单机部署建议显式压低 —— 每个线程都要预分配一块大缓冲区。
    onnx_num_threads: int = 2

    # ---------------- 解析（MinerU 3.x） ----------------
    #: 是否启用 MinerU。没有版面还原服务的部署形态下置 false，
    #: 省掉每次解析都去连一次不存在的服务的等待。
    mineru_enabled: bool = True
    mineru_api_url: str = "http://mineru:8000"
    mineru_backend: str = "pipeline"
    mineru_effort: str = "medium"
    mineru_lang: str = "ch"
    mineru_timeout: float = 600.0
    parser_keep_table_structure: bool = True
    parser_header_footer_threshold: int = 3

    # ---------------- 扫描件 OCR（本地，无 GPU） ----------------
    #: 扫描件 OCR 渲染倍率。原 PDF 是 108dpi 图片，2× 渲染后识别率明显更好。
    ocr_render_scale: float = 2.0
    #: OCR 引擎。rapidocr-onnxruntime 是纯 CPU 的 PaddleOCR 蒸馏版。
    ocr_engine: str = "rapidocr"
    #: 单页 OCR 超时秒数（超时按空页处理，不中断整份文档）
    ocr_page_timeout: float = 120.0

    # ---------------- 存储 ----------------
    #: 存储后端开关。生产默认 qdrant / elasticsearch / postgres / minio；
    #: 离线单机（无容器编排）时切 local，接口完全一致。
    vector_backend: VectorBackend = "qdrant"
    keyword_backend: KeywordBackend = "elasticsearch"
    doc_backend: DocBackend = "postgres"
    object_backend: ObjectBackend = "minio"
    #: 离线后端的数据目录
    local_store_dir: str = "data/local_store"

    postgres_host: str = "postgres"
    postgres_port: int = 5432
    postgres_user: str = "kb"
    postgres_password: str = "kb_change_me"
    postgres_db: str = "kb"

    redis_url: str = "redis://redis:6379/0"

    qdrant_url: str = "http://qdrant:6333"
    qdrant_collection: str = "kb_chunks"
    qdrant_vector_size: int = 1024

    elasticsearch_url: str = "http://elasticsearch:9200"
    elasticsearch_index: str = "kb_chunks"

    minio_endpoint: str = "minio:9000"
    minio_access_key: str = "minioadmin"
    minio_secret_key: str = "minioadmin"
    minio_bucket: str = "kb-docs"
    minio_secure: bool = False

    # ---------------- 检索策略 ----------------
    retrieve_top_k: int = Field(default=50, ge=1, le=1000)
    rerank_top_k: int = Field(default=8, ge=1, le=100)
    rrf_k: int = Field(default=60, ge=1, le=1000)
    child_chunk_tokens: int = Field(default=400, ge=64)
    parent_chunk_tokens: int = Field(default=2000, ge=256)
    child_chunk_overlap: int = Field(default=60, ge=0)
    enable_query_rewrite: bool = True
    #: 精排分数下限 —— reranker 分数已统一 sigmoid 为相关概率 [0,1]。
    #: 0.15 以下视为弱相关（jina-reranker 上对应 logit ≈ -1.7）。
    #: 主要的噪音过滤靠相对比例（rerank_keep_ratio），这里是绝对底线。
    rerank_min_score: float = Field(default=0.15, ge=0.0, le=1.0)
    #: 相对阈值：低于最佳分这个比例的块不进上下文。
    #: 低相关块是幻觉温床 —— 模型会硬从无关条款里「找出」答案。
    #: 1.0 = 关闭相对过滤（全保留）；全被过滤时走「未检索到相关资料」的诚实分支。
    rerank_keep_ratio: float = Field(default=0.4, ge=0.0, le=1.0)
    #: 单个父块进入 prompt 的最大字符数。超长父块稀释关键信息还烧 token。
    context_block_max_chars: int = Field(default=1600, ge=256)

    # ---------------- 权限 ----------------
    acl_enabled: bool = True
    acl_default_allow: bool = False

    # ---------------- 计算属性 ----------------

    @computed_field  # type: ignore[prop-decorator]
    @property
    def postgres_dsn(self) -> str:
        # connect_timeout：数据库不可达时要尽快失败，
        # 否则控制台每个请求都要干等连接超时。
        return (
            f"postgresql://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
            "?connect_timeout=5"
        )

    @property
    def llm_openai_base(self) -> str:
        return self.llm_base_url.rstrip("/")

    @property
    def has_fallback_model(self) -> bool:
        return bool(self.llm_fallback_model and self.llm_fallback_provider)


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
