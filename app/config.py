from pathlib import Path
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parents[1]  # project root (flooriq-nepal)
ENV_FILE = ROOT / ".env"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=str(ENV_FILE), extra="ignore")

    app_env: str = Field(default="dev", validation_alias="APP_ENV")
    tz: str = Field(default="Asia/Kathmandu", validation_alias="TZ")

    supabase_db_host: str = Field(default="", validation_alias="SUPABASE_DB_HOST")
    supabase_db_port: int = Field(default=5432, validation_alias="SUPABASE_DB_PORT")
    supabase_db_name: str = Field(default="postgres", validation_alias="SUPABASE_DB_NAME")
    supabase_db_user: str = Field(default="postgres", validation_alias="SUPABASE_DB_USER")
    supabase_db_password: str = Field(default="", validation_alias="SUPABASE_DB_PASSWORD")
    supabase_db_sslmode: str = Field(default="require", validation_alias="SUPABASE_DB_SSLMODE")


settings = Settings()
