from __future__ import annotations

from typing import Any

from ownpi.llm.base import LLMClient
from ownpi.llm.registry import register_llm_provider
from ownpi.runtime.native_tools.provider import (
    FrozenProviderJson,
    NativeToolProviderCapabilities,
    ProviderOutputCompletion,
    ProviderOutputEnvelope,
    ProviderOutputItem,
    ProviderOutputItemKind,
    ProviderOutputSource,
    ProviderOutputSourceKind,
)
from ownpi.runtime.native_tools.provider_input import NativeHistoryProviderInput
from ownpi.runtime.native_tools.spec import NativeToolCall, NativeToolSpec
from ownpi.schemas.runtime import LLMConfig
from ownpi.utils.language import contains_chinese


class MockLLMClient(LLMClient):
    def __init__(self, config: LLMConfig | None = None) -> None:
        self._config = config

    @property
    def native_tool_provider_capabilities(self) -> NativeToolProviderCapabilities:
        return NativeToolProviderCapabilities(
            native_tool_strict=bool(self._config and self._config.native_tool_strict),
        )

    def generate_text(self, prompt: str) -> str:
        if _prefer_chinese(prompt):
            return "Mock 直接回答：这是一个占位的自然语言响应。请使用 `ownpi setup` 配置真实 LLM。"
        return (
            "Mock direct answer: this is a placeholder natural-language response. "
            "Configure a real LLM with `ownpi setup` for actual answers."
        )

    def generate_json(self, prompt: str) -> dict[str, Any]:
        prompt_lower = prompt.lower()
        if 'task interpreter' in prompt_lower:
            preferred_tier = None
            if 'forced tier constraint: full' in prompt_lower:
                preferred_tier = 'full'
            elif 'forced tier constraint: direct' in prompt_lower:
                preferred_tier = 'direct'
            elif 'quick_summary' in prompt_lower or 'direct_qa' in prompt_lower or 'rewrite' in prompt_lower:
                preferred_tier = 'direct'
            elif 'paper_summarization' in prompt_lower or 'literature_review' in prompt_lower:
                preferred_tier = 'full'
            elif 'bci_engineering' in prompt_lower or 'research' in prompt_lower:
                preferred_tier = 'full'
            return {
                'task_kind': 'mock_task',
                'problem_shape': 'analysis' if 'analysis' in prompt_lower else 'general',
                'desired_deliverable': 'mock deliverable',
                'complexity': 'medium',
                'needs_code_execution': any(term in prompt_lower for term in ('patch', 'implement', 'codebase', 'repo')),
                'needs_durable_state': preferred_tier == 'full',
                'needs_claim_boundary_review': 'claim' in prompt_lower or 'paper' in prompt_lower,
                'preferred_execution_tier': preferred_tier,
                'planning_constraints': [],
                'interpretation_rationale': 'Mock interpreter output for active planning.',
                'confidence': 0.7,
            }
        if "requested_skill_expansions" in prompt and "lightweight context selector" in prompt:
            requested_skill_expansions: list[str] = []
            prompt_lower = prompt.lower()
            if "bci" in prompt_lower and "bci preprocessing" in prompt_lower:
                requested_skill_expansions.append("BCI Preprocessing")
            if "review" in prompt_lower and "neuro review checklist" in prompt_lower:
                requested_skill_expansions.append("Neuro Review Checklist")
            return {
                "requested_skill_expansions": requested_skill_expansions,
                "rationale": "Mock runtime selected a small amount of context for disclosure.",
            }
        mode = "neuroscience_discovery" if "neuroscience_discovery" in prompt_lower else "bci_engineering"
        task_family = "seizure_detection" if "seizure" in prompt_lower else "general_bci_task"
        signal_modality = "EEG"
        objective = "模拟解析后的自然语言请求。" if _prefer_chinese(prompt) else "Mock-interpreted natural language request."
        return {
            "task_id": "chat_mock_task",
            "mode": mode,
            "signal_modality": signal_modality,
            "task_family": task_family,
            "dataset": {
                "name": "unspecified_dataset",
                "path": "./data",
            },
            "objective": objective,
        }

    def generate_structured_json(
        self,
        prompt: str,
        *,
        schema: dict[str, Any],
        request_kind: str,
        schema_name: str | None = None,
    ) -> dict[str, Any]:
        del request_kind, schema_name
        payload = self.generate_json(prompt)
        properties = schema.get('properties') if isinstance(schema, dict) else None
        if isinstance(properties, dict) and 'structured_issues' in properties:
            payload.setdefault('structured_issues', [])
        return payload

    def complete_with_native_tools(
        self,
        *,
        messages: list[dict[str, Any]],
        native_conversation_input: NativeHistoryProviderInput,
        tools: list[NativeToolSpec],
        request_kind: str = 'native_tool_turn',
        tool_choice: str = 'auto',
    ) -> ProviderOutputEnvelope:
        if tool_choice not in {'auto', 'required', 'none'}:
            raise ValueError('native tool_choice must be auto, required or none')
        tool_names = {tool.name for tool in tools} if tool_choice != 'none' else set()
        if {'submit_feedback', 'next_stage'} <= tool_names:
            if not _successful_tool_result_seen(
                native_conversation_input,
                tool_name='submit_feedback',
            ):
                call = NativeToolCall(
                    tool_name='submit_feedback',
                    input={
                        'content': (
                            'No findings.\n\n'
                            'The accepted mock evidence supports advancing within the '
                            'structural validation boundary.'
                        ),
                    },
                    provider_call_id='mock-analysis-feedback-call',
                    request_id='mock-analysis-feedback-call',
                )
                return _mock_provider_output(
                    (_tool_call_response_item(call),),
                    usage={'input_tokens': 1, 'output_tokens': 1},
                )
            call = NativeToolCall(
                tool_name='next_stage',
                input={'target_stage': 'output_synthesis'},
                provider_call_id='mock-next-stage-call',
                request_id='mock-next-stage-call',
            )
            return _mock_provider_output(
                (_tool_call_response_item(call),),
                usage={'input_tokens': 1, 'output_tokens': 1},
            )
        del (
            messages,
            native_conversation_input,
            tools,
            request_kind,
            tool_choice,
        )
        message_payload = {
            'role': 'assistant',
            'content': 'Mock native tool turn completed without tool calls.',
        }
        return _mock_provider_output(
            (
                ProviderOutputItem(
                    kind=ProviderOutputItemKind.MESSAGE,
                    sequence_index=0,
                    provider_type='message',
                    provider_payload=FrozenProviderJson.from_value(message_payload),
                    text='Mock native tool turn completed without tool calls.',
                ),
            ),
        )
