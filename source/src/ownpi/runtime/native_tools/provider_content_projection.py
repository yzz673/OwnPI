"""Atomic content conversion for typed history request serializers."""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Iterable

from ownpi.runtime.model_content import ModelContentItem, redact_image_url_value
from ownpi.runtime.native_tools.provider_input import NativeHistoryProviderInput
from ownpi.runtime.vision import ImagePreparationError, split_base64_data_url


def provider_message_blocks(content: Any, *, source: str) -> list[dict[str, Any]]:
    """Select consumable blocks without modifying the provider's raw facts."""
    if not isinstance(content, list):
        return []
    result = []
    for block in content:
        if not isinstance(block, dict):
            continue
        kind = block.get('type')
        if not isinstance(kind, str):
            continue
        if kind in ({'input_text', 'output_text'} if source == 'openai_responses' else {'text'}):
            valid = isinstance(block.get('text'), str)
        elif source == 'openai_responses' and kind == 'input_image':
            valid = isinstance(block.get('image_url'), str) and block.get('detail') in (
                None, 'auto', 'low', 'high', 'original',
            )
        elif source == 'openai_chat' and kind == 'image_url':
            image = block.get('image_url')
            valid = isinstance(image, dict) and isinstance(image.get('url'), str) and image.get('detail') in (
                None, 'auto', 'low', 'high', 'original',
            )
        elif kind == 'input_audio':
            audio = block.get('input_audio')
            valid = (isinstance(block.get('audio_url'), str) if source == 'openai_responses' else
                     isinstance(audio, dict) and isinstance(audio.get('data'), str)
                     and audio.get('format') in ('wav', 'mp3'))
        else:
            valid = False
        if valid:
            result.append(block)
    return result


def provider_message_content(content: Any, *, source: str, target: str) -> str | list[dict[str, Any]]:
    if isinstance(content, str):
        return content
    result = []
    for block in provider_message_blocks(content, source=source):
        kind = block['type']
        if source == target:
            result.append({key: value for key, value in block.items()
                           if target != 'openai_responses' or key != 'logprobs'})
        elif kind in {'text', 'input_text', 'output_text'}:
            if target == 'openai_responses':
                result.append({'type': 'output_text', 'text': block['text'], 'annotations': []})
            else:
                result.append({'type': 'text', 'text': block['text']})
        elif kind in {'input_image', 'image_url'}:
            image = block['image_url']
            url = image['url'] if isinstance(image, dict) else image
            detail = (image if isinstance(image, dict) else block).get('detail', 'auto')
            if target == 'anthropic':
                result.extend(anthropic_content((ModelContentItem.input_image(image_url=url),)))
            elif target == 'openai_chat':
                result.append({'type': 'image_url', 'image_url': {'url': url, 'detail': detail}})
            else:
                result.append({'type': 'input_image', 'image_url': url, 'detail': detail})
    return result


def openai_responses_content(items: Iterable[ModelContentItem]) -> list[dict[str, Any]]:
    return [_content_block(item, target='openai_responses') for item in items]


def openai_chat_content(items: Iterable[ModelContentItem]) -> list[dict[str, Any]]:
    return [_content_block(item, target='openai_chat') for item in items]


def anthropic_content(items: Iterable[ModelContentItem]) -> list[dict[str, Any]]:
    return [_content_block(item, target='anthropic') for item in items]


def history_prefix_messages(
    value: NativeHistoryProviderInput,
    prefix_messages: list[dict[str, Any]] | None,
) -> list[dict[str, Any]]:
    messages = deepcopy(
        value.immutable_prefix_messages if prefix_messages is None else prefix_messages,
    )
    return messages


def _content_block(item: ModelContentItem, *, target: str) -> dict[str, Any]:
    if item.type != 'input_image':
        return _text_block(item.text, target)
    if target == 'openai_responses':
        return {'type': 'input_image', 'image_url': item.image_url, 'detail': item.detail}
    if target == 'openai_chat':
        return {
            'type': 'image_url',
            'image_url': {'url': item.image_url, 'detail': item.detail},
        }
    if item.image_url.startswith(('https://', 'http://')):
        return {'type': 'image', 'source': {'type': 'url', 'url': item.image_url}}
    try:
        media_type, data = split_base64_data_url(item.image_url)
    except ImagePreparationError:
        return _text_block(
            'image content omitted because it could not be prepared: '
            f'{redact_image_url_value(item.image_url)}', target,
        )
    return {
        'type': 'image',
        'source': {'type': 'base64', 'media_type': media_type, 'data': data},
    }


def _text_block(text: str, target: str) -> dict[str, Any]:
    return {'type': 'input_text' if target == 'openai_responses' else 'text', 'text': text}
