from __future__ import annotations

from typing import Any

from ownpi.runtime.contracts import ToolResult


def summarize_schema(
    *,
    tool_name: str,
    schema: dict[str, Any] | None,
    default_input_examples: list[dict[str, Any]] | None = None,
    include_tool_name: bool = True,
) -> str:
    if not isinstance(schema, dict):
        return f'{tool_name} accepts any JSON object.' if include_tool_name else 'accepts any JSON object'
    properties = schema.get('properties')
    allowed_input_keys = (
        [str(key) for key in properties if str(key).strip()]
        if isinstance(properties, dict)
        else []
    )
    required_input_keys = [str(item) for item in schema.get('required', []) if str(item).strip()]
    additional_properties = bool(schema.get('additionalProperties', True))
    fragments: list[str] = []
    if include_tool_name:
        fragments.append(f'{tool_name}')
    if not allowed_input_keys and additional_properties is False:
        fragments.append('expects {} only')
    elif allowed_input_keys:
        fragments.append(f"allowed={','.join(allowed_input_keys[:8])}")
    if required_input_keys:
        fragments.append(f"required={','.join(required_input_keys[:8])}")
    if additional_properties is False:
        fragments.append('no_extra_keys')
    examples = _normalize_examples(default_input_examples)
    if examples:
        fragments.append(f"use={_format_json_like(examples[0])}")
    if not fragments:
        return 'accepts JSON object input'
    return ' '.join(fragment for fragment in fragments if fragment).strip()


