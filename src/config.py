import json
import json5
import os
from pathlib import Path
from typing import Optional
from pydantic import BaseModel, HttpUrl, field_validator


class TelegramConfig(BaseModel):
    api_id: int
    api_hash: str
    phone_number: str
    session_string: Optional[str] = None


class BarkConfig(BaseModel):
    endpoints: list[str]
    group: Optional[str] = None
    sound: Optional[str] = None

    @field_validator("endpoints")
    @classmethod
    def validate_endpoints(cls, v):
        if not v or len(v) == 0:
            raise ValueError("At least one Bark endpoint must be configured")
        return v


class ActivityConfig(BaseModel):
    timeout_seconds: int = 300
    session_hash: Optional[list[str]] = None


class LoggingConfig(BaseModel):
    level: str = "INFO"
    file: Optional[str] = None


class ImageCacheConfig(BaseModel):
    backend: str = "tmpfiles"  # "tmpfiles" or "imgbb"
    # imgbb.com options
    imgbb_api_key: Optional[str] = None
    # shared
    expiration_days: int = 7
    db_path: str = "image_cache.db"


class Config(BaseModel):
    telegram: TelegramConfig
    bark: BarkConfig
    activity: ActivityConfig = ActivityConfig()
    logging: LoggingConfig = LoggingConfig()
    image_cache: ImageCacheConfig = ImageCacheConfig()

    @staticmethod
    def load(config_path: str = "config.json") -> "Config":
        if not os.path.exists(config_path):
            raise FileNotFoundError(
                f"Configuration file not found: {config_path}\n"
                f"Please run 'python init.py' to set up your configuration."
            )

        with open(config_path, "r", encoding="utf-8") as f:
            config_dict = json5.load(f)

        return Config(**config_dict)

    def save(self, config_path: str = "config.json") -> None:
        os.makedirs(os.path.dirname(config_path) or ".", exist_ok=True)
        with open(config_path, "w") as f:
            json.dump(self.model_dump(), f, indent=2)

    def update_session_string(self, session_string: str, config_path: str = "config.json") -> None:
        self.telegram.session_string = session_string
        self.save(config_path)
