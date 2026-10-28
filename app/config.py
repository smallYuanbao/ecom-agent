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