def build_input_validation_feedback(
    *,
    tool_name: str,
    schema: dict[str, Any] | None,
    input: dict[str, Any],
    default_input_examples: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    if not isinstance(schema, dict):
        return {
            'error': f'invalid input for {tool_name}: expected object',
            'compact_hint': f'{tool_name} expects a JSON object input.',
            'violation_kind': 'shape_mismatch',
            'allowed_input_keys': [],
            'required_input_keys': [],
            'invalid_input_keys': [],
            'missing_required_input_keys': [],
            'default_input_examples': _normalize_examples(default_input_examples),
            'schema_summary': summarize_schema(
                tool_name=tool_name,
                schema=schema,
                default_input_examples=default_input_examples,
            ),
        }
    properties = schema.get('properties')
    allowed_input_keys = (
        [str(key) for key in properties if str(key).strip()]
        if isinstance(properties, dict)
        else []
    )
    required_input_keys = [str(item) for item in schema.get('required', []) if str(item).strip()]
    missing_required_input_keys = [item for item in required_input_keys if item not in input]
    invalid_input_keys: list[str] = []
    if schema.get('additionalProperties', True) is False and isinstance(properties, dict):
        allowed_keys = {str(key) for key in properties}
        invalid_input_keys = [str(key) for key in input if str(key) not in allowed_keys]
    error = ''
    violation_kind = 'shape_mismatch'
    if invalid_input_keys:
        violation_kind = 'unknown_input_key'
    elif missing_required_input_keys:
        violation_kind = 'shape_mismatch'
    if missing_required_input_keys:
        error = f'missing required input keys for {tool_name}: {", ".join(missing_required_input_keys)}'
    if invalid_input_keys:
        error = f'unexpected input keys for {tool_name}: {", ".join(invalid_input_keys)}'
    elif not error:
        error = f'invalid input for {tool_name}'
    examples = _normalize_examples(default_input_examples)
    example_text = f' Use { _format_json_like(examples[0]) }.' if examples else ''
    allowed_text = (
        ' allowed keys: {} only.'
        if not allowed_input_keys and schema.get('additionalProperties', True) is False
        else f" allowed keys: {', '.join(allowed_input_keys)}."
        if allowed_input_keys
        else ''
    )
    compact_hint = (
        f'{error}.{allowed_text}{example_text}'.replace('..', '.').strip()
    )
    return {
        'error': error,
        'compact_hint': compact_hint,
        'violation_kind': violation_kind,
        'allowed_input_keys': allowed_input_keys,
        'required_input_keys': required_input_keys,
        'invalid_input_keys': invalid_input_keys,
        'missing_required_input_keys': missing_required_input_keys,
        'default_input_examples': examples,
        'schema_summary': summarize_schema(
            tool_name=tool_name,
            schema=schema,
            default_input_examples=examples,
        ),
    }


def summarize_tool_result(tool_result: ToolResult, *, max_len: int = 240) -> str:
    status = 'ok' if tool_result.ok else 'failed'
    primary = _tool_result_primary_detail(tool_result)
    prefix = f'{tool_result.tool_name} ({status})'
    if not primary:
        return prefix
    text = f'{prefix} {primary}'.strip()
    return text if len(text) <= max_len else f'{text[: max_len - 1]}…'


def _tool_result_primary_detail(tool_result: ToolResult) -> str:
    data = tool_result.data or {}
    if tool_result.tool_name == 'file_reader' and isinstance(data, dict):
        return (
            f"path={data.get('file_path', '')}; "
            f"requested_limit={data.get('requested_limit', '')}; "
            f"effective_limit={data.get('effective_limit', '')}; "
            f"execution_range={data.get('start_line', '')}-{data.get('end_line', '')} "
            f"of {data.get('total_lines', '')}"
        ).strip()
    if tool_result.tool_name == 'remote_pull' and isinstance(data, dict):
        previews = data.get('pulled_artifact_previews', [])
        preview_count = len(previews) if isinstance(previews, list) else 0
        return (
            f"paths={_compact_list(data.get('paths'), limit=3)}; "
            f"local_destinations={_compact_list(data.get('local_destinations'), limit=3)}; "
            f"previews={preview_count}"
        ).strip()
    if tool_result.tool_name == 'remote_monitor' and isinstance(data, dict):
        raw_monitor = data.get('monitor')
        monitor: dict[str, Any] = raw_monitor if isinstance(raw_monitor, dict) else {}
        return (
            f"job_state={monitor.get('job_state', '')}; "
            f"artifacts_ready={monitor.get('artifacts_ready', False)}; "
            f"failure_type={monitor.get('failure_type', '')}; "
            f"recommended_next_action={monitor.get('recommended_next_action', '')}"
        ).strip()
    if tool_result.tool_name in {'execution_monitor', 'execution_kill'}:
        raw_monitor = data.get('monitor')
        monitor = raw_monitor if isinstance(raw_monitor, dict) else {}
        stream_parts = [
            f"recent_stdout={_compact_stream_preview(monitor.get('recent_stdout'))}"
            if monitor.get('recent_stdout')
            else '',
            f"recent_stderr={_compact_stream_preview(monitor.get('recent_stderr'))}"
            if monitor.get('recent_stderr')
            else '',
        ]
        return (
            f"job_state={monitor.get('job_state', '')}; "
            f"exit_code={monitor.get('exit_code', '')}; "
            f"output_truncated={bool(monitor.get('output_truncated'))}; "
            f"{'; '.join(part for part in stream_parts if part)}"
        ).strip()
    if tool_result.tool_name == 'pdf_reader' and isinstance(data, dict):
        preview = ' '.join(str(data.get('text_preview', '') or '').split())[:160]
        return (
            f"path={data.get('source_path', '')} extractor={data.get('extractor', '')} "
            f"pages={data.get('page_count', '')} readable={data.get('readable_pages', '')}"
            + (f" preview={preview}" if preview else '')
        ).strip()
    if tool_result.tool_name in {
        'exec_command',
    } and isinstance(data, dict):
        stdout_preview = _compact_stream_preview(data.get('stdout_preview', ''))
        stderr_preview = _compact_stream_preview(data.get('stderr_preview', ''))
        stream_parts = [
            f"stdout_preview={stdout_preview}" if stdout_preview else '',
            f"stderr_preview={stderr_preview}" if stderr_preview else '',
        ]
        return (
            f"status={data.get('status', '')}; exit_code={data.get('exit_code', '')}; "
            f"{'; '.join(part for part in stream_parts if part)}; "
            f"command={_compact_scalar(data.get('command', ''))}; cwd={_compact_scalar(data.get('cwd', ''))}"
        ).strip()
    if tool_result.tool_name == 'mcp_tool_list' and isinstance(data, dict):
        tools = data.get('tools', [])
        tool_names = [str(item.get('name', '')) for item in tools[:4] if isinstance(item, dict)]
        return f"server={data.get('server_name', '')}; tools={_compact_list(tool_names, limit=4)}".strip()
    if tool_result.tool_name == 'mcp_tool_invoke' and isinstance(data, dict):
        content = _compact_scalar(data.get('content', ''))
        return (
            f"server={data.get('server_name', '')}; tool={data.get('tool_name', '')}; "
            f"content={content}"
        ).strip()
    if tool_result.stdout:
        return ' '.join(str(tool_result.stdout).split())[:200]
    if tool_result.error:
        return str(tool_result.error).strip()[:200]
    if tool_result.stderr:
        return ' '.join(str(tool_result.stderr).split())[:200]
    return ''


def _compact_list(value: Any, *, limit: int) -> str:
    if not isinstance(value, list):
        return '[]'
    items = [str(item) for item in value[:limit] if str(item).strip()]
    if len(value) > limit:
        items.append('…')
    return '[' + ', '.join(items) + ']'


def _compact_scalar(value: Any) -> str:
    text = str(value)
    return text if len(text) <= 120 else f'{text[:119]}…'


def _compact_stream_preview(value: Any, *, limit: int = 120) -> str:
    text = ' '.join(str(value or '').split())
    if not text:
        return ''
    return text if len(text) <= limit else f'{text[: limit - 1]}…'


def _normalize_examples(examples: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    if not isinstance(examples, list):
        return []
    normalized: list[dict[str, Any]] = []
    for item in examples:
        if isinstance(item, dict):
            normalized.append(dict(item))
    return normalized[:4]


def _format_json_like(value: dict[str, Any]) -> str:
    if not value:
        return '{}'
    parts = [f'"{key}": "{item}"' if isinstance(item, str) else f'"{key}": {item}' for key, item in value.items()]
    return '{' + ', '.join(parts) + '}'
