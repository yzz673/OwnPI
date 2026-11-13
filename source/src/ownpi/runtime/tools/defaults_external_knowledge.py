from __future__ import annotations
from ownpi.runtime.tools.definitions import ToolExposure

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any
from uuid import uuid4

from ownpi.runtime.contracts import ToolResult
from ownpi.runtime.limits.projection import (
    DEFAULT_EXTERNAL_KNOWLEDGE_READ_ITEMS,
    DEFAULT_EXTERNAL_KNOWLEDGE_SEARCH_RESULTS,
)
from ownpi.runtime.memory.external.contracts import (
    ExternalKnowledgeReadRequest,
    ExternalKnowledgeResultStatus,
    ExternalKnowledgeSearchRequest,
    ExternalKnowledgeSourceId,
)
from ownpi.runtime.memory.external import service as external_service
from ownpi.runtime.memory.external.navigation import (
    EXTERNAL_KNOWLEDGE_HIERARCHY_GUIDE,
    EXTERNAL_KNOWLEDGE_READ_TOOL,
    EXTERNAL_KNOWLEDGE_RESULT_FAMILY,
    EXTERNAL_KNOWLEDGE_SEARCH_TOOL,
)
from ownpi.runtime.memory.map_contracts import MemoryDomain
from ownpi.runtime.memory.payloads import (
    drop_last_list_item,
    serialized_payload_size_bytes,
    truncate_utf8,
)
from ownpi.runtime.memory.receipts import (
    MemoryConsumptionOperation,
    MemoryConsumptionStatus,
)
from ownpi.runtime.memory.tool_results import build_memory_tool_result
from ownpi.runtime.tools.definitions import ToolDefinition, ToolExecutionContext


def build_external_knowledge_tool_definitions(state: Any) -> tuple[ToolDefinition, ...]:
    config = state.runtime_preferences.external_knowledge
    pin = state.memory_state.external_knowledge_pin
    if (
        not bool(config.get('enabled', False))
        or pin is None
        or str(state.memory_state.external_knowledge_status or '') == 'unavailable'
    ):
        return ()
    delivery_root = str(config.get('delivery_root', '') or '').strip()
    if not delivery_root:
        return ()
    try:
        service = external_service.build_external_knowledge_service(
            delivery_root=Path(delivery_root),
            pin=pin,
        )
    except (OSError, TypeError, ValueError):
        return ()
    if not service.available_sources:
        return ()
    max_result_chars = int(config.get('max_tool_result_chars', 6000) or 6000)
    return (
        _search_definition(
            service=service,
            max_result_chars=max_result_chars,
        ),
        _read_definition(
            service=service,
            max_result_chars=max_result_chars,
        ),
    )


