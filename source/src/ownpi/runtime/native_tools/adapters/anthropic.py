from __future__ import annotations

import json
from typing import Any, Iterable

from ownpi.runtime.native_tools.spec import (
    NativeToolSpec,
)
from ownpi.runtime.native_tools.provider import (
    FrozenProviderJson,
    ProviderOutputItem,
    ProviderOutputItemKind,
)
from ownpi.runtime.native_tools.provider_output_failure import preserve_provider_parse_source


class AnthropicNativeToolAdapter:
    def build_provider_tools(self, specs: Iterable[NativeToolSpec]) -> list[dict[str, Any]]:
        return [
            {
                'name': spec.name,
                'description': spec.description,
                'input_schema': spec.input_schema,
            }
            for spec in specs
        ]

    def parse_response_items(self, message: dict[str, Any]) -> tuple[ProviderOutputItem, ...]:
        with preserve_provider_parse_source(message):
            return self._parse_response_items(message)

    def _parse_response_items(self, message: dict[str, Any]) -> tuple[ProviderOutputItem, ...]:
        content = message.get('content', [])
        if not isinstance(content, list):
            raise ValueError('Anthropic message content must be an array')
        items: list[ProviderOutputItem] = []
        for block in content:
            if not isinstance(block, dict):
                continue
            block_type = str(block.get('type', '') or '')
            if block_type == 'text':
                if not isinstance(block.get('text'), str):
                    continue
                citations = block.get('citations')
                items.append(
                    ProviderOutputItem(
                        kind=ProviderOutputItemKind.MESSAGE,
                        sequence_index=len(items),
                        provider_type=block_type,
                        provider_payload=FrozenProviderJson.from_value(block),
                        provider_item_id=str(block.get('id') or ''),
                        provider_status=str(block.get('status') or ''),
                        text=str(block.get('text', '') or ''),
                        content_blocks=(FrozenProviderJson.from_value(block),),
                        provider_metadata=FrozenProviderJson.from_value(
                            {'citation_count': len(citations) if isinstance(citations, list) else 0}
                        ),
                    )
                )
                continue
            if block_type in {'thinking', 'redacted_thinking'}:
                if any(field in block and not isinstance(block[field], str)
                       for field in ('thinking', 'text', 'signature', 'data')):
                    continue
                items.append(
                    ProviderOutputItem(
                        kind=ProviderOutputItemKind.REASONING,
                        sequence_index=len(items),
                        provider_type=block_type,
                        provider_payload=FrozenProviderJson.from_value(block),
                        provider_item_id=str(block.get('id') or ''),
                        text=str(block.get('thinking') or block.get('text') or ''),
                    )
                )
                continue
            if block_type in {'server_tool_use', 'web_search_tool_result'}:
                if block_type == 'web_search_tool_result' and not isinstance(block.get('content'), (list, dict)):
                    continue
                provider_item_id = str(
                    block.get('id') or block.get('tool_use_id') or ''
                )
                result_status, error_code = _hosted_result_outcome(block)
                items.append(
                    ProviderOutputItem(
                        kind=(
                            ProviderOutputItemKind.HOSTED_TOOL_CALL
                            if block_type == 'server_tool_use'
                            else ProviderOutputItemKind.HOSTED_TOOL_RESULT
                        ),
                        sequence_index=len(items),
                        provider_type=block_type,
                        provider_payload=FrozenProviderJson.from_value(block),
                        provider_item_id=provider_item_id,
                        provider_status=str(block.get('status') or ''),
                        provider_metadata=FrozenProviderJson.from_value(
                            {
                                'result_status': result_status,
                                'error_code': error_code,
                            }
                        ),
                    )
                )
                continue
            if block_type != 'tool_use':
                items.append(
                    ProviderOutputItem(
                        kind=ProviderOutputItemKind.PROVIDER_SPECIFIC,
                        sequence_index=len(items),
                        provider_type=block_type or 'unknown',
                        provider_payload=FrozenProviderJson.from_value(block),
                        provider_item_id=str(block.get('id') or ''),
                        provider_status=str(block.get('status') or ''),
                    )
                )
                continue
            name = block.get('name')
            if not isinstance(name, str):
                continue
            call_id = block.get('id')
            if not isinstance(call_id, str):
                continue
            raw_input = block.get('input')
            items.append(
                ProviderOutputItem(
                    kind=ProviderOutputItemKind.TOOL_CALL,
                    sequence_index=len(items),
                    provider_type=block_type,
                    provider_payload=FrozenProviderJson.from_value(block),
                    provider_item_id=call_id,
                    tool_name=name,
                    call_id=call_id,
                    request_id=call_id,
                    raw_arguments=_raw_anthropic_arguments(raw_input),
                )
            )
        return tuple(items)



def _hosted_result_outcome(block: dict[str, Any]) -> tuple[str, str]:
    if block.get('type') != 'web_search_tool_result':
        return '', ''
    content = block.get('content')
    if isinstance(content, list):
        return ('success' if content else 'empty'), ''
    if isinstance(content, dict):
        content_type = str(content.get('type') or '')
        if content_type == 'web_search_tool_result_error':
            return 'error', str(content.get('error_code') or '')
    return 'unknown', ''


def _raw_anthropic_arguments(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(',', ':'),
        sort_keys=True,
    )
