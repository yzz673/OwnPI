from __future__ import annotations

import hashlib
import json
import os
import random
import ssl
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Iterator
from dataclasses import replace
from typing import Any
from urllib.parse import urlparse

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError, ValidationError

from ownpi.llm.anthropic_native_stream import (
    AnthropicMessagesStreamNormalizer,
)
from ownpi.llm.base import LLMClient, current_cancel_checker
from ownpi.llm.cancellation import run_provider_io
from ownpi.llm.output_limits import resolve_output_token_limit
from ownpi.llm.provider_error_classification import classify_provider_http_error
from ownpi.llm.failure_diagnostics import diagnostic_message, sanitize_error_metadata
from ownpi.llm.provider_retry import (
    ProviderRetryBudgetState,
    ProviderRetryKernel,
    ProviderRetryPolicy,
    RetryKind,
    RetryOwner,
    RetryCancelled,
    RetryExhausted,
    RetryableProviderOperation,
)
from ownpi.llm.registry import register_llm_provider
from ownpi.llm.sse_failure import (
    ProviderSSEFailure,
    SSEEventTracker,
    iter_native_sse_events,
    decode_native_json,
    native_blocking_response_error,
    provider_http_failure,
    provider_transport_failure,
    retryable_malformed_response,
)
from ownpi.llm.streaming import CanonicalResponseEvent, CanonicalResponseEventType
from ownpi.llm.telemetry import emit_llm_trace, summarize_prompt
from ownpi.llm.transport import classify_transport_error
from ownpi.runtime.model_content import ModelContentItem, content_items_from_dicts, redact_image_url_value
from ownpi.runtime.limits.projection import MAX_PROVIDER_ERROR_BODY_EXCERPT_CHARS
from ownpi.runtime.limits.resources import MAX_PROVIDER_ERROR_BODY_PARSE_CHARS
from ownpi.runtime.native_tools.adapters import AnthropicNativeToolAdapter
from ownpi.runtime.native_tools.hosted_web_search import (
    HostedWebSearchOptions,
    WebSearchExecutionMode,
    select_web_search_execution_mode,
)
from ownpi.runtime.native_tools.provider import (
    FrozenProviderJson,
    NativeToolProviderCapabilities,
    ProviderOutputCompletion,
    ProviderOutputEnvelope,
    ProviderOutputSource,
    ProviderOutputSourceKind,
)
from ownpi.runtime.native_tools.provider_input import NativeHistoryProviderInput
from ownpi.runtime.native_tools.provider_input_projection import (
    anthropic_history_messages,
    anthropic_system_text,
)
from ownpi.runtime.native_tools.streaming import (
    aggregate_native_response_events,
    blocking_native_response_events,
)
from ownpi.runtime.native_tools.spec import NativeToolSpec
from ownpi.runtime.vision import ImagePreparationError, split_base64_data_url
from ownpi.schemas.runtime import LLMConfig


_ANTHROPIC_VERSION = "2023-06-01"


class _ResponseTextHandlerError(Exception):
    def __init__(self, cause: Exception) -> None:
        super().__init__(str(cause))
        self.cause = cause
def _resolve_required_env(env_name: str | None, *, label: str) -> str:
    if not env_name:
        raise ValueError(f"{label} env var name must be configured.")
    value = os.environ.get(env_name, "").strip()
    if not value:
        raise ValueError(f"Missing {label} environment variable: {env_name}")
    return value


def _resolve_base_url(config: LLMConfig) -> str:
    value = (config.base_url or "").strip()
    if not value and config.base_url_env:
        value = os.environ.get(config.base_url_env, "").strip()
    if not value:
        raise ValueError("Anthropic-compatible provider requires base_url or base_url_env.")
    return value


def _join_endpoint(base_url: str, endpoint_path: str | None) -> str:
    versioned_base = urlparse(base_url).path.rstrip('/').endswith('/v1')
    path = endpoint_path or ('/messages' if versioned_base else '/v1/messages')
    if urlparse(path).scheme:
        return path
    return f"{base_url.rstrip('/')}/{path.lstrip('/')}"


def _extract_anthropic_text(payload: dict[str, Any]) -> str:
    content = payload.get("content")
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""

    chunks: list[str] = []
    for item in content:
        if not isinstance(item, dict):
            continue
        if item.get("type") not in {None, "text"}:
            continue
        text = item.get("text")
        if text is not None:
            chunks.append(str(text))
    return "".join(chunks)


def _strip_json_fences(text: str) -> str:
    stripped = text.strip()
    if not stripped.startswith("```"):
        return stripped
    lines = stripped.splitlines()
    if len(lines) >= 3 and lines[0].startswith("```") and lines[-1].strip() == "```":
        return "\n".join(lines[1:-1]).strip()
    return stripped


def _extract_json_object_text(text: str) -> str:
    stripped = _strip_json_fences(text)
    try:
        parsed = json.loads(stripped)
    except json.JSONDecodeError:
        pass
    else:
        if isinstance(parsed, dict):
            return stripped

    decoder = json.JSONDecoder()
    search_index = 0
    while True:
        object_start = stripped.find("{", search_index)
        if object_start < 0:
            return stripped
        try:
            parsed, _end = decoder.raw_decode(stripped[object_start:])
        except json.JSONDecodeError:
            search_index = object_start + 1
            continue
        if isinstance(parsed, dict):
            return stripped[object_start : object_start + _end]
        search_index = object_start + 1


def _extract_anthropic_usage(payload: dict[str, Any]) -> dict[str, int]:
    usage = payload.get("usage")
    if not isinstance(usage, dict):
        return {}
    result: dict[str, int] = {}
    for key in ("input_tokens", "output_tokens", "total_tokens"):
        value = usage.get(key)
        if isinstance(value, int):
            result[key] = value
    cache_read = usage.get('cache_read_input_tokens')
    if isinstance(cache_read, int):
        result['cache_read_tokens'] = cache_read
    cache_write = usage.get('cache_creation_input_tokens')
    if isinstance(cache_write, int):
        result['cache_write_tokens'] = cache_write
    if "total_tokens" not in result and "input_tokens" in result and "output_tokens" in result:
        result["total_tokens"] = result["input_tokens"] + result["output_tokens"]
    return result


def _summarize_anthropic_response_shape(payload: dict[str, Any]) -> dict[str, Any]:
    content = payload.get("content")
    if isinstance(content, list):
        content_types = [
            str(item.get("type", "<missing>") or "<missing>")
            for item in content
            if isinstance(item, dict)
        ]
        return {
            "top_level_keys": sorted(str(key) for key in payload.keys()),
            "content_kind": "list",
            "content_len": len(content),
            "content_types": content_types[:8],
            "stop_reason": payload.get("stop_reason"),
        }
    return {
        "top_level_keys": sorted(str(key) for key in payload.keys()),
        "content_kind": type(content).__name__,
        "stop_reason": payload.get("stop_reason"),
    }


