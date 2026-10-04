from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    anthropic_api_key: str = ""
    chat_model: str = "claude-sonnet-4-5"
    triage_model: str = "claude-haiku-4-5"

    twilio_account_sid: str = ""
    twilio_auth_token: str = ""
    twilio_from_number: str = ""
    user_phone_number: str = ""
    public_url: str = "http://localhost:8000"

    timezone: str = "Europe/Rome"
    quiet_hours_start: int = 23
    quiet_hours_end: int = 8
    max_calls_per_day: int = 3
    call_urgency_threshold: int = 8

    language: str = "it-IT"
    watch_interval_seconds: int = 120
    db_path: str = "jarvis.db"


@lru_cache
def get_settings() -> Settings:
    return Settings()
