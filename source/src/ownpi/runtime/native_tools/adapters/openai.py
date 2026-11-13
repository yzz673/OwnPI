from __future__ import annotations

from typing import TYPE_CHECKING, Any, Iterable

from ownpi.runtime.native_tools.spec import (
    NativeToolSpec,
)
from ownpi.runtime.native_tools.provider import (
    FrozenProviderJson,
    ProviderOutputItem,
    ProviderOutputItemKind,
)
from ownpi.runtime.native_tools.provider_output_failure import preserve_provider_parse_source
from ownpi.runtime.native_tools.provider_content_projection import provider_message_blocks
from ownpi.runtime.native_tools.strict_arguments import native_function_schema

if TYPE_CHECKING:
    pass


class OpenAINativeToolAdapter:
    @staticmethod
    def _function(spec: NativeToolSpec, strict: bool) -> dict[str, Any]:
        projection = native_function_schema(spec.input_schema, strict=strict)
        return {'name': spec.name, 'description': spec.description,
                'parameters': projection.parameters, 'strict': projection.strict}

    def build_provider_tools(
        self, specs: Iterable[NativeToolSpec], *, strict: bool = False,
    ) -> list[dict[str, Any]]:
        return [
            {
                'type': 'function',
                'function': self._function(spec, strict),
            }
            for spec in specs
        ]

    def build_responses_provider_tools(
        self,
        specs: Iterable[NativeToolSpec],
        *,
        native_tool_search_output: bool,
        strict: bool = False,
        hosted_web_search_spec: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        tools: list[dict[str, Any]] = []
        for spec in specs:
            if spec.name == 'web_search' and hosted_web_search_spec is not None:
                tools.append(dict(hosted_web_search_spec))
                continue
            if spec.name == 'tool_search' and native_tool_search_output:
                tools.append(
                    {
                        'type': 'tool_search',
                        'execution': 'client',
                        'description': spec.description,
                        'parameters': spec.input_schema,
                    }
                )
                continue
            tools.append(
                {
                    'type': 'function',
                    **self._function(spec, strict),
                }
            )
        return tools

    def build_search_output_tools(
        self, definitions: Iterable[dict[str, Any]], *, strict: bool = False,
    ) -> list[dict[str, Any]]:
        tools: list[dict[str, Any]] = []
        namespaces: dict[str, dict[str, Any]] = {}
        for definition in definitions:
            spec = NativeToolSpec(
                name=definition['name'], description=definition.get('description', ''),
                input_schema=definition['input_schema'],
                execution_class=definition.get('execution_class', 'read_only'),
                side_effect_level=definition.get('side_effect_level', 'none'),
                permission_class=definition.get('permission_class', 'default'),
            )
            function = self.build_responses_provider_tools(
                (spec,), native_tool_search_output=False, strict=strict,
            )[0]
            namespace = definition.get('namespace', '')
            if not namespace:
                tools.append(function)
                continue
            if namespace not in namespaces:
                group = {
                    'type': 'namespace', 'name': namespace,
                    'description': definition.get('namespace_description', ''), 'tools': [],
                }
                namespaces[namespace] = group
                tools.append(group)
            namespaces[namespace]['tools'].append(function)
        return tools



    def parse_response_items(self, message: dict[str, Any]) -> tuple[ProviderOutputItem, ...]:
        with preserve_provider_parse_source(message):
            return self._parse_response_items(message)

    def _parse_response_items(self, message: dict[str, Any]) -> tuple[ProviderOutputItem, ...]:
        items: list[ProviderOutputItem] = []
        content = message.get('content')
        if content is not None and not isinstance(content, (str, list)):
            content = None
        reasoning_fields = ('reasoning_content', 'reasoning')
        for field_name in reasoning_fields:
            value = message.get(field_name)
            if value is None:
                continue
            items.append(ProviderOutputItem(
                kind=ProviderOutputItemKind.REASONING,
                sequence_index=len(items),
                provider_type=field_name,
                provider_payload=FrozenProviderJson.from_value({field_name: value}),
                text=value if isinstance(value, str) else '',
            ))
        has_other_content = any(
            key not in {'tool_calls', 'content', 'role', 'id', *reasoning_fields}
            and value is not None
            for key, value in message.items()
        )
        if content not in (None, '', []) or has_other_content:
            blocks = (provider_message_blocks(content, source='openai_chat') if isinstance(content, list)
                      else [{'type': 'text', 'text': content}] if isinstance(content, str) else [])
            items.append(
                ProviderOutputItem(
                    kind=ProviderOutputItemKind.MESSAGE,
                    sequence_index=len(items),
                    provider_type='message',
                    provider_payload=FrozenProviderJson.from_value(message),
                    provider_item_id=str(message.get('id') or ''),
                    text=_message_text(content),
                    content_blocks=tuple(
                        FrozenProviderJson.from_value(block)
                        for block in blocks
                        if isinstance(block, dict)
                    ),
                )
            )
        raw_calls = message.get('tool_calls')
        if raw_calls is not None and not isinstance(raw_calls, list):
            raw_calls = None
        calls = raw_calls if isinstance(raw_calls, list) else []
        for raw_call in calls:
            if not isinstance(raw_call, dict):
                continue
            if raw_call.get('type', 'function') != 'function':
                items.append(ProviderOutputItem(
                    kind=ProviderOutputItemKind.PROVIDER_SPECIFIC,
                    sequence_index=len(items),
                    provider_type=str(raw_call.get('type') or 'unknown'),
                    provider_payload=FrozenProviderJson.from_value(raw_call),
                    provider_item_id=str(raw_call.get('id') or ''),
                ))
                continue
            function = raw_call.get('function')
            function_payload = function if isinstance(function, dict) else {}
            name = function_payload.get('name')
            if not isinstance(name, str):
                continue
            raw_arguments = function_payload.get('arguments')
            if not isinstance(raw_arguments, str):
                continue
            call_id = raw_call.get('id')
            if not isinstance(call_id, str):
                continue
            items.append(
                ProviderOutputItem(
                    kind=ProviderOutputItemKind.TOOL_CALL,
                    sequence_index=len(items),
                    provider_type=str(raw_call.get('type') or 'function'),
                    provider_payload=FrozenProviderJson.from_value(raw_call),
                    provider_item_id=call_id,
                    tool_name=name,
                    call_id=call_id,
                    request_id=call_id,
                    raw_arguments=raw_arguments,
                )
            )
        return tuple(items)




def _message_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ''
    return ''.join(
        block['text']
        for block in provider_message_blocks(content, source='openai_chat')
        if block['type'] == 'text'
    )