def _search_definition(
    *,
    service: external_service.ExternalKnowledgeService,
    max_result_chars: int,
) -> ToolDefinition:
    async def handler(context: ToolExecutionContext, arguments: dict[str, Any]) -> ToolResult:
        del context
        try:
            sources = tuple(
                ExternalKnowledgeSourceId(str(item))
                for item in arguments.get('sources', ())
            )
            request = ExternalKnowledgeSearchRequest(
                query=str(arguments.get('query', '')),
                sources=sources,
                intents=tuple(str(item) for item in arguments.get('intents', ())),
                limit=int(
                    arguments.get(
                        'limit',
                        DEFAULT_EXTERNAL_KNOWLEDGE_SEARCH_RESULTS,
                    )
                ),
            )
        except (KeyError, TypeError, ValueError):
            return _error_result(
                tool_name=EXTERNAL_KNOWLEDGE_SEARCH_TOOL,
                operation=MemoryConsumptionOperation.SEARCH,
                fallback='invalid_request',
            )
        try:
            result = service.search(request)
            payload = _bounded_payload(
                external_service.external_result_to_dict(result),
                max_chars=max_result_chars,
                item_paths=(('hits',),),
            )
            return _tool_result(
                tool_name=EXTERNAL_KNOWLEDGE_SEARCH_TOOL,
                payload=payload,
                operation=MemoryConsumptionOperation.SEARCH,
            )
        except (KeyError, OSError, TypeError, UnicodeError, ValueError):
            return _error_result(
                tool_name=EXTERNAL_KNOWLEDGE_SEARCH_TOOL,
                operation=MemoryConsumptionOperation.SEARCH,
                fallback='adapter_error',
            )

    return ToolDefinition(
        exposure=ToolExposure.DIRECT,
        **_summary(
            name=EXTERNAL_KNOWLEDGE_SEARCH_TOOL,
            description=(
                'Search the pinned read-only scientific knowledge release on demand for the '
                'current Stage. Use method_knowledge for methods, analysis design, controls, '
                'validation, and failure patterns; use research_strategy for novelty, evidence '
                'strength, claim boundaries, scientific organization, and research positioning. '
                'Results are candidate summaries and stable refs; read a selected ref with '
                'external_knowledge_read. Not for Runtime Tool/API documentation, local code, '
                'workspace files or state, or dependency installation. '
                f'{EXTERNAL_KNOWLEDGE_HIERARCHY_GUIDE}'
            ),
            detail=(
                'Use only when the current work has an unresolved scientific knowledge need. '
                'Search summaries, select a relevant candidate, read that ref, and apply useful '
                'guidance to the current decision. A lookup is optional when accepted evidence '
                'or upstream artifacts already provide sufficient guidance.'
            ),
            search_hint=(
                'Search Method Knowledge or Research Strategy for a current unresolved scientific '
                'question, then progressively read one selected candidate.'
            ),
        ),
        input_schema_builder=lambda: {
            'type': 'object',
            'properties': {
                'query': {
                    'type': 'string',
                    'minLength': 1,
                    'maxLength': 2000,
                    'description': (
                        'The current unresolved methodological or research-strategy knowledge '
                        'need. Do not use this field to request Runtime Tool schemas, local code, '
                        'workspace files or state, or dependency installation instructions.'
                    ),
                },
                'sources': {
                    'type': 'array',
                    'description': (
                        'Optional scientific source filter. Omit to search both sources when the '
                        'knowledge need genuinely spans concrete methods and research strategy.'
                    ),
                    'items': {
                        'type': 'string',
                        'enum': [item.value for item in ExternalKnowledgeSourceId],
                        'description': (
                            'method_knowledge covers methods, analysis design, controls, '
                            'validation, and failure patterns; research_strategy covers novelty, '
                            'evidence strength, claim boundaries, scientific organization, and '
                            'research positioning.'
                        ),
                    },
                    'maxItems': 2,
                },
                'intents': {
                    'type': 'array',
                    'items': {
                        'type': 'string',
                        'pattern': r'^[A-Za-z0-9][A-Za-z0-9._+-]*$',
                        'maxLength': 64,
                        'description': (
                            'Optional canonical intent identifier such as '
                            'claim_guarding or analysis_decision; do not use natural-language '
                            'phrases.'
                        ),
                    },
                    'maxItems': 8,
                },
                'limit': {
                    'type': 'integer',
                    'minimum': 1,
                    'default': DEFAULT_EXTERNAL_KNOWLEDGE_SEARCH_RESULTS,
                },
            },
            'required': ['query'],
            'additionalProperties': False,
        },
        handler=handler,
        observation_family=EXTERNAL_KNOWLEDGE_RESULT_FAMILY,
        result_family=EXTERNAL_KNOWLEDGE_RESULT_FAMILY,
        max_prompt_result_chars=max_result_chars,
        max_result_size_chars=max_result_chars,
        result_identity_fields=('status', 'logical_ref'),
    )


