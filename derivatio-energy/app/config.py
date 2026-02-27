from pydantic_settings import BaseSettings
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

class Settings(BaseSettings):
    supabase_url: str
    supabase_key: str
    supabase_service_key: str
    entsoe_api_token: str = ""
    database_url: str = ""
    supabase_service_role_key: str = ""
    environment: str = "development"

    class Config:
        env_file = str(BASE_DIR / ".env")
        extra = "ignore"

settings = Settings()
