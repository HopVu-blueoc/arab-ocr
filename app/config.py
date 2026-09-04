from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    data_dir: Path = Path("data")
    redis_url: str = "redis://localhost:6379/0"
    job_backend: str = "inline"  # inline | celery
    ocr_device: str = "cpu"
    ocr_lang: str = "ar"
    ocr_version: str = "PP-OCRv5"
    ocr_rec_model: str = "arabic_PP-OCRv5_mobile_rec"
    ocr_det_model: str = "PP-OCRv5_mobile_det"
    reviewer_name: str = "reviewer"

    @property
    def db_path(self) -> Path:
        return self.data_dir / "app.db"

    @property
    def images_dir(self) -> Path:
        return self.data_dir / "images"

    @property
    def exports_dir(self) -> Path:
        return self.data_dir / "exports"


@lru_cache
def get_settings() -> Settings:
    return Settings()