def _read_definition(
    *,
    service: external_service.ExternalKnowledgeService,
    max_result_chars: int,
) -> ToolDefinition:
    async def handler(context: ToolExecutionContext, arguments: dict[str, Any]) -> ToolResult:
        del context
        try:
            request = ExternalKnowledgeReadRequest(
                logical_ref=str(arguments.get('logical_ref', '')),
                relation=str(arguments.get('relation', '') or ''),
                cursor=str(arguments.get('cursor', '') or ''),
                limit=int(
                    arguments.get(
                        'limit',
                        DEFAULT_EXTERNAL_KNOWLEDGE_READ_ITEMS,
                    )
                ),
            )
        except (KeyError, TypeError, ValueError):
            return _error_result(
                tool_name=EXTERNAL_KNOWLEDGE_READ_TOOL,
                operation=MemoryConsumptionOperation.READ,
                fallback='invalid_request',
            )
        try:
            result = service.read(request)
            payload = _bounded_payload(
                external_service.external_result_to_dict(result),
                max_chars=max_result_chars,
                item_paths=(('relation_page', 'items'), ('related_refs',)),
            )
            return _tool_result(
                tool_name=EXTERNAL_KNOWLEDGE_READ_TOOL,
                payload=payload,
                operation=MemoryConsumptionOperation.READ,
            )
        except (KeyError, OSError, TypeError, UnicodeError, ValueError):
            return _error_result(
                tool_name=EXTERNAL_KNOWLEDGE_READ_TOOL,
                operation=MemoryConsumptionOperation.READ,
                fallback='adapter_error',
            )

    return ToolDefinition(
        exposure=ToolExposure.DIRECT,
        **_summary(
            name=EXTERNAL_KNOWLEDGE_READ_TOOL,
            description=(
                'Progressively read bounded detail from one stable External Knowledge ref, '
                'normally selected from external_knowledge_search. Apply relevant guidance to '
                'the current Stage decision or artifact; do not copy the knowledge body '
                'mechanically or propagate it as state to downstream Stages.'
            ),
            detail=(
                'Read expands one selected Method Knowledge or Research Strategy candidate. Use '
                'returned relations and cursors only when more bounded detail is needed; preserve '
                'the resulting scientific decision in current work rather than duplicating the '
                'reference body across the workflow. '
                f'{EXTERNAL_KNOWLEDGE_HIERARCHY_GUIDE}'
            ),
            search_hint=(
                'Read a stable ref selected by external_knowledge_search and apply it to the '
                'current Stage knowledge need.'
            ),
        ),
        input_schema_builder=lambda: {
            'type': 'object',
            'properties': {
                'logical_ref': {
                    'type': 'string',
                    'minLength': 1,
                    'maxLength': 320,
                    'description': (
                        'Stable opaque ref normally selected from an '
                        'external_knowledge_search candidate.'
                    ),
                },
                'relation': {
                    'type': 'string',
                    'maxLength': 96,
                    'description': (
                        'Optional relation returned by a prior read for bounded progressive '
                        'disclosure of related knowledge.'
                    ),
                },
                'cursor': {
                    'type': 'string',
                    'maxLength': 320,
                    'description': (
                        'Optional cursor returned by a prior read to continue the same bounded '
                        'progressive relation page.'
                    ),
                },
                'limit': {
                    'type': 'integer',
                    'minimum': 1,
                    'default': DEFAULT_EXTERNAL_KNOWLEDGE_READ_ITEMS,
                    'description': 'Maximum number of related items to return in this bounded read.',
                },
            },
            'required': ['logical_ref'],
            'additionalProperties': False,
        },
        handler=handler,
        observation_family=EXTERNAL_KNOWLEDGE_RESULT_FAMILY,
        result_family=EXTERNAL_KNOWLEDGE_RESULT_FAMILY,
        max_prompt_result_chars=max_result_chars,
        max_result_size_chars=max_result_chars,
        result_identity_fields=('status', 'logical_ref'),
    )


def _summary(
    *,
    name: str,
    description: str,
    detail: str,
    search_hint: str,
) -> dict[str, Any]:
    return {
        'name': name,
        'kind': 'memory_read',
        'description': description,
        'detail': detail,
        'search_hint': search_hint,
        'search_keywords': (
            'external',
            'knowledge',
            'memory',
            'method',
            'research',
            'method_knowledge',
            'research_strategy',
            'controls',
            'failure patterns',
            'claim boundaries',
            'evidence strength',
        ),
        'read_only': True,
        'concurrency_safe': True,
        'permission_class': 'artifact',
        'invocation_mode': 'agent_requested',
    }