def _read_http_error_detail(exc: urllib.error.HTTPError) -> dict[str, Any]:
    raw_body = b''
    try:
        raw_body = exc.read()
    except OSError:
        raw_body = b''
    body_text = raw_body.decode('utf-8', errors='replace').strip()
    detail: dict[str, Any] = {
        'http_status': exc.code,
        'http_reason': str(exc.reason or ''),
        'http_error_body_sha256': hashlib.sha256(raw_body).hexdigest(),
        'http_error_body_truncated': (
            len(body_text) > MAX_PROVIDER_ERROR_BODY_EXCERPT_CHARS
        ),
    }
    if not body_text:
        return detail
    detail['http_error_body_excerpt'] = body_text[
        :MAX_PROVIDER_ERROR_BODY_EXCERPT_CHARS
    ]
    detail['_http_error_body'] = body_text
    parse_text = body_text[:MAX_PROVIDER_ERROR_BODY_PARSE_CHARS]
    try:
        parsed = json.loads(parse_text)
    except json.JSONDecodeError:
        return detail
    if isinstance(parsed, dict):
        detail['_http_error_payload'] = parsed
    return detail


def _http_error_trace_detail(detail: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in detail.items()
        if not str(key).startswith('_')
    }


def _messages_trace_text(messages: list[dict[str, Any]]) -> str:
    chunks: list[str] = []
    for message in messages[-6:]:
        role = str(message.get('role', '') or '')
        content = message.get('content')
        if isinstance(content, str):
            text = content
        else:
            text = json.dumps(_redacted_content_for_trace(content), ensure_ascii=False, sort_keys=True)
        chunks.append(f'{role}: {text[:1000]}')
    return '\n'.join(chunks)


def _redacted_content_for_trace(content: Any) -> Any:
    if isinstance(content, list):
        redacted: list[Any] = []
        for item in content:
            if isinstance(item, dict) and item.get('type') == 'input_image':
                updated = dict(item)
                updated['image_url'] = redact_image_url_value(str(updated.get('image_url', '') or ''))
                redacted.append(updated)
            else:
                redacted.append(item)
        return redacted
    return content


def _messages_input_image_count(messages: list[dict[str, Any]]) -> int:
    count = 0
    for message in messages:
        content = message.get('content')
        if isinstance(content, list):
            count += sum(1 for item in content if isinstance(item, dict) and item.get('type') == 'input_image')
    return count


def _anthropic_system_text(messages: list[dict[str, Any]]) -> str:
    chunks = [
        str(message.get('content', '') or '').strip()
        for message in messages
        if str(message.get('role', '') or '') in {'system', 'developer'}
    ]
    return '\n\n'.join(chunk for chunk in chunks if chunk)


