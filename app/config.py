import os
from pathlib import Path
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parents[1]  # project root (flooriq-nepal)
ENV_FILE = ROOT / ".env"

# In production (e.g., Railway), rely on real environment variables.
# Only load .env locally when APP_ENV != "prod".
_ENV_FILE = str(ENV_FILE) if (os.getenv("APP_ENV", "dev") != "prod" and ENV_FILE.exists()) else None


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=_ENV_FILE, extra="ignore")

    app_env: str = Field(default="dev", validation_alias="APP_ENV")
    tz: str = Field(default="Asia/Kathmandu", validation_alias="TZ")

    supabase_db_host: str = Field(default="", validation_alias="SUPABASE_DB_HOST")
    supabase_db_port: int = Field(default=6543, validation_alias="SUPABASE_DB_PORT")
    supabase_db_name: str = Field(default="postgres", validation_alias="SUPABASE_DB_NAME")
    supabase_db_user: str = Field(default="", validation_alias="SUPABASE_DB_USER")
    supabase_db_password: str = Field(default="", validation_alias="SUPABASE_DB_PASSWORD")
    supabase_db_sslmode: str = Field(default="require", validation_alias="SUPABASE_DB_SSLMODE")
    redis_url: str | None = Field(default=None, validation_alias="REDIS_URL")

    # Demo hardening
    dashboard_basic_auth: bool = Field(default=False, validation_alias="DASHBOARD_BASIC_AUTH")
    dashboard_username: str = Field(default="", validation_alias="DASHBOARD_USERNAME")
    dashboard_password: str = Field(default="", validation_alias="DASHBOARD_PASSWORD")

    rate_limit_enabled: bool = Field(default=True, validation_alias="RATE_LIMIT_ENABLED")
    rate_limit_per_minute: int = Field(default=60, validation_alias="RATE_LIMIT_PER_MINUTE")

    github_token: str = Field(default="", validation_alias="GITHUB_TOKEN")
    github_repo: str = Field(default="GaneshPokharel-tech/flooriq-nepal", validation_alias="GITHUB_REPO")

    # Broker auth (Phase 1: no DB)
    broker_auth_enabled: bool = Field(default=False, validation_alias="BROKER_AUTH_ENABLED")
    broker_auth_secret: str = Field(default="", validation_alias="BROKER_AUTH_SECRET")
    broker_session_ttl_seconds: int = Field(default=7 * 24 * 3600, validation_alias="BROKER_SESSION_TTL_SECONDS")
    broker_cookie_name: str = Field(default="flooriq_broker", validation_alias="BROKER_COOKIE_NAME")
    broker_allowlist_json: str = Field(default="", validation_alias="BROKER_ALLOWLIST_JSON")
    broker_default_plan: str = Field(default="free", validation_alias="BROKER_DEFAULT_PLAN")

    # Plan-based daily quota (Free: FREE_DAILY_LIMIT/day, Paid: unlimited)
    plan_rate_limit_enabled: bool = Field(default=False, validation_alias="PLAN_RATE_LIMIT_ENABLED")
    free_daily_limit: int = Field(default=10, validation_alias="FREE_DAILY_LIMIT")

    # WhatsApp alerts (Phase 1: dry-run logging unless provider configured)
    whatsapp_provider: str = Field(default="dryrun", validation_alias="WHATSAPP_PROVIDER")
    whatsapp_from: str = Field(default="", validation_alias="WHATSAPP_FROM")
    whatsapp_token: str = Field(default="", validation_alias="WHATSAPP_TOKEN")


settings = Settings()
