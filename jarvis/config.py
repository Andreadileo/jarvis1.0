from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


def _csv(raw: str) -> list[str]:
    return [item.strip() for item in raw.split(",") if item.strip()]


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
    verify_twilio_signature: bool = True

    # Canale non urgente
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""

    # Fonti
    calendar_ics_urls: str = ""          # uno o più URL iCal separati da virgola
    calendar_lookahead_minutes: int = 45
    imap_host: str = ""
    imap_user: str = ""
    imap_password: str = ""
    imap_folder: str = "INBOX"
    imap_vip_senders: str = ""           # indirizzi o domini separati da virgola
    imap_keywords: str = ""              # parole chiave separate da virgola
    inbound_token: str = ""              # protegge POST /events

    # Guardrail
    timezone: str = "Europe/Rome"
    quiet_hours_start: int = 23
    quiet_hours_end: int = 8
    max_calls_per_day: int = 3
    call_urgency_threshold: int = 8
    max_notifications_per_hour: int = 4

    # Iniziativa
    briefing_enabled: bool = True
    briefing_hour: int = 8

    language: str = "it-IT"
    watch_interval_seconds: int = 120
    db_path: str = "jarvis.db"

    @property
    def ics_urls(self) -> list[str]:
        return _csv(self.calendar_ics_urls)

    @property
    def vip_senders(self) -> list[str]:
        return [s.lower() for s in _csv(self.imap_vip_senders)]

    @property
    def keywords(self) -> list[str]:
        return [k.lower() for k in _csv(self.imap_keywords)]


@lru_cache
def get_settings() -> Settings:
    return Settings()
