"""配置与启动校验。

设计依据：L6（.env + 启动校验）、S5（密钥永不进日志）。
"""
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict

# 启动时必须存在的项。缺失直接失败，不要带着空配置跑。
REQUIRED = ("chat_api_key",)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_env: str = "dev"
    database_url: str = "sqlite+aiosqlite:///./data/app.db"

    chat_model: str = "deepseek-chat"
    embed_model: str = "bge-m3"
    chat_api_key: str = ""
    chat_base_url: str = ""

    # 演示用身份：Ch02 还没有登录体系，订单归属校验需要一个固定的 user_id。
    # Ch06 接真实鉴权后，这个值会被请求里的登录身份替换。
    demo_user_id: str = "u1"

    # 向量库：本地 Docker 中的 Chroma（见 docs/reports/local-env.md）
    # 注意集合名必须独立，且建集合时显式 space=cosine（默认 l2 会让证据闸语义反转）
    chroma_host: str = "localhost"
    chroma_port: int = 8000
    chroma_collection: str = "ecom_kb"

    # Embedding：本地 Ollama，bge-m3 输出 1024 维
    ollama_base_url: str = "http://localhost:11434"

    # 检索参数：起步值，未校准。语料 40 个 chunk，调参容易过拟合 45 条评测
    dense_top_k: int = 20
    sparse_top_k: int = 20
    rrf_k: int = 60
    # 余弦相似度下限。实测：可答题最低 0.566、拒答题最高 0.500，取 0.55
    # 已知漏洞：域外问题「能分期付款吗」0.572 会越过——单阈值分不干净，见 app/core/gate.py
    evidence_threshold: float = 0.55

    # 检索策略：dense / bm25 / hybrid。默认值由 Task 5 的实验决定，
    # 见 docs/reports/retrieval-eval.md
    retrieval_strategy: str = "dense"

    prompt_version: str = "v1"
    kb_version: str = ""          # 运行时由知识库内容哈希填充

    # 观测（可选）
    langfuse_public_key: str = ""
    langfuse_secret_key: str = ""
    langfuse_base_url: str = ""

    def missing(self) -> list[str]:
        return [k for k in REQUIRED if not getattr(self, k)]

    def safe_summary(self) -> dict:
        """只暴露非敏感项；密钥一律 <set>/<unset>。"""
        return {
            "env": self.app_env,
            "chat_model": self.chat_model,
            "embed_model": self.embed_model,
            "prompt_version": self.prompt_version,
            "kb_version": self.kb_version,
            "chat_api_key": "<set>" if self.chat_api_key else "<unset>",
            "langfuse": "on" if all(
                (self.langfuse_public_key, self.langfuse_secret_key, self.langfuse_base_url)
            ) else "off",
        }


@lru_cache
def get_settings() -> Settings:
    return Settings()


def validate_or_die() -> None:
    s = get_settings()
    missing = s.missing()
    if missing:
        raise SystemExit(
            "配置缺失: " + ", ".join(missing) + "\n"
            "请复制 .env.example 为 .env 并填写。"
        )