def _tool_result(
    *,
    tool_name: str,
    payload: dict[str, Any],
    operation: MemoryConsumptionOperation,
) -> ToolResult:
    status = str(payload.get('status', ExternalKnowledgeResultStatus.ERROR.value))
    refs = _visible_logical_refs(payload)
    receipt_status = _receipt_status(status)
    if receipt_status in {
        MemoryConsumptionStatus.EMPTY,
        MemoryConsumptionStatus.UNAVAILABLE,
        MemoryConsumptionStatus.ERROR,
    }:
        refs = ()
    return build_memory_tool_result(
        tool_name=tool_name,
        payload=payload,
        domain=MemoryDomain.EXTERNAL_KNOWLEDGE,
        invocation_id=f'external-{operation.value}:{uuid4().hex}',
        operation=operation,
        receipt_status=receipt_status,
        logical_refs=refs,
        matched_count=_matched_count(payload),
        fallback=str(payload.get('fallback', '') or status),
        truncated=bool(payload.get('truncated', False)),
    )


def _error_result(
    *,
    tool_name: str,
    operation: MemoryConsumptionOperation,
    fallback: str,
) -> ToolResult:
    return _tool_result(
        tool_name=tool_name,
        payload={
            'status': ExternalKnowledgeResultStatus.INVALID_REQUEST.value,
            'fallback': fallback,
            'truncated': False,
        },
        operation=operation,
    )


def _bounded_payload(
    payload: dict[str, Any],
    *,
    max_chars: int,
    item_paths: tuple[tuple[str, ...], ...],
) -> dict[str, Any]:
    bounded = json.loads(json.dumps(payload, ensure_ascii=False))
    while serialized_payload_size_bytes(bounded) > max_chars:
        if drop_last_list_item(bounded, item_paths):
            bounded['truncated'] = True
            bounded['status'] = ExternalKnowledgeResultStatus.PARTIAL.value
            continue
        body = bounded.get('body')
        if isinstance(body, str) and body:
            excess = serialized_payload_size_bytes(bounded) - max_chars
            body_bytes = len(body.encode('utf-8'))
            bounded['body'], _ = truncate_utf8(
                body,
                max(0, body_bytes - max(256, excess)),
            )
            bounded['truncated'] = True
            bounded['status'] = ExternalKnowledgeResultStatus.PARTIAL.value
            continue
        bounded = {
            'status': ExternalKnowledgeResultStatus.PARTIAL.value,
            'logical_ref': str(bounded.get('logical_ref', '') or '')[:320],
            'fallback': 'tool_result_budget',
            'truncated': True,
        }
        break
    return bounded
def _visible_logical_refs(payload: Mapping[str, Any]) -> tuple[str, ...]:
    hits = payload.get('hits')
    if isinstance(hits, list):
        return tuple(
            str(item.get('logical_ref', ''))
            for item in hits
            if isinstance(item, Mapping) and str(item.get('logical_ref', '')).strip()
        )[:32]
    logical_ref = str(payload.get('logical_ref', '') or '').strip()
    return (logical_ref,) if logical_ref and (payload.get('body') or payload.get('relation_page')) else ()


def _matched_count(payload: Mapping[str, Any]) -> int:
    value = payload.get('matched_count', 0)
    return int(value) if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else 0


def _receipt_status(status: str) -> MemoryConsumptionStatus:
    mapping = {
        ExternalKnowledgeResultStatus.SUCCESS.value: MemoryConsumptionStatus.SUCCESS,
        ExternalKnowledgeResultStatus.EMPTY.value: MemoryConsumptionStatus.EMPTY,
        ExternalKnowledgeResultStatus.UNAVAILABLE.value: MemoryConsumptionStatus.UNAVAILABLE,
        ExternalKnowledgeResultStatus.PARTIAL.value: MemoryConsumptionStatus.PARTIAL,
    }
    return mapping.get(status, MemoryConsumptionStatus.ERROR)