def _anthropic_messages(
    messages: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    projected: list[dict[str, Any]] = []
    for message in messages:
        role = str(message.get('role', '') or '').strip()
        if role in {'system', 'developer'}:
            continue
        if role not in {'user', 'assistant'}:
            role = 'user'
        content = message.get('content', '')
        projected.append({'role': role, 'content': _anthropic_provider_content(content)})
    return _normalize_anthropic_projected_messages(projected)


def _anthropic_provider_content(content: Any) -> str | list[dict[str, Any]]:
    items = content_items_from_dicts(content) if isinstance(content, list) else ()
    if not items:
        return content
    return [_anthropic_content_item(item) for item in items]


def _anthropic_content_item(item: ModelContentItem) -> dict[str, Any]:
    if item.type != 'input_image':
        return {'type': 'text', 'text': item.text}
    try:
        media_type, data = split_base64_data_url(item.image_url)
    except ImagePreparationError:
        return {
            'type': 'text',
            'text': f'image content omitted because it could not be prepared: {redact_image_url_value(item.image_url)}',
        }
    return {
        'type': 'image',
        'source': {
            'type': 'base64',
            'media_type': media_type,
            'data': data,
        },
    }


def _normalize_anthropic_projected_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for message in messages:
        role = str(message.get('role', '') or '').strip()
        if role not in {'user', 'assistant'}:
            role = 'user'
        content = _anthropic_content_blocks(message.get('content', ''))
        if role == 'assistant' and not content:
            content = [{'type': 'text', 'text': '[Tool use interrupted]'}]
        if role == 'user':
            content = _hoist_anthropic_tool_results(content)
        if normalized and normalized[-1].get('role') == 'user' and role == 'user':
            previous = _anthropic_content_blocks(normalized[-1].get('content', []))
            merged_content = _hoist_anthropic_tool_results(_join_anthropic_user_content(previous, content))
            normalized[-1] = {
                'role': 'user',
                'content': _compact_anthropic_content(merged_content),
            }
            continue
        if normalized and normalized[-1].get('role') == 'assistant' and role == 'assistant':
            normalized.append({'role': 'user', 'content': '[Provider transcript boundary inserted]'})
        if not normalized and role == 'assistant':
            normalized.append({'role': 'user', 'content': '[Conversation resumed before assistant context]'})
        normalized.append({'role': role, 'content': _compact_anthropic_content(content)})
    return normalized


def _anthropic_content_blocks(content: Any) -> list[dict[str, Any]]:
    if isinstance(content, str):
        return [{'type': 'text', 'text': content}] if content else []
    if isinstance(content, list):
        blocks: list[dict[str, Any]] = []
        for item in content:
            if isinstance(item, dict):
                blocks.append(dict(item))
            elif item:
                blocks.append({'type': 'text', 'text': str(item)})
        return blocks
    if content:
        return [{'type': 'text', 'text': json.dumps(content, ensure_ascii=False, sort_keys=True)}]
    return []


def _compact_anthropic_content(content: list[dict[str, Any]]) -> str | list[dict[str, Any]]:
    if len(content) == 1 and content[0].get('type') == 'text':
        return str(content[0].get('text', '') or '')
    return content


def _join_anthropic_user_content(
    previous: list[dict[str, Any]],
    current: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    if (
        previous
        and current
        and previous[-1].get('type') == 'text'
        and current[0].get('type') == 'text'
    ):
        merged_last = dict(previous[-1])
        merged_last['text'] = f"{str(merged_last.get('text', '') or '')}\n{str(current[0].get('text', '') or '')}"
        return [*previous[:-1], merged_last, *current[1:]]
    return [*previous, *current]


def _hoist_anthropic_tool_results(content: list[dict[str, Any]]) -> list[dict[str, Any]]:
    tool_results: list[dict[str, Any]] = []
    other_blocks: list[dict[str, Any]] = []
    for block in content:
        if block.get('type') == 'tool_result':
            tool_results.append(block)
        else:
            other_blocks.append(block)
    return [*tool_results, *other_blocks]


class AnthropicCompatibleClient(LLMClient):
    """Minimal Anthropic Messages API client for Kimi Code and similar gateways."""

    def __init__(self, config: LLMConfig) -> None:
        # Anthropic-compatible models may omit reasoning effort; the optional
        # user field is ignored until the protocol supports it.
        self._config = config
        self._provider = config.provider
        self._model = config.model
        self._input_modalities = tuple(config.input_modalities)
        self._web_search_execution_mode = select_web_search_execution_mode(
            supports_hosted_web_search=(
                config.supports_hosted_web_search and config.web_search_mode == 'live'
            ),
            web_search_mode=config.web_search_mode,
        )
        self._hosted_web_search_options = HostedWebSearchOptions(
            mode=config.web_search_mode,
            context_size=config.web_search_context_size,
            allowed_domains=tuple(config.web_search_allowed_domains),
            user_location=tuple(config.web_search_user_location),
            search_content_types=tuple(config.web_search_content_types),
            max_uses=config.web_search_max_uses,
        )
        if (
            self._web_search_execution_mode == WebSearchExecutionMode.HOSTED
            and (
                self._hosted_web_search_options.context_size is not None
                or self._hosted_web_search_options.search_content_types
            )
        ):
            raise ValueError(
                'Anthropic hosted web search does not support context size or content type options'
            )
        self._api_key = _resolve_required_env(config.api_key_env, label="API key")
        self._base_url = _resolve_base_url(config)
        self._endpoint_url = _join_endpoint(self._base_url, config.endpoint_path)
        self._request_timeout_seconds = max(1, int(config.request_timeout_seconds))
        self._sse_idle_timeout_seconds = max(
            1,
            min(int(config.sse_idle_timeout_seconds), int(config.request_timeout_seconds)),
        )
        self._retry_policy = ProviderRetryPolicy(
            request_max_retries=config.effective_request_max_retries,
            stream_max_retries=config.effective_stream_max_retries,
            base_delay_seconds=config.retry_backoff_seconds,
            config_source=config.retry_config_source,
        )
        self._retry_sleep: Callable[[float], None] = time.sleep
        self._retry_jitter: Callable[[], float] = lambda: random.uniform(0.9, 1.1)
        self._retry_kernel = ProviderRetryKernel(
            self._retry_policy,
            sleeper=lambda seconds: self._retry_sleep(seconds),
            jitter=lambda: self._retry_jitter(),
            is_cancelled=self._is_cancelled,
        )
        self._output_token_limit = resolve_output_token_limit(config)
        self._max_tokens = self._output_token_limit.effective

    def set_cancel_checker(self, checker: Callable[[], bool] | None) -> None:
        super().set_cancel_checker(checker)

    def _is_cancelled(self) -> bool:
        checker = current_cancel_checker(self)
        return bool(checker is not None and checker())

    def _raise_if_cancelled(self, boundary: str) -> None:
        if self._is_cancelled():
            raise RetryCancelled(f'provider request cancelled at {boundary}')

    def generate_text(self, prompt: str) -> str:
        return self._post_messages(prompt, response_mode="text", request_kind="text")

    def generate_text_response(self, prompt: str, *, request_kind: str) -> str:
        return self._post_messages(prompt, response_mode="text", request_kind=request_kind)

    def generate_text_response_with_handler(
        self,
        prompt: str,
        *,
        request_kind: str,
        response_text_handler: Callable[[str], None],
    ) -> str:
        return self._post_messages(
            prompt,
            response_mode='text',
            request_kind=request_kind,
            response_text_handler=response_text_handler,
        )

    def generate_json(self, prompt: str) -> dict[str, Any]:
        text = self._post_messages(prompt, response_mode="json", request_kind="json")
        return self._parse_json_object(text, request_kind="json")

    def generate_structured_json(
        self,
        prompt: str,
        *,
        schema: dict[str, Any],
        request_kind: str,
        schema_name: str | None = None,
    ) -> dict[str, Any]:
        text = self._post_messages(
            prompt,
            response_mode="structured_json",
            request_kind=request_kind,
            schema=schema,
            schema_name=schema_name,
        )
        payload = self._parse_json_object(text, request_kind=request_kind)
        self._validate_structured_json(
            payload,
            schema=schema,
            request_kind=request_kind,
        )
        return payload

    def generate_structured_json_from_messages(
        self,
        *,
        messages: list[dict[str, Any]],
        prompt_for_fallback: str,
        schema: dict[str, Any],
        request_kind: str,
        schema_name: str | None = None,
    ) -> dict[str, Any]:
        text = self._post_messages(
            prompt_for_fallback,
            response_mode="structured_json",
            request_kind=request_kind,
            schema=schema,
            schema_name=schema_name,
            messages=messages,
        )
        payload = self._parse_json_object(text, request_kind=request_kind)
        self._validate_structured_json(
            payload,
            schema=schema,
            request_kind=request_kind,
        )
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
        return aggregate_native_response_events(
            self.stream_with_native_tools(
                messages=messages,
                native_conversation_input=native_conversation_input,
                tools=tools,
                request_kind=request_kind,
                tool_choice=tool_choice,
            )
        )

    def stream_with_native_tools(
        self,
        *,
        messages: list[dict[str, Any]],
        native_conversation_input: NativeHistoryProviderInput,
        tools: list[NativeToolSpec],
        request_kind: str = 'native_tool_turn',
        tool_choice: str = 'auto',
    ) -> Iterator[CanonicalResponseEvent]:
        if tool_choice not in {'auto', 'required', 'none'}:
            raise ValueError('native tool_choice must be auto, required or none')
        if tool_choice == 'none':
            tools = []
        body = self._native_tool_body(
            native_conversation_input=native_conversation_input,
            tools=tools,
            tool_choice=tool_choice,
        )
        body['stream'] = True
        yield from self._stream_anthropic_native_body(
            body=body,
            prompt_for_trace=_messages_trace_text(messages),
            request_kind=request_kind,
            extra_trace_metadata={
                'input_image_block_count': _messages_input_image_count(messages),
                'tool_choice': tool_choice,
                'web_search_execution_mode': self._request_web_search_execution_mode(
                    tools
                ).value,
            },
        )

    def _stream_anthropic_native_body(
        self,
        *,
        body: dict[str, Any],
        prompt_for_trace: str,
        request_kind: str,
        extra_trace_metadata: dict[str, Any],
    ) -> Iterator[CanonicalResponseEvent]:
        data = json.dumps(body, ensure_ascii=False).encode('utf-8')
        headers = {
            'Content-Type': 'application/json',
            'Accept': 'application/json, text/event-stream',
            'x-api-key': self._api_key,
            'anthropic-version': _ANTHROPIC_VERSION,
            'User-Agent': 'ownpi/0.1',
        }
        attempts = self._retry_policy.total_attempts('stream')
        started_at = time.perf_counter()
        trace_attempts: list[dict[str, Any]] = []
        outer_state = ProviderRetryBudgetState()
        inner_state = ProviderRetryBudgetState()
        sampling_attempt = 1

        def schedule_retry(
            *,
            owner: RetryOwner,
            kind: RetryKind,
            trace_attempt: dict[str, Any],
            retry_after_seconds: float | None = None,
        ) -> tuple[Any, RetryOwner] | None:
            nonlocal inner_state, outer_state, sampling_attempt
            decision = self._retry_kernel.next_decision(
                state=inner_state if owner == 'inner_transport' else outer_state,
                owner=owner,
                kind=kind,
                retry_after_seconds=retry_after_seconds,
            )
            if decision is None and owner == 'inner_transport':
                decision = self._retry_kernel.next_decision(
                    state=outer_state,
                    owner='outer_sampling',
                    kind=kind,
                    retry_after_seconds=retry_after_seconds,
                )
                owner = 'outer_sampling'
            if decision is None:
                return None
            trace_attempt.update(decision.to_trace_metadata())
            self._retry_kernel.wait(decision)
            if owner == 'inner_transport':
                inner_state = decision.state
            else:
                outer_state = decision.state
                sampling_attempt += 1
                inner_state = ProviderRetryBudgetState()
            return decision, owner
        attempt = 0
        while True:
            attempt += 1
            attempt_started_at = time.perf_counter()
            emitted_count = 0
            emitted_delta_chars = 0
            accepted_stream = False
            completion_event: CanonicalResponseEvent | None = None
            try:
                request = urllib.request.Request(
                    self._endpoint_url,
                    data=data,
                    headers=headers,
                    method='POST',
                )
                self._raise_if_cancelled('http_handshake_before_open')
                with run_provider_io(
                    urllib.request.urlopen,
                    request,
                    timeout=self._request_timeout_seconds,
                ) as response:
                    self._raise_if_cancelled('http_handshake_after_open')
                    content_type = str(response.headers.get('Content-Type', '') or '')
                    accepted_stream = 'text/event-stream' in content_type.casefold()
                    if 'text/event-stream' not in content_type.casefold():
                        self._raise_if_cancelled('response_read_before_body')
                        parsed = decode_native_json(run_provider_io(response.read).decode('utf-8'))
                        self._raise_if_cancelled('response_read_after_body')
                        if not isinstance(parsed, dict):
                            raise retryable_malformed_response(
                                error_message='Anthropic native response was not a JSON object',
                            )
                        failure = native_blocking_response_error(parsed, responses=False)
                        if failure is not None:
                            raise failure
                        completed_items = (
                            AnthropicNativeToolAdapter().parse_response_items(parsed)
                        )
                        provider_metadata = {
                                **{key: value for key, value in parsed.items() if key != 'content'},
                                'stop_reason': str(parsed.get('stop_reason') or ''),
                                'api_style': 'anthropic_messages',
                                'response_transport': 'json',
                                'response_content_type': content_type,
                                'max_tokens': self._max_tokens,
                                'max_tokens_source': self._output_token_limit.source,
                                'max_tokens_upper_limit': self._output_token_limit.upper_limit,
                        }
                        completed = ProviderOutputEnvelope(
                            completed_items=completed_items,
                            completion=ProviderOutputCompletion.create(
                                response_id=str(parsed.get('id') or ''),
                                status=str(parsed.get('status') or ''),
                                stop_reason=str(parsed.get('stop_reason') or ''),
                                usage=_extract_anthropic_usage(parsed),
                                provider_metadata=provider_metadata,
                                provider_protocol='anthropic_messages_json',
                            ),
                            source=ProviderOutputSource(
                                kind=ProviderOutputSourceKind.BLOCKING,
                                response_payload=FrozenProviderJson.from_value(parsed),
                                completed_item_payloads=tuple(
                                    item.provider_payload for item in completed_items
                                ),
                            ),
                        )
                        for event in blocking_native_response_events(
                            completed,
                            request_kind=request_kind,
                            provider_protocol='anthropic_messages_json',
                            attempt=attempt,
                        ):
                            if event.type_value == CanonicalResponseEventType.RESPONSE_COMPLETED.value:
                                completion_event = event
                            else:
                                yield event
                        usage = completed.usage
                    else:
                        self._apply_sse_idle_timeout(response)
                        normalizer = AnthropicMessagesStreamNormalizer()
                        tracker = SSEEventTracker()
                        response_error: ProviderSSEFailure | None = None
                        usage: dict[str, int] = {}
                        for event_name, event_payload in iter_native_sse_events(
                            response, check_cancelled=self._raise_if_cancelled,
                        ):
                            tracker.observe(event_name, event_payload)
                            if event_name == 'error':
                                error = event_payload.get('error')
                                if isinstance(error, dict):
                                    response_error = tracker.explicit_error_failure(error)
                                    continue
                            if event_name == 'ping':
                                continue
                            for event in normalizer.feed(
                                event_name,
                                event_payload,
                                attempt=attempt,
                                request_kind=request_kind,
                            ):
                                if event.type_value == CanonicalResponseEventType.USAGE_UPDATED.value:
                                    usage.update(event.usage)
                                if event.type_value == CanonicalResponseEventType.RESPONSE_COMPLETED.value:
                                    event = replace(
                                        event,
                                        provider_metadata={
                                            **dict(event.provider_metadata),
                                            'response_transport': 'sse',
                                            'response_content_type': content_type,
                                            'max_tokens': self._max_tokens,
                                            'max_tokens_source': self._output_token_limit.source,
                                            'max_tokens_upper_limit': (
                                                self._output_token_limit.upper_limit
                                            ),
                                        },
                                    )
                                emitted_count += 1
                                emitted_delta_chars += len(event.delta)
                                if event.type_value == CanonicalResponseEventType.RESPONSE_COMPLETED.value:
                                    completion_event = event
                                else:
                                    yield event
                            if normalizer.completed:
                                break
                        if not normalizer.completed:
                            raise response_error or tracker.failure(
                                failure_kind='premature_eof',
                                failure_class='transient_provider_failure', retryable=True,
                                semantic_disposition='retryable_transport',
                            )
                    trace_attempts.append(
                        {
                            'attempt': attempt,
                            'status': 'success',
                            'elapsed_ms': round(
                                (time.perf_counter() - attempt_started_at) * 1000,
                                2,
                            ),
                            'content_type': content_type,
                        }
                    )
                    self._emit_trace(
                        prompt=prompt_for_trace,
                        response_mode='native_tools',
                        request_kind=request_kind,
                        success=True,
                        attempts=trace_attempts,
                        total_attempts=attempts,
                        elapsed_ms=round((time.perf_counter() - started_at) * 1000, 2),
                        usage=usage,
                        extra_metadata={
                            **extra_trace_metadata,
                            'transport_mode': (
                                'sse'
                                if 'text/event-stream' in content_type.casefold()
                                else 'json'
                            ),
                            'request_payload_char_count': len(data),
                        },
                    )
                    if completion_event is not None:
                        yield completion_event
                    return
            except urllib.error.HTTPError as exc:
                detail = _read_http_error_detail(exc)
                detail_text = str(detail.get('_http_error_body') or exc.reason)
                classification = classify_provider_http_error(
                    exc.code,
                    detail_text,
                    error_payload=detail.get('_http_error_payload'),
                )
                trace_attempts.append(
                    {
                        'attempt': attempt,
                        'sampling_attempt': sampling_attempt,
                        'status': 'http_error',
                        'elapsed_ms': round(
                            (time.perf_counter() - attempt_started_at) * 1000,
                            2,
                        ),
                        'error_type': type(exc).__name__,
                        **classification.to_trace_metadata(),
                        **_http_error_trace_detail(detail),
                    }
                )
                next_retry = None
                if classification.retryable and classification.retry_kind is not None:
                    next_retry = schedule_retry(
                        owner=classification.retry_owner or 'inner_transport',
                        kind=classification.retry_kind,
                        trace_attempt=trace_attempts[-1],
                        retry_after_seconds=(
                            self._http_retry_after_seconds(exc)
                            or classification.retry_after_seconds
                        ),
                    )
                if next_retry is not None:
                    continue
                self._emit_native_stream_failure_trace(
                    prompt=prompt_for_trace,
                    request_kind=request_kind,
                    attempts=trace_attempts,
                    total_attempts=attempts,
                    started_at=started_at,
                    exc=exc,
                    extra_metadata=extra_trace_metadata,
                )
                raise provider_http_failure(
                    classification=classification,
                    detail=detail_text,
                ) from exc
            except (ProviderSSEFailure, json.JSONDecodeError) as exc:
                if isinstance(exc, json.JSONDecodeError):
                    exc = retryable_malformed_response(error_message=str(exc))
                trace_attempts.append(
                    {
                        'attempt': attempt,
                        'sampling_attempt': sampling_attempt,
                        'status': (
                            'stream_retryable_error' if exc.retryable else 'stream_terminal_error'
                        ),
                        'elapsed_ms': round(
                            (time.perf_counter() - attempt_started_at) * 1000,
                            2,
                        ),
                        'error_type': type(exc).__name__,
                        **exc.to_trace_metadata(),
                    }
                )
                next_retry = None
                if exc.retryable:
                    next_retry = schedule_retry(
                        owner='outer_sampling',
                        kind='stream',
                        trace_attempt=trace_attempts[-1],
                        retry_after_seconds=exc.retry_after_seconds,
                    )
                if next_retry is not None:
                    if emitted_count:
                        yield _anthropic_attempt_discarded_event(
                            request_kind=request_kind,
                            attempt=attempt,
                            event_count=emitted_count,
                            delta_char_count=emitted_delta_chars,
                        )
                    continue
                self._emit_native_stream_failure_trace(
                    prompt=prompt_for_trace,
                    request_kind=request_kind,
                    attempts=trace_attempts,
                    total_attempts=attempts,
                    started_at=started_at,
                    exc=exc,
                    extra_metadata=extra_trace_metadata,
                )
                raise exc
            except (urllib.error.URLError, ssl.SSLError, OSError) as exc:
                transport = classify_transport_error(exc)
                trace_attempts.append(
                    {
                        'attempt': attempt,
                        'sampling_attempt': sampling_attempt,
                        'status': 'transport_error',
                        'elapsed_ms': round(
                            (time.perf_counter() - attempt_started_at) * 1000,
                            2,
                        ),
                        'error_type': type(exc).__name__,
                        'retryable': transport.retriable,
                    }
                )
                retry_kind: RetryKind = 'stream' if accepted_stream else 'request'
                next_retry = None
                if transport.retriable:
                    next_retry = schedule_retry(
                        owner='outer_sampling' if accepted_stream else 'inner_transport',
                        kind=retry_kind,
                        trace_attempt=trace_attempts[-1],
                    )
                if next_retry is not None:
                    if emitted_count:
                        yield _anthropic_attempt_discarded_event(
                            request_kind=request_kind,
                            attempt=attempt,
                            event_count=emitted_count,
                            delta_char_count=emitted_delta_chars,
                        )
                    continue
                self._emit_native_stream_failure_trace(
                    prompt=prompt_for_trace,
                    request_kind=request_kind,
                    attempts=trace_attempts,
                    total_attempts=attempts,
                    started_at=started_at,
                    exc=exc,
                    extra_metadata=extra_trace_metadata,
                )
                raise provider_transport_failure(
                    exception=exc,
                    phase=transport.phase,
                    kind=transport.kind,
                    detail=transport.reason_message or str(exc),
                    retryable=transport.retriable,
                ) from exc
        raise RuntimeError('Anthropic native streaming request failed unexpectedly')

    @staticmethod
    def _response_socket_candidates(response: Any) -> list[Any]:
        candidates: list[Any] = []
        seen: set[int] = {id(response)}
        queue: list[tuple[Any, int]] = [(response, 0)]
        while queue:
            current, depth = queue.pop(0)
            if depth >= 4:
                continue
            for attr in ('socket', '_sock', 'fp', 'raw'):
                child = getattr(current, attr, None)
                if child is None or id(child) in seen:
                    continue
                seen.add(id(child))
                candidates.append(child)
                queue.append((child, depth + 1))
        return candidates

    def _apply_sse_idle_timeout(self, response: Any) -> bool:
        timeout_seconds = float(self._sse_idle_timeout_seconds)
        for candidate in self._response_socket_candidates(response):
            setter = getattr(candidate, 'settimeout', None)
            if not callable(setter):
                continue
            try:
                setter(timeout_seconds)
                return True
            except (OSError, TypeError, ValueError):
                continue
        return False

    def _emit_native_stream_failure_trace(
        self,
        *,
        prompt: str,
        request_kind: str,
        attempts: list[dict[str, Any]],
        total_attempts: int,
        started_at: float,
        exc: BaseException,
        extra_metadata: dict[str, Any],
    ) -> None:
        self._emit_trace(
            prompt=prompt,
            response_mode='native_tools',
            request_kind=request_kind,
            success=False,
            attempts=attempts,
            total_attempts=total_attempts,
            elapsed_ms=round((time.perf_counter() - started_at) * 1000, 2),
            error_type=type(exc).__name__,
            error_message=str(exc)[:400],
            extra_metadata={
                **extra_metadata,
                'transport_mode': 'sse',
            },
        )

    def measure_native_tool_request(
        self,
        *,
        messages: list[dict[str, Any]],
        native_conversation_input: NativeHistoryProviderInput,
        tools: list[NativeToolSpec],
    ) -> Any:
        del messages
        from ownpi.runtime.native_tools.provider_request_measurement import (
            measure_native_provider_request,
            with_model_visible_request_tokens,
            with_serialized_request_bytes,
        )

        measurement = measure_native_provider_request(
            conversation_input=native_conversation_input,
            tool_specs=tools,
            provider_id=self._provider,
            model_id=self._model,
            request_static_properties={
                'api_style': 'anthropic_messages',
                'tool_choice': 'auto',
                'web_search_execution_mode': self._request_web_search_execution_mode(
                    tools
                ).value,
                'hosted_web_search_spec_digest': (
                    self._hosted_web_search_options.anthropic_digest
                    if self._request_web_search_execution_mode(tools)
                    == WebSearchExecutionMode.HOSTED
                    else ''
                ),
            },
        )
        body = self._native_tool_body(
            native_conversation_input=native_conversation_input,
            tools=tools,
        )
        def project(value: NativeHistoryProviderInput) -> list[dict[str, Any]]:
            return anthropic_history_messages(value, prefix_messages=[
                {'role': str(item.get('role') or 'user'),
                 'content': _anthropic_provider_content(item.get('content', ''))}
                for item in value.immutable_prefix_messages
            ])
        measurement = with_model_visible_request_tokens(
            measurement, conversation_input=native_conversation_input,
            serialized_request=body, project=project, modalities=self._input_modalities,
        )
        return with_serialized_request_bytes(measurement, serialized_request=body)

    def _native_tool_body(
        self,
        *,
        native_conversation_input: NativeHistoryProviderInput,
        tools: list[NativeToolSpec],
        tool_choice: str = 'auto',
    ) -> dict[str, Any]:
        native_conversation_input = native_conversation_input.for_modalities(self._input_modalities)
        if tool_choice == 'none':
            tools = []
        request_web_search_mode = self._request_web_search_execution_mode(tools)
        provider_tools: list[dict[str, Any]] = []
        adapter = AnthropicNativeToolAdapter()
        for spec in tools:
            if (
                spec.name == 'web_search'
                and request_web_search_mode == WebSearchExecutionMode.HOSTED
            ):
                hosted_spec = self._hosted_web_search_options.build_anthropic_spec()
                if hosted_spec is None:
                    raise RuntimeError(
                        'Anthropic hosted web-search capability has no request spec'
                    )
                provider_tools.append(hosted_spec)
            else:
                provider_tools.extend(adapter.build_provider_tools((spec,)))
        body: dict[str, Any] = {
            'model': self._model,
            'max_tokens': self._max_tokens,
            'messages': anthropic_history_messages(
                native_conversation_input,
                prefix_messages=[
                    {
                        'role': str(item.get('role') or 'user'),
                        'content': _anthropic_provider_content(item.get('content', '')),
                    }
                    for item in native_conversation_input.immutable_prefix_messages
                ],
            ),
            'tools': provider_tools,
        }
        if tool_choice == 'none':
            body.pop('tools')
        if tool_choice == 'required':
            if len(tools) != 1:
                raise ValueError('required Anthropic atomic tool call requires exactly one tool')
            body['tool_choice'] = {
                'type': 'tool',
                'name': tools[0].name,
                'disable_parallel_tool_use': True,
            }
        system_text = anthropic_system_text(native_conversation_input)
        if system_text:
            body['system'] = system_text
        return body

    @property
    def supports_native_tool_calling(self) -> bool:
        return True

    @property
    def supports_image_input(self) -> bool:
        return 'image' in self._input_modalities

    @property
    def native_tool_provider_capabilities(self) -> NativeToolProviderCapabilities:
        return NativeToolProviderCapabilities(
            native_tool_search_output=False,
            supports_parallel_tool_calls=True,
            web_search_execution_mode=self._web_search_execution_mode,
            hosted_web_search_spec_digest=(
                self._hosted_web_search_options.anthropic_digest
                if self._web_search_execution_mode == WebSearchExecutionMode.HOSTED
                else ''
            ),
        )

    def _request_web_search_execution_mode(
        self,
        tools: list[NativeToolSpec],
    ) -> WebSearchExecutionMode:
        web_search_specs = tuple(spec for spec in tools if spec.name == 'web_search')
        if not web_search_specs:
            return WebSearchExecutionMode.DISABLED
        if not all(spec.hosted_execution_allowed for spec in web_search_specs):
            return WebSearchExecutionMode.LOCAL_FALLBACK
        return self._web_search_execution_mode

    def _post_messages(
        self,
        prompt: str,
        *,
        response_mode: str,
        request_kind: str,
        schema: dict[str, Any] | None = None,
        schema_name: str | None = None,
        messages: list[dict[str, Any]] | None = None,
        response_text_handler: Callable[[str], None] | None = None,
        ) -> str:
        system_prompt = self._build_system_prompt(
            response_mode=response_mode,
            schema=schema,
            schema_name=schema_name,
        )
        projected_messages = _anthropic_messages(messages) if messages is not None else [{"role": "user", "content": prompt}]
        extra_system = _anthropic_system_text(messages or [])
        system_text = '\n\n'.join(part for part in (system_prompt, extra_system) if part.strip())
        prompt_for_trace = _messages_trace_text(messages) if messages is not None else prompt
        body = {
            "model": self._model,
            "max_tokens": self._max_tokens,
            "system": system_text,
            "messages": projected_messages,
        }
        parsed = self._post_raw_body(
            body=body,
            prompt_for_trace=prompt_for_trace,
            response_mode=response_mode,
            request_kind=request_kind,
            extra_trace_metadata={'input_image_block_count': _messages_input_image_count(messages or [])},
            response_text_handler=response_text_handler,
        )
        text = _extract_anthropic_text(parsed)
        if not text:
            response_shape = _summarize_anthropic_response_shape(parsed)
            raise RuntimeError(
                f"Anthropic-compatible {request_kind} response did not contain text content; "
                f"response_shape={response_shape}"
            )
        return text

    def _post_raw_body(
        self,
        *,
        body: dict[str, Any],
        prompt_for_trace: str,
        response_mode: str,
        request_kind: str,
        extra_trace_metadata: dict[str, Any] | None = None,
        response_text_handler: Callable[[str], None] | None = None,
    ) -> dict[str, Any]:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "x-api-key": self._api_key,
            "anthropic-version": _ANTHROPIC_VERSION,
            "User-Agent": "ownpi/0.1",
        }

        started_at = time.perf_counter()
        trace_attempts: list[dict[str, Any]] = []
        def operation(sampling_attempt: int) -> dict[str, Any]:
            return self._post_raw_body_attempt(
                data=data,
                headers=headers,
                prompt_for_trace=prompt_for_trace,
                response_mode=response_mode,
                request_kind=request_kind,
                trace_attempts=trace_attempts,
                sampling_attempt=sampling_attempt,
                response_text_handler=response_text_handler,
            )

        def on_retry(
            decision: Any,
            _failure: RetryableProviderOperation,
        ) -> None:
            if trace_attempts:
                trace_attempts[-1].update(decision.to_trace_metadata())

        try:
            parsed, _state = self._retry_kernel.run_nested(
                operation,
                kind='request',
                on_retry=on_retry,
            )
        except ProviderSSEFailure as failure:
            cause = failure.__cause__ or failure
            final_attempt = trace_attempts[-1] if trace_attempts else {}
            self._emit_trace(
                prompt=prompt_for_trace,
                response_mode=response_mode,
                request_kind=request_kind,
                success=False,
                attempts=trace_attempts,
                total_attempts=len(trace_attempts),
                elapsed_ms=round((time.perf_counter() - started_at) * 1000, 2),
                error_type=type(cause).__name__,
                error_message=(
                    f"HTTP {cause.code}: {cause.reason}"
                    if isinstance(cause, urllib.error.HTTPError)
                    else str(cause)[:400]
                ),
                error_detail={
                    key: value
                    for key, value in final_attempt.items()
                    if key.startswith('http_')
                },
                extra_metadata={**dict(extra_trace_metadata or {}), **failure.to_trace_metadata()},
            )
            raise failure
        except RetryExhausted as exc:
            failure = exc.last_failure.cause
            if isinstance(failure, RetryExhausted):
                failure = failure.last_failure.cause
            final_attempt = trace_attempts[-1] if trace_attempts else {}
            failure_metadata = (
                failure.to_trace_metadata()
                if isinstance(failure, ProviderSSEFailure)
                else dict(exc.last_failure.trace_metadata)
            )
            self._emit_trace(
                prompt=prompt_for_trace,
                response_mode=response_mode,
                request_kind=request_kind,
                success=False,
                attempts=trace_attempts,
                total_attempts=len(trace_attempts),
                elapsed_ms=round((time.perf_counter() - started_at) * 1000, 2),
                error_type=type(failure).__name__,
                error_message=str(
                    final_attempt.get('error_message') or failure
                )[:400],
                error_detail={
                    key: value
                    for key, value in final_attempt.items()
                    if key.startswith('http_')
                },
                extra_metadata={
                    **dict(extra_trace_metadata or {}),
                    **failure_metadata,
                    **exc.outcome.to_trace_metadata(),
                },
            )
            if isinstance(failure, ProviderSSEFailure):
                raise failure
            raise failure
        except RuntimeError as failure:
            final_attempt = trace_attempts[-1] if trace_attempts else {}
            self._emit_trace(
                prompt=prompt_for_trace,
                response_mode=response_mode,
                request_kind=request_kind,
                success=False,
                attempts=trace_attempts,
                total_attempts=len(trace_attempts),
                elapsed_ms=round((time.perf_counter() - started_at) * 1000, 2),
                error_type=str(final_attempt.get('error_type') or type(failure).__name__),
                error_message=str(final_attempt.get('error_message') or failure)[:400],
                extra_metadata=extra_trace_metadata,
            )
            raise

        self._emit_trace(
            prompt=prompt_for_trace,
            response_mode=response_mode,
            request_kind=request_kind,
            success=True,
            attempts=trace_attempts,
            total_attempts=len(trace_attempts),
            elapsed_ms=round((time.perf_counter() - started_at) * 1000, 2),
            usage=_extract_anthropic_usage(parsed),
            extra_metadata=extra_trace_metadata,
        )
        return parsed

    def _post_raw_body_attempt(
        self,
        *,
        data: bytes,
        headers: dict[str, str],
        prompt_for_trace: str,
        response_mode: str,
        request_kind: str,
        trace_attempts: list[dict[str, Any]],
        sampling_attempt: int,
        response_text_handler: Callable[[str], None] | None,
    ) -> dict[str, Any]:
        attempt = len(trace_attempts) + 1
        attempt_started_at = time.perf_counter()
        request = urllib.request.Request(
            self._endpoint_url,
            data=data,
            headers=headers,
            method="POST",
        )
        try:
            self._raise_if_cancelled('http_handshake_before_open')
            with run_provider_io(
                urllib.request.urlopen,
                request,
                timeout=self._request_timeout_seconds,
            ) as response:
                self._raise_if_cancelled('http_handshake_after_open')
                self._raise_if_cancelled('response_read_before_body')
                raw_body = run_provider_io(response.read).decode("utf-8")
                self._raise_if_cancelled('response_read_after_body')
                parsed = json.loads(raw_body)
                if not isinstance(parsed, dict):
                    failure = retryable_malformed_response(
                        error_message=(
                            f"Anthropic-compatible {request_kind} response was not a JSON object."
                        )
                    )
                    trace_attempts.append({
                        "attempt": attempt,
                        "sampling_attempt": sampling_attempt,
                        "status": "malformed_response",
                        "elapsed_ms": round((time.perf_counter() - attempt_started_at) * 1000, 2),
                        "retryable": True,
                        **failure.to_trace_metadata(),
                        "error_type": "NonObjectResponse",
                    })
                    raise RetryableProviderOperation(
                        owner='inner_transport',
                        kind='request',
                        cause=failure,
                        trace_metadata=failure.to_trace_metadata(),
                    )
                if response_mode != 'native_tools' and not _extract_anthropic_text(parsed):
                    response_shape = _summarize_anthropic_response_shape(parsed)
                    failure = retryable_malformed_response(
                        error_message=(
                            f"Anthropic-compatible {request_kind} response did not contain text content; "
                            f"response_shape={response_shape}"
                        )
                    )
                    trace_attempts.append({
                        "attempt": attempt,
                        "sampling_attempt": sampling_attempt,
                        "status": "malformed_response",
                        "elapsed_ms": round((time.perf_counter() - attempt_started_at) * 1000, 2),
                        "response_shape": response_shape,
                        "retryable": True,
                        **failure.to_trace_metadata(),
                        "error_type": "MissingTextContent",
                    })
                    raise RetryableProviderOperation(
                        owner='inner_transport',
                        kind='request',
                        cause=failure,
                        trace_metadata=failure.to_trace_metadata(),
                    )
                if response_text_handler is not None:
                    try:
                        self._handle_text_response(
                            _extract_anthropic_text(parsed),
                            response_text_handler=response_text_handler,
                        )
                    except Exception as exc:
                        raise _ResponseTextHandlerError(exc) from exc
                trace_attempts.append({
                    "attempt": attempt,
                    "sampling_attempt": sampling_attempt,
                    "status": "success",
                    "elapsed_ms": round((time.perf_counter() - attempt_started_at) * 1000, 2),
                })
                return parsed
        except _ResponseTextHandlerError as exc:
            raise exc.cause
        except urllib.error.HTTPError as exc:
            detail = _read_http_error_detail(exc)
            detail_text = str(detail.get('_http_error_body') or exc.reason)
            classification = classify_provider_http_error(
                exc.code,
                detail_text,
                error_payload=detail.get('_http_error_payload'),
            )
            failure = provider_http_failure(
                classification=classification,
                detail=detail_text,
            )
            trace_attempts.append({
                "attempt": attempt,
                "sampling_attempt": sampling_attempt,
                "status": "http_error",
                "elapsed_ms": round((time.perf_counter() - attempt_started_at) * 1000, 2),
                "error_type": type(exc).__name__,
                **classification.to_trace_metadata(),
                **_http_error_trace_detail(detail),
            })
            if not classification.retryable or classification.retry_kind is None:
                raise failure from exc
            raise RetryableProviderOperation(
                owner=classification.retry_owner or 'inner_transport',
                kind=classification.retry_kind,
                cause=failure,
                trace_metadata=failure.to_trace_metadata(),
                retry_after_seconds=(
                    self._http_retry_after_seconds(exc)
                    or classification.retry_after_seconds
                ),
            ) from exc
        except (urllib.error.URLError, ssl.SSLError, OSError) as exc:
            transport = classify_transport_error(exc)
            trace_attempts.append({
                "attempt": attempt,
                "sampling_attempt": sampling_attempt,
                "status": "transport_error",
                "elapsed_ms": round((time.perf_counter() - attempt_started_at) * 1000, 2),
                "error_type": type(exc).__name__,
                "retryable": transport.retriable,
            })
            failure = provider_transport_failure(
                exception=exc,
                phase=transport.phase,
                kind=transport.kind,
                detail=transport.reason_message or str(exc),
                retryable=transport.retriable,
            )
            if not transport.retriable:
                raise failure from exc
            raise RetryableProviderOperation(
                owner='inner_transport',
                kind='request',
                cause=failure,
                trace_metadata=failure.to_trace_metadata(),
            ) from exc
        except json.JSONDecodeError as exc:
            failure = retryable_malformed_response(
                error_message='Anthropic-compatible response was not valid JSON.'
            )
            trace_attempts.append({
                "attempt": attempt,
                "sampling_attempt": sampling_attempt,
                "status": "parse_error",
                "elapsed_ms": round((time.perf_counter() - attempt_started_at) * 1000, 2),
                "error_type": type(exc).__name__,
                **failure.to_trace_metadata(),
            })
            raise RetryableProviderOperation(
                owner='inner_transport',
                kind='request',
                cause=failure,
                trace_metadata=failure.to_trace_metadata(),
            ) from exc

    @staticmethod
    def _http_retry_after_seconds(exc: urllib.error.HTTPError) -> float | None:
        raw_value = exc.headers.get('Retry-After') if exc.headers is not None else None
        if raw_value is None:
            return None
        try:
            return max(0.0, float(str(raw_value).strip()))
        except ValueError:
            return None

    def _emit_trace(
        self,
        *,
        prompt: str,
        response_mode: str,
        request_kind: str,
        success: bool,
        attempts: list[dict[str, Any]],
        total_attempts: int,
        elapsed_ms: float,
        error_type: str | None = None,
        error_message: str | None = None,
        error_detail: dict[str, Any] | None = None,
        usage: dict[str, int] | None = None,
        extra_metadata: dict[str, Any] | None = None,
    ) -> None:
        parsed_url = urlparse(self._endpoint_url)
        attempts = [sanitize_error_metadata(item) for item in attempts]
        error_message = diagnostic_message(error_message) if error_message is not None else None
        extra_metadata = sanitize_error_metadata(extra_metadata or {})
        error_detail = sanitize_error_metadata(error_detail or {})
        usage_payload = dict(usage or {})
        retried = any('retry_delay_seconds' in attempt for attempt in attempts)
        request_retry_count = sum(
            1
            for attempt in attempts
            if 'retry_delay_seconds' in attempt
            and attempt.get('retry_kind') == 'request'
        )
        sampling_retry_count = sum(
            1
            for attempt in attempts
            if 'retry_delay_seconds' in attempt
            and attempt.get('retry_kind') == 'stream'
        )
        stream_attempt_count = sum(
            1
            for attempt in attempts
            if bool(attempt.get('stream_accepted'))
            or str(attempt.get('content_type') or '').casefold().startswith(
                'text/event-stream'
            )
            or str(attempt.get('status') or '').startswith('stream_')
        )
        retry_exhausted = (
            not success
            and bool(attempts[-1].get('retryable'))
            if attempts
            else False
        )
        emit_llm_trace(
            {
                "event_type": "request_completed",
                "provider": "anthropic_compatible",
                "model": self._model,
                "api_style": "anthropic_messages",
                "request_kind": request_kind,
                "response_mode": response_mode,
                "endpoint_host": parsed_url.netloc,
                "endpoint_path": parsed_url.path,
                "timeout_seconds": self._request_timeout_seconds,
                "request_max_retries": self._retry_policy.request_max_retries,
                "stream_max_retries": self._retry_policy.stream_max_retries,
                "retry_config_source": self._retry_policy.config_source,
                "total_attempts_configured": total_attempts,
                "attempt_count": len(attempts),
                "request_attempt_count": len(attempts),
                "stream_attempt_count": stream_attempt_count,
                "retried": retried,
                "retry_recovery_count": int(success and retried),
                "request_retry_count": request_retry_count,
                "same_turn_sampling_retry_count": sampling_retry_count,
                "same_turn_sampling_recovery_count": int(
                    success and sampling_retry_count > 0
                ),
                "retry_exhaustion_count": int(retry_exhausted),
                "deterministic_stop_count": int(
                    not success and not retry_exhausted
                ),
                "success": success,
                "elapsed_ms": elapsed_ms,
                "prompt_char_count": len(prompt),
                "prompt_line_count": len(prompt.splitlines()),
                "prompt_head": summarize_prompt(prompt),
                **dict(extra_metadata or {}),
                "attempts": attempts,
                "usage": usage_payload,
                **usage_payload,
                "error_type": error_type,
                "error_message": error_message,
                "error_detail": dict(error_detail or {}),
            }
        )

    def _build_system_prompt(
        self,
        *,
        response_mode: str,
        schema: dict[str, Any] | None,
        schema_name: str | None,
    ) -> str:
        if response_mode == "text":
            return "You are a precise coding agent. Answer directly and avoid unnecessary text."
        if response_mode == "structured_json" and schema is not None:
            schema_label = schema_name or "structured_response"
            schema_json = json.dumps(schema, ensure_ascii=False, sort_keys=True)
            return (
                "Return exactly one single JSON object. Do not include markdown, code fences, prose, or commentary. "
                f"The JSON object must match the {schema_label} JSON Schema: {schema_json}"
            )
        return "Return exactly one single JSON object. Do not include markdown, code fences, prose, or commentary."

    def _parse_json_object(self, text: str, *, request_kind: str) -> dict[str, Any]:
        try:
            payload = json.loads(_extract_json_object_text(text))
        except json.JSONDecodeError as exc:
            raise RuntimeError(
                f"Anthropic-compatible {request_kind} response was not valid JSON."
            ) from exc
        if not isinstance(payload, dict):
            raise RuntimeError(
                f"Anthropic-compatible {request_kind} response must be a JSON object."
            )
        return payload

    @staticmethod
    def _validate_structured_json(
        payload: dict[str, Any],
        *,
        schema: dict[str, Any],
        request_kind: str,
    ) -> None:
        try:
            Draft202012Validator.check_schema(schema)
        except SchemaError as exc:
            raise ValueError(
                f"Anthropic-compatible {request_kind} JSON Schema is invalid: {exc.message}"
            ) from exc
        try:
            Draft202012Validator(schema).validate(payload)
        except ValidationError as exc:
            location = '.'.join(str(part) for part in exc.absolute_path) or '<root>'
            raise RuntimeError(
                f"Anthropic-compatible {request_kind} response failed JSON Schema "
                f"validation at {location}: {exc.message}"
            ) from exc


def _build_anthropic_compatible_client(config: LLMConfig) -> AnthropicCompatibleClient:
    return AnthropicCompatibleClient(config)


def _anthropic_attempt_discarded_event(
    *,
    request_kind: str,
    attempt: int,
    event_count: int,
    delta_char_count: int,
) -> CanonicalResponseEvent:
    return CanonicalResponseEvent(
        event_type=CanonicalResponseEventType.ATTEMPT_DISCARDED,
        request_kind=request_kind,
        response_mode='native_tools',
        attempt=attempt,
        provider_protocol='anthropic_messages',
        payload={
            'discarded_event_count': event_count,
            'discarded_delta_char_count': delta_char_count,
        },
    )


register_llm_provider("anthropic_compatible", _build_anthropic_compatible_client)
