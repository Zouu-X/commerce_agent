from decimal import Decimal
from functools import lru_cache
from pathlib import Path

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_env: str = "development"
    database_url: str = "postgresql+asyncpg://commerce:commerce@localhost:5432/commerce"
    model_provider: str = "deepseek"
    model_name: str = "deepseek-v4-flash"
    model_base_url: str = "https://api.deepseek.com"
    model_api_key: SecretStr | None = None
    model_api_key_file: str | None = None
    model_input_cost_per_million: Decimal = Field(default=Decimal("0.14"), ge=0)
    model_output_cost_per_million: Decimal = Field(default=Decimal("0.28"), ge=0)
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

    def resolved_model_api_key(self) -> SecretStr | None:
        if self.model_api_key is not None:
            direct_value = self.model_api_key.get_secret_value().strip()
            if direct_value:
                return SecretStr(direct_value)
        if self.model_api_key_file is None:
            return None
        try:
            raw_value = Path(self.model_api_key_file).read_text(encoding="utf-8").strip()
        except OSError:
            return None
        if not raw_value:
            return None
        # Support both a raw Docker secret and a conventional KEY=value file.
        if raw_value.startswith("MODEL_API_KEY="):
            raw_value = raw_value.partition("=")[2].strip()
        return SecretStr(raw_value) if raw_value else None


@lru_cache
def get_settings() -> Settings:
    return Settings()
