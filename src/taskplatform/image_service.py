"""Safe local image storage and bounded JPEG/PNG thumbnail processing."""

from __future__ import annotations

import os
import warnings
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from uuid import UUID

from PIL import Image, UnidentifiedImageError


class UnsupportedImageError(ValueError):
    """The upload is a valid image format the service does not accept."""


class InvalidImageError(ValueError):
    """The upload cannot be decoded as a complete image."""


class UploadTooLargeError(ValueError):
    """The upload or its decoded dimensions exceed configured limits."""


@dataclass(frozen=True)
class StoredUpload:
    input_key: str
    output_key: str
    image_format: str
    width: int
    height: int


class ImageStorage:
    """Resolve task-owned relative keys beneath one shared local storage root."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).expanduser()

    def path_for(self, key: str) -> Path:
        relative = Path(key)
        if relative.is_absolute():
            raise ValueError("storage keys must be relative")
        root = self.root.resolve()
        path = (root / relative).resolve()
        if not path.is_relative_to(root):
            raise ValueError("storage key escapes the storage root")
        return path

    def store_upload(
        self,
        data: bytes,
        task_id: str,
        *,
        max_bytes: int,
        max_pixels: int,
        max_dimension: int,
    ) -> StoredUpload:
        if len(data) > max_bytes:
            raise UploadTooLargeError(f"upload exceeds the {max_bytes}-byte limit")
        try:
            UUID(task_id)
        except ValueError as exc:
            raise ValueError("task id must be a UUID") from exc

        try:
            with warnings.catch_warnings():
                warnings.simplefilter("error", Image.DecompressionBombWarning)
                with Image.open(BytesIO(data)) as image:
                    image_format = image.format
                    if image_format not in {"JPEG", "PNG"}:
                        raise UnsupportedImageError("only JPEG and PNG images are supported")
                    width, height = image.size
                    if width <= 0 or height <= 0 or width > max_dimension or height > max_dimension:
                        raise UploadTooLargeError(
                            f"image dimensions must each be at most {max_dimension} pixels"
                        )
                    if width * height > max_pixels:
                        raise UploadTooLargeError(
                            f"image exceeds the {max_pixels}-pixel limit"
                        )
                    image.verify()
                # verify() checks the structure; load() confirms the pixel data is decodable.
                with Image.open(BytesIO(data)) as image:
                    image.load()
        except UploadTooLargeError:
            raise
        except UnsupportedImageError:
            raise
        except (Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
            raise UploadTooLargeError("decoded image is too large") from exc
        except (UnidentifiedImageError, OSError, SyntaxError, ValueError) as exc:
            raise InvalidImageError("upload is not a complete valid image") from exc

        extension = "jpg" if image_format == "JPEG" else "png"
        input_key = f"uploads/{task_id}.{extension}"
        output_key = f"results/{task_id}.{extension}"
        input_path = self.path_for(input_key)
        input_path.parent.mkdir(parents=True, exist_ok=True)
        with input_path.open("xb") as image_file:
            image_file.write(data)
        return StoredUpload(input_key, output_key, image_format, width, height)


def resize_image(payload: dict, storage_root: str | Path) -> dict:
    """Create a bounded thumbnail, atomically replacing the task's output file."""
    storage = ImageStorage(storage_root)
    source = storage.path_for(str(payload["input_key"]))
    destination = storage.path_for(str(payload["output_key"]))
    max_dimension = int(payload["max_dimension"])
    if max_dimension <= 0:
        raise ValueError("max_dimension must be positive")

    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f".{destination.name}.tmp")
    try:
        with Image.open(source) as image:
            image_format = image.format
            if image_format not in {"JPEG", "PNG"}:
                raise InvalidImageError("stored source is not JPEG or PNG")
            image.thumbnail((max_dimension, max_dimension), Image.Resampling.LANCZOS)
            if image_format == "JPEG" and image.mode not in {"RGB", "L"}:
                image = image.convert("RGB")
            image.save(temporary, format=image_format, optimize=True)
            width, height = image.size
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)

    return {
        "width": width,
        "height": height,
        "format": image_format.lower(),
        "size_bytes": destination.stat().st_size,
    }
