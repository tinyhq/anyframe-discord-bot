from __future__ import annotations

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    anyframe_base_url: str = "https://api.anyfrm.com"
    # Accept the SDK-standard ANYFRAME_API_KEY, but also the legacy
    # ANYFRAME_API_TOKEN used by earlier deployments of this bot.
    anyframe_api_key: str = Field(
        validation_alias=AliasChoices("ANYFRAME_API_KEY", "ANYFRAME_API_TOKEN"),
    )
    anyframe_agent_id: int

    discord_bot_token: str

    state_db_path: str = "./state.db"

    boot_timeout_s: float = 180.0

    # Discord caps a single message at 2000 chars; leave headroom for
    # code fences and ellipses we may prepend.
    discord_msg_limit: int = 1900

    # New-thread name length cap (Discord limit is 100; keep some headroom).
    thread_name_limit: int = 80

    # 7 days, in minutes — the max auto-archive Discord allows.
    thread_auto_archive_minutes: int = 10080


settings = Settings()
