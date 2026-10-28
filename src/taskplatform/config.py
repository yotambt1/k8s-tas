"""Runtime settings, read from environment variables."""

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    redis_url: str
    queue_name: str
    lease_seconds: float
    storage_dir: Path
    max_upload_bytes: int
    max_image_pixels: int
    max_image_dimension: int
    output_max_dimension: int


def get_settings() -> Settings:
    return Settings(
        redis_url=os.environ.get("REDIS_URL", "redis://localhost:6379/0"),
        queue_name=os.environ.get("QUEUE_NAME", "tasks"),
        lease_seconds=float(os.environ.get("LEASE_SECONDS", "30")),
        storage_dir=Path(os.environ.get("STORAGE_DIR", "./data")).expanduser(),
        max_upload_bytes=int(os.environ.get("MAX_UPLOAD_BYTES", str(10 * 1024 * 1024))),
        max_image_pixels=int(os.environ.get("MAX_IMAGE_PIXELS", "40000000")),
        max_image_dimension=int(os.environ.get("MAX_IMAGE_DIMENSION", "10000")),
        output_max_dimension=int(os.environ.get("OUTPUT_MAX_DIMENSION", "512")),
    )
