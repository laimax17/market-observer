"""Runtime settings, loaded from environment / .env (prefix ``MO_``).

Secrets (API keys, webhook URLs) must come from the environment, never be
hardcoded. See ``.env.example`` for the full list.
"""

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="MO_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # LLM (DeepSeek, OpenAI-compatible). Optional so the data-only path and
    # the test suite can run without credentials.
    deepseek_api_key: str | None = None
    deepseek_base_url: str = "https://api.deepseek.com"
    deepseek_model: str = "deepseek-chat"

    # Discord
    discord_webhook_url: str | None = None

    # Behaviour
    pinned_symbols: str = "SPY,QQQ"
    watchlist_size: int = 10
    llm_temperature: float = 0.2
    llm_timeout_seconds: float = 60.0
    llm_max_retries: int = 2
    output_dir: str = "output"

    @property
    def pinned_symbol_list(self) -> list[str]:
        return [s.strip().upper() for s in self.pinned_symbols.split(",") if s.strip()]


def load_settings() -> Settings:
    """Load settings from the environment. Kept as a function so tests can
    construct ``Settings`` with explicit overrides instead."""
    return Settings()
