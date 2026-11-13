from __future__ import annotations

import base64
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import Literal

from ownpi.runtime.limits.resources import (
    IMAGE_HIGH_MAX_DIMENSION,
    IMAGE_ORIGINAL_MAX_DIMENSION,
    MAX_IMAGE_INPUT_BYTES,
)


ImagePromptDetail = Literal['auto', 'high', 'original']
_PASSTHROUGH_FORMATS = {
    'PNG': 'image/png',
    'JPEG': 'image/jpeg',
    'WEBP': 'image/webp',
}


class ImagePreparationError(RuntimeError):
    def __init__(self, message: str, *, error_class: str = 'image_processing_error') -> None:
        super().__init__(message)
        self.error_class = error_class


@dataclass(frozen=True)
class ImagePromptPayload:
    image_url: str
    mime_type: str
    width: int
    height: int
    source_path: str
    detail: ImagePromptDetail = 'high'


def data_url_from_bytes(mime: str, data: bytes) -> str:
    encoded = base64.b64encode(data).decode('ascii')
    return f'data:{mime};base64,{encoded}'


def split_base64_data_url(image_url: str) -> tuple[str, str]:
    prefix = 'data:'
    if not image_url.lower().startswith(prefix):
        raise ImagePreparationError('image_url is not a data URL', error_class='invalid_data_url')
    metadata, separator, payload = image_url[len(prefix):].partition(',')
    if not separator:
        raise ImagePreparationError('data URL is missing comma separator', error_class='invalid_data_url')
    parts = [part.strip() for part in metadata.split(';') if part.strip()]
    if not parts:
        raise ImagePreparationError('data URL is missing media type', error_class='invalid_data_url')
    if not any(part.lower() == 'base64' for part in parts[1:]):
        raise ImagePreparationError('only base64 data URLs are supported', error_class='invalid_data_url')
    return parts[0], payload


def load_local_image_for_prompt(path: Path | str, *, detail: str = 'high') -> ImagePromptPayload:
    normalized_detail = _normalize_detail(detail)
    if normalized_detail == 'low':
        raise ImagePreparationError(
            "image detail 'low' is not supported; use 'high', 'original', or 'auto'",
            error_class='unsupported_detail',
        )
    source_path = Path(path)
    try:
        file_bytes = source_path.read_bytes()
    except OSError as exc:
        raise ImagePreparationError(
            f'failed to read image {source_path}: {exc}',
            error_class='read_error',
        ) from exc
    if len(file_bytes) > MAX_IMAGE_INPUT_BYTES:
        raise ImagePreparationError(
            f'image input is too large: {len(file_bytes)} bytes',
            error_class='image_too_large',
        )
    try:
        from PIL import Image
    except ImportError as exc:
        raise ImagePreparationError('pillow is not installed', error_class='dependency_missing') from exc

    try:
        with Image.open(BytesIO(file_bytes)) as image:
            image.load()
            source_format = str(image.format or '').upper()
            width, height = image.size
            max_dimension = (
                IMAGE_ORIGINAL_MAX_DIMENSION
                if normalized_detail == 'original'
                else IMAGE_HIGH_MAX_DIMENSION
            )
            needs_resize = max(width, height) > max_dimension
            can_passthrough = source_format in _PASSTHROUGH_FORMATS and not needs_resize
            if can_passthrough:
                mime_type = _PASSTHROUGH_FORMATS[source_format]
                data = file_bytes
                final_width, final_height = width, height
            else:
                processed = image.convert('RGBA') if image.mode in {'P', 'LA'} else image.convert('RGB')
                if needs_resize:
                    processed.thumbnail((max_dimension, max_dimension))
                final_width, final_height = processed.size
                buffer = BytesIO()
                processed.save(buffer, format='PNG')
                data = buffer.getvalue()
                mime_type = 'image/png'
    except ImagePreparationError:
        raise
    except Exception as exc:
        raise ImagePreparationError(
            f'failed to decode image {source_path}: {exc}',
            error_class='decode_error',
        ) from exc

    return ImagePromptPayload(
        image_url=data_url_from_bytes(mime_type, data),
        mime_type=mime_type,
        width=int(final_width),
        height=int(final_height),
        source_path=str(source_path),
        detail='original' if normalized_detail == 'original' else 'high',
    )


def _normalize_detail(value: str) -> str:
    detail = str(value or 'high').strip().lower()
    if detail in {'auto', 'high'}:
        return 'high'
    if detail in {'original', 'low'}:
        return detail
    raise ImagePreparationError(
        f"unsupported image detail '{detail}'; use 'high' or 'original'",
        error_class='unsupported_detail',
    )
