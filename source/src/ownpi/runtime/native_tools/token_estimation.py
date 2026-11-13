"""Coarse model-visible item costs, independent of transport accounting."""

from __future__ import annotations

import base64
import hashlib
import json
import math
from collections import OrderedDict
from io import BytesIO
from threading import RLock
from typing import Any

from PIL import Image
from mutagen import File as audio_file

from ownpi.runtime.limits.tool_output import approx_tokens_from_byte_count


RESIZED_IMAGE_BYTES = 7373
MEDIA_CACHE_SIZE = 32
_image_cache: OrderedDict[bytes, int | None] = OrderedDict()
_audio_cache: OrderedDict[bytes, int | None] = OrderedDict()
_cache_lock = RLock()


def _cached_cost(payload: str, cache: OrderedDict, read: Any) -> int | None:
    key = hashlib.sha256(payload.encode('utf-8')).digest()
    with _cache_lock:
        if key not in cache:
            try:
                cache[key] = read(base64.b64decode(payload, validate=True))
            except Exception:
                # Malformed metadata affects only this heuristic, never the run.
                cache[key] = None
            if len(cache) > MEDIA_CACHE_SIZE:
                cache.popitem(last=False)
        cache.move_to_end(key)
        return cache[key]


def _image_bytes(data: bytes) -> int:
    with Image.open(BytesIO(data)) as image:
        return min(10_000, math.ceil(image.width / 32) * math.ceil(image.height / 32)) * 4


def _audio_bytes(data: bytes) -> int | None:
    media = audio_file(BytesIO(data))
    if media is None:
        return None
    duration = media.info.length
    return math.ceil(duration * 10) * 4 if math.isfinite(duration) and duration >= 0 else None


def _data_payload(url: object, modality: str) -> str | None:
    if not isinstance(url, str) or not url.lower().startswith('data:') or ',' not in url:
        return None
    metadata, payload = url.split(',', 1)
    mime, *parameters = metadata[5:].split(';')
    if mime.lower().startswith(modality + '/') and 'base64' in (part.lower() for part in parameters):
        return payload
    return None


def _block_adjustment(block: object, *, encrypted: bool = False) -> int:
    if not isinstance(block, dict):
        return 0
    kind = block.get('type')
    if kind == 'tool_result':
        return _content_adjustment(block.get('content'))
    if kind in {'input_image', 'image_url', 'image'}:
        url = block.get('image_url')
        detail = block.get('detail')
        if isinstance(url, dict):
            detail, url = url.get('detail'), url.get('url')
        payload = _data_payload(url, 'image')
        if kind == 'image':
            source = block.get('source', {})
            if (isinstance(source, dict) and source.get('type') == 'base64'
                    and str(source.get('media_type', '')).lower().startswith('image/')
                    and isinstance(source.get('data'), str)):
                payload = source['data']
        if payload is not None:
            cost = (_cached_cost(payload, _image_cache, _image_bytes)
                    if detail == 'original' else None)
            return (RESIZED_IMAGE_BYTES if cost is None else cost) - len(payload.encode('utf-8'))
    if kind in {'input_audio', 'audio'}:
        payload = _data_payload(block.get('audio_url'), 'audio')
        audio = block.get('input_audio')
        if payload is None and isinstance(audio, dict) and isinstance(audio.get('data'), str):
            payload = audio['data']
        if payload is not None:
            cost = _cached_cost(payload, _audio_cache, _audio_bytes)
            if cost is None:
                fallback = block.get('audio_url', payload)
                cost = approx_tokens_from_byte_count(len(fallback.encode('utf-8'))) * 4
            return cost - len(payload.encode('utf-8'))
    if encrypted and kind == 'encrypted_content' and isinstance(block.get('encrypted_content'), str):
        size = len(block['encrypted_content'].encode('utf-8'))
        return (size * 9 + 15) // 16 - size
    return 0


def _content_adjustment(content: object, *, encrypted: bool = False) -> int:
    return sum(_block_adjustment(block, encrypted=encrypted) for block in content) if isinstance(content, (list, tuple)) else 0


def estimate_item_tokens(item: object, *, serialized_bytes: int | None = None) -> int:
    """Round once per item; special rules apply only to recognized content."""
    raw = serialized_bytes if serialized_bytes is not None else len(json.dumps(
        item, ensure_ascii=False, sort_keys=True, separators=(',', ':'), default=str,
    ).encode('utf-8'))
    if isinstance(item, dict):
        kind = item.get('type')
        cipher = item.get('encrypted_content')
        if kind in {'reasoning', 'compaction', 'context_compaction'} and isinstance(cipher, str):
            return approx_tokens_from_byte_count(max(0, len(cipher.encode('utf-8')) * 3 // 4 - 650))
        if kind in {'function_call_output', 'custom_tool_call_output'}:
            raw += _content_adjustment(item.get('output'), encrypted=kind == 'function_call_output')
        elif kind in {'message', 'agent_message'} or ('role' in item and 'content' in item):
            raw += _content_adjustment(item.get('content'), encrypted=kind == 'agent_message')
        elif kind in {'input_image', 'image_url', 'image', 'input_audio', 'audio'}:
            raw += _block_adjustment(item)
    return approx_tokens_from_byte_count(max(0, raw))


def estimate_items_tokens(items: Any) -> int:
    return sum(estimate_item_tokens(item) for item in items)
