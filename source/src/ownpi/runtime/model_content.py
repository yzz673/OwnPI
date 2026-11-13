from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal


ImageDetail = Literal['auto', 'high', 'original']
ModelContentType = Literal['input_text', 'input_image']


@dataclass(frozen=True)
class ModelContentItem:
    type: ModelContentType
    text: str = ''
    image_url: str = ''
    detail: ImageDetail = 'high'
    source_path: str = ''
    mime_type: str = ''

    @classmethod
    def input_text(cls, text: str) -> 'ModelContentItem':
        return cls(type='input_text', text=str(text or ''))

    @classmethod
    def input_image(
        cls,
        *,
        image_url: str,
        detail: ImageDetail = 'high',
        source_path: str = '',
        mime_type: str = '',
    ) -> 'ModelContentItem':
        return cls(
            type='input_image',
            image_url=str(image_url or ''),
            detail=normalize_image_detail(detail),
            source_path=str(source_path or ''),
            mime_type=str(mime_type or ''),
        )

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> 'ModelContentItem':
        item_type = str(data.get('type', '') or '').strip()
        if item_type == 'input_image':
            return cls.input_image(
                image_url=str(data.get('image_url', '') or ''),
                detail=normalize_image_detail(data.get('detail')),
                source_path=str(data.get('source_path', '') or ''),
                mime_type=str(data.get('mime_type', '') or ''),
            )
        return cls.input_text(str(data.get('text', '') or ''))

    def to_dict(self, *, redact_image_url: bool = False) -> dict[str, Any]:
        if self.type == 'input_image':
            image_url = redact_image_url_value(self.image_url) if redact_image_url else self.image_url
            payload = {
                'type': 'input_image',
                'image_url': image_url,
                'detail': self.detail,
            }
            if self.source_path:
                payload['source_path'] = self.source_path
            if self.mime_type:
                payload['mime_type'] = self.mime_type
            return payload
        return {'type': 'input_text', 'text': self.text}


def content_items_from_dicts(items: Any) -> tuple[ModelContentItem, ...]:
    if not isinstance(items, (list, tuple)):
        return ()
    return tuple(ModelContentItem.from_dict(item) for item in items if isinstance(item, dict))


def content_items_to_dicts(
    items: tuple[ModelContentItem, ...] | list[ModelContentItem],
    *,
    redact_image_url: bool = False,
) -> list[dict[str, Any]]:
    return [item.to_dict(redact_image_url=redact_image_url) for item in items]


def normalize_image_detail(value: Any) -> ImageDetail:
    detail = str(value or 'high').strip().lower()
    if detail == 'auto':
        return 'auto'
    if detail == 'original':
        return 'original'
    return 'high'


def redact_image_url_value(image_url: str) -> str:
    if not image_url:
        return ''
    if image_url.startswith('data:'):
        return f'<image data URL omitted: {len(image_url)} chars>'
    return image_url
