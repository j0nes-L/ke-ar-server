from pydantic_settings import BaseSettings
from functools import lru_cache


class Settings(BaseSettings):
    API_KEY: str
    MASTER_PASSWORD: str
    DEBUG: bool = False

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"


@lru_cache
def get_settings() -> Settings:
    return Settings()
