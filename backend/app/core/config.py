from functools import lru_cache

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_env: str = "development"
    database_url: str = "postgresql+asyncpg://commerce:commerce@localhost:5432/commerce"
    model_provider: str = "mock"
    model_name: str = "mock-commerce-agent"
    model_base_url: str = "https://api.openai.com/v1"
    model_api_key: SecretStr | None = None
    model_timeout_seconds: float = Field(default=30.0, gt=0)
    agent_total_timeout_seconds: float = Field(default=45.0, gt=0)
    agent_tool_timeout_seconds: float = Field(default=10.0, gt=0)
    agent_max_model_loops: int = Field(default=6, ge=1, le=20)
    agent_max_tool_calls: int = Field(default=8, ge=1, le=50)
    agent_history_limit: int = Field(default=50, ge=4, le=500)
    agent_tool_result_max_chars: int = Field(default=12_000, ge=256, le=100_000)

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()