def _build_mock_client(config: LLMConfig) -> MockLLMClient:
    return MockLLMClient(config=config)


def _prefer_chinese(prompt: str) -> bool:
    if "Respond in Chinese" in prompt:
        return True
    if "Respond in English" in prompt:
        return False
    return contains_chinese(prompt)


def _extract_json_object(prompt: str, label: str) -> dict[str, Any]:
    marker = f'{label}: '
    start = prompt.find(marker)
    if start == -1:
        return {}
    start += len(marker)
    raw = prompt[start:].strip()
    try:
        payload = __import__('json').loads(raw)
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def _extract_prompt_line_value(prompt: str, label: str) -> str | None:
    prefix = f'{label}:'
    for line in prompt.splitlines():
        if line.startswith(prefix):
            value = line[len(prefix):].strip()
            return value or None
    return None


def _successful_tool_result_seen(
    provider_input: NativeHistoryProviderInput,
    *,
    tool_name: str,
) -> bool:
    for entry in provider_input.items:
        item = entry.item
        if getattr(item, 'tool_name', '') == tool_name and getattr(item, 'success', False):
            return True
    return False


def _tool_call_response_item(call: NativeToolCall) -> ProviderOutputItem:
    payload = {
        'type': 'function_call',
        'name': call.tool_name,
        'arguments': call.input,
        'call_id': call.provider_call_id or call.request_id,
    }
    return ProviderOutputItem(
        kind=ProviderOutputItemKind.TOOL_CALL,
        sequence_index=0,
        provider_type='function_call',
        provider_payload=FrozenProviderJson.from_value(payload),
        provider_item_id=call.provider_call_id or call.request_id,
        tool_name=call.tool_name,
        call_id=call.provider_call_id,
        request_id=call.request_id,
        raw_arguments=FrozenProviderJson.from_value(call.input).canonical_json,
    )


def _mock_provider_output(
    items: tuple[ProviderOutputItem, ...],
    *,
    usage: dict[str, int] | None = None,
) -> ProviderOutputEnvelope:
    return ProviderOutputEnvelope(
        completed_items=items,
        completion=ProviderOutputCompletion.create(
            status='completed',
            usage=usage,
            provider_metadata={'provider': 'mock'},
            provider_protocol='mock',
        ),
        source=ProviderOutputSource(
            kind=ProviderOutputSourceKind.SYNTHETIC,
            completed_item_payloads=tuple(item.provider_payload for item in items),
        ),
    )


register_llm_provider("mock", _build_mock_client)
