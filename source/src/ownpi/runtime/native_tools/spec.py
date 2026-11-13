from __future__ import annotations

import copy
from dataclasses import asdict, dataclass, field
from typing import Any

from ownpi.runtime.model_content import ModelContentItem, content_items_from_dicts, content_items_to_dicts
from ownpi.runtime.tools.definitions import ToolDefinition
from ownpi.schemas.runtime_tooling import ToolCallBatchPolicy


TURN_EXCLUSIVE_INVOCATION_CONSTRAINT = (
    'Invocation constraint: this tool must be the only tool call in its provider turn.'
)


@dataclass(frozen=True)
class NativeToolSpec:
    name: str
    description: str
    input_schema: dict[str, Any]
    execution_class: str
    side_effect_level: str
    permission_class: str
    read_only: bool = False
    concurrency_safe: bool = False
    batch_policy: ToolCallBatchPolicy = ToolCallBatchPolicy.COMPATIBLE
    source_tool_name: str = ''
    hosted_execution_allowed: bool = True

    def __post_init__(self) -> None:
        policy = self.batch_policy
        if not isinstance(policy, ToolCallBatchPolicy):
            policy = ToolCallBatchPolicy(str(policy or ToolCallBatchPolicy.COMPATIBLE))
            object.__setattr__(self, 'batch_policy', policy)
        if policy is ToolCallBatchPolicy.TURN_EXCLUSIVE:
            description = str(self.description or '').strip()
            if TURN_EXCLUSIVE_INVOCATION_CONSTRAINT not in description:
                object.__setattr__(
                    self,
                    'description',
                    f'{description} {TURN_EXCLUSIVE_INVOCATION_CONSTRAINT}'.strip(),
                )

    @classmethod
    def from_tool_definition(
        cls,
        definition: ToolDefinition,
    ) -> 'NativeToolSpec':
        summary = definition.to_summary()
        abi = definition.tool_abi()
        return cls(
            name=summary.name,
            description=str(abi.description or summary.description or summary.detail or summary.name),
            input_schema=copy.deepcopy(abi.input_schema),
            execution_class=str(summary.execution_class or 'read_only'),
            side_effect_level=str(summary.side_effect_level or 'none'),
            permission_class=str(summary.permission_class or 'default'),
            read_only=bool(summary.read_only),
            concurrency_safe=bool(summary.concurrency_safe),
            batch_policy=summary.batch_policy,
            source_tool_name=summary.name,
        )

    def provider_neutral_schema(self) -> dict[str, Any]:
        return {
            'name': self.name,
            'description': self.description,
            'input_schema': copy.deepcopy(self.input_schema),
        }

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload['input_schema'] = copy.deepcopy(self.input_schema)
        return payload


@dataclass(frozen=True)
class NativeToolCall:
    tool_name: str
    input: dict[str, Any] = field(default_factory=dict)
    provider_call_id: str = ''
    request_id: str = ''
    raw_provider_payload: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            'tool_name': self.tool_name,
            'input': copy.deepcopy(self.input),
            'provider_call_id': self.provider_call_id,
            'request_id': self.request_id,
            'raw_provider_payload': copy.deepcopy(self.raw_provider_payload),
        }


@dataclass(frozen=True)
class NativeToolResult:
    tool_name: str
    request_id: str
    provider_call_id: str = ''
    status: str = 'completed'
    output_text: str = ''
    data: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)
    content_items: tuple[ModelContentItem, ...] = ()

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> 'NativeToolResult':
        return cls(
            tool_name=str(data.get('tool_name', '') or ''),
            request_id=str(data.get('request_id', '') or ''),
            provider_call_id=str(data.get('provider_call_id', '') or ''),
            status=str(data.get('status', 'completed') or 'completed'),
            output_text=str(data.get('output_text', '') or ''),
            data=dict(data.get('data', {})) if isinstance(data.get('data'), dict) else {},
            metadata=dict(data.get('metadata', {})) if isinstance(data.get('metadata'), dict) else {},
            content_items=content_items_from_dicts(data.get('content_items')),
        )

    def to_dict(self, *, redact_image_url: bool = False) -> dict[str, Any]:
        payload = {
            'tool_name': self.tool_name,
            'request_id': self.request_id,
            'provider_call_id': self.provider_call_id,
            'status': self.status,
            'output_text': self.output_text,
            'data': copy.deepcopy(self.data),
            'metadata': copy.deepcopy(self.metadata),
        }
        if self.content_items:
            payload['content_items'] = content_items_to_dicts(
                self.content_items,
                redact_image_url=redact_image_url,
            )
        return payload


@dataclass(frozen=True)
class NativeToolError:
    tool_name: str
    request_id: str
    provider_call_id: str = ''
    failure_class: str = 'tool_runtime_error'
    error_message: str = ''
    recovery_hint: str = ''
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            'tool_name': self.tool_name,
            'request_id': self.request_id,
            'provider_call_id': self.provider_call_id,
            'failure_class': self.failure_class,
            'error_message': self.error_message,
            'recovery_hint': self.recovery_hint,
            'metadata': copy.deepcopy(self.metadata),
        }
