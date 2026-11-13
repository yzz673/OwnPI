from __future__ import annotations

from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError

from ownpi.controller.runtime_control_signal import RuntimeControlRequested
from ownpi.runtime.native_tools.loop_outcome import is_loop_cancellation
from ownpi.runtime.tools.result_serialization import tool_output_json_text
from ownpi.runtime.native_tools.spec import NativeToolCall, NativeToolResult
from ownpi.task_authoring.contracts import TaskSpecArtifactRef
from ownpi.task_authoring.persistence import (
    RunTaskArtifactLayout,
    persist_task_spec_artifact,
)
from ownpi.task_authoring.tool_specs import TASK_SPEC_AUTHORING_TOOLS


TASK_SPEC_HEADINGS = (
    "# Task Specification",
    "## User Request",
    "## Inputs and Resources",
    "## Execution Binding",
    "## Expected Deliverables",
    "## Constraints and Preferences",
    "## Open Questions",
)
MEDIA_SUFFIXES = (
    ".bmp",
    ".gif",
    ".jpeg",
    ".jpg",
    ".mkv",
    ".mov",
    ".mp3",
    ".mp4",
    ".png",
    ".tif",
    ".tiff",
    ".wav",
    ".webm",
)


@dataclass
class TaskSpecAuthoringToolContext:
    request_id: str
    raw_user_message: str
    project_root: Path
    layout: RunTaskArtifactLayout
    inspected_paths: set[str] = field(default_factory=set)


def handle_inspect_user_path(
    *,
    arguments: dict[str, Any],
    context: TaskSpecAuthoringToolContext,
    provider_call_id: str = "",
) -> NativeToolResult:
    path_value = arguments.get("path")
    reason = arguments.get("reason")
    if not isinstance(path_value, str) or not path_value:
        return _failed_result(
            tool_name="inspect_user_path",
            context=context,
            provider_call_id=provider_call_id,
            failure_class="invalid_tool_arguments",
            message="path must be a non-empty string",
        )
    if not isinstance(reason, str) or not reason.strip() or len(reason) > 300:
        return _failed_result(
            tool_name="inspect_user_path",
            context=context,
            provider_call_id=provider_call_id,
            failure_class="invalid_tool_arguments",
            message="reason must be a non-empty string of at most 300 characters",
        )
    if path_value not in context.raw_user_message:
        return _failed_result(
            tool_name="inspect_user_path",
            context=context,
            provider_call_id=provider_call_id,
            failure_class="path_not_in_raw_message",
            message="path must appear verbatim in the raw user message",
        )
    if path_value not in context.inspected_paths:
        context.inspected_paths.add(path_value)

    try:
        resolution = _resolve_user_path(path_value, context.project_root)
    except (OSError, ValueError, RuntimeError) as error:
        if is_loop_cancellation(error) or isinstance(error, RuntimeControlRequested):
            raise
        return _failed_result(
            tool_name="inspect_user_path",
            context=context,
            provider_call_id=provider_call_id,
            failure_class="path_inspection_error",
            message=str(error),
        )
    if isinstance(resolution, str):
        return _failed_result(
            tool_name="inspect_user_path",
            context=context,
            provider_call_id=provider_call_id,
            failure_class=resolution,
            message=_path_policy_message(resolution),
        )

    resolved_path = resolution
    metadata: dict[str, Any] = {
        "raw_path": path_value,
        "resolved_path": str(resolved_path),
        "exists": False,
        "is_file": False,
        "is_dir": False,
        "size_bytes": None,
        "media_suffix": resolved_path.suffix.lower() if resolved_path.suffix.lower() in MEDIA_SUFFIXES else None,
        "read_content": False,
        "reason": reason,
    }
    try:
        metadata["exists"] = resolved_path.exists()
        metadata["is_file"] = resolved_path.is_file()
        metadata["is_dir"] = resolved_path.is_dir()
        if metadata["exists"] and metadata["is_file"]:
            metadata["size_bytes"] = resolved_path.stat().st_size
    except OSError as error:
        metadata["error"] = error.__class__.__name__
        metadata["error_message"] = str(error)[:300]
    return _completed_result(
        tool_name="inspect_user_path",
        context=context,
        provider_call_id=provider_call_id,
        data=metadata,
        output_text=tool_output_json_text(metadata, tool_name="inspect_user_path"),
    )


def handle_write_task_spec(
    *,
    arguments: dict[str, Any],
    context: TaskSpecAuthoringToolContext,
    provider_call_id: str = "",
    revision: int = 1,
) -> NativeToolResult:
    content = arguments.get("content")
    if not isinstance(content, str):
        return _failed_result(
            tool_name="write_task_spec",
            context=context,
            provider_call_id=provider_call_id,
            failure_class="invalid_tool_arguments",
            message="content must be a string",
        )
    validation_error = validate_task_spec_content(content)
    if validation_error is not None:
        return _failed_result(
            tool_name="write_task_spec",
            context=context,
            provider_call_id=provider_call_id,
            failure_class=validation_error[0],
            message=validation_error[1],
        )
    normalized_content = content.strip()
    artifact = persist_task_spec_artifact(
        layout=context.layout,
        raw_user_message=context.raw_user_message,
        task_spec_content=normalized_content,
        revision=revision,
    )
    data = artifact.to_dict()
    return _completed_result(
        tool_name="write_task_spec",
        context=context,
        provider_call_id=provider_call_id,
        data={"artifact": data},
        output_text=tool_output_json_text(data, tool_name="write_task_spec"),
    )


def execute_task_spec_authoring_call(
    *,
    call: NativeToolCall,
    context: TaskSpecAuthoringToolContext,
) -> NativeToolResult:
    tool = next(
        (candidate for candidate in TASK_SPEC_AUTHORING_TOOLS if candidate.name == call.tool_name),
        None,
    )
    if tool is None:
        return _failed_call_result(
            call=call,
            failure_class="unknown_tool",
            message=f"unknown task authoring tool: {call.tool_name}",
        )
    try:
        Draft202012Validator(tool.input_schema).validate(call.input)
    except ValidationError as error:
        return _failed_call_result(
            call=call,
            failure_class="invalid_tool_arguments",
            message=error.message,
        )
    # Tool results must echo this invocation's identity, not the enclosing
    # authoring task request. Keep the shared inspection set and task context.
    context = replace(context, request_id=call.request_id or call.provider_call_id)
    if call.tool_name == "inspect_user_path":
        return handle_inspect_user_path(
            arguments=call.input,
            context=context,
            provider_call_id=call.provider_call_id,
        )
    try:
        return handle_write_task_spec(
            arguments=call.input,
            context=context,
            provider_call_id=call.provider_call_id,
        )
    except Exception as error:
        return _failed_call_result(
            call=call,
            failure_class="persistence_error",
            message=str(error),
        )


def artifact_from_write_result(result: NativeToolResult) -> TaskSpecArtifactRef | None:
    raw = result.data.get("artifact")
    if not isinstance(raw, dict):
        return None
    try:
        return TaskSpecArtifactRef(
            raw_message_path=str(raw["raw_message_path"]),
            task_spec_path=str(raw["task_spec_path"]),
            content_hash=str(raw["content_hash"]),
            raw_message_hash=str(raw["raw_message_hash"]),
            revision=int(raw.get("revision", 1)),
        )
    except (KeyError, TypeError, ValueError):
        return None


def validate_task_spec_content(content: str) -> tuple[str, str] | None:
    if not content.strip():
        return ("empty_task_spec", "task specification content must not be empty")
    if "\x00" in content:
        return ("invalid_task_spec_content", "task specification must not contain NUL")
    try:
        content.encode("utf-8")
    except UnicodeEncodeError:
        return ("invalid_task_spec_encoding", "task specification must be valid UTF-8")
    missing = [heading for heading in TASK_SPEC_HEADINGS if heading not in content]
    if missing:
        return ("invalid_task_spec_structure", f"missing required heading: {missing[0]}")
    if _contains_bracketed_guidance(content):
        return (
            "invalid_task_spec_structure",
            "task specification must not retain bracketed authoring guidance",
        )
    return None


def _contains_bracketed_guidance(content: str) -> bool:
    for line in content.splitlines():
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            return True
    return False


def _resolve_user_path(path_value: str, project_root: Path) -> Path | str:
    candidate = Path(path_value).expanduser()
    if candidate.is_absolute():
        return candidate.resolve(strict=False)
    root = project_root.expanduser().resolve(strict=False)
    resolved = (root / candidate).resolve(strict=False)
    try:
        resolved.relative_to(root)
    except ValueError:
        return "relative_path_outside_project_root"
    return resolved


def _path_policy_message(failure_class: str) -> str:
    messages = {
        "relative_path_outside_project_root": "relative paths may not escape project_root",
    }
    return messages.get(failure_class, failure_class)


def _completed_result(
    *,
    tool_name: str,
    context: TaskSpecAuthoringToolContext,
    provider_call_id: str,
    data: dict[str, Any],
    output_text: str,
) -> NativeToolResult:
    return NativeToolResult(
        tool_name=tool_name,
        request_id=context.request_id,
        provider_call_id=provider_call_id,
        status="completed",
        output_text=output_text,
        data=data,
    )


def _failed_result(
    *,
    tool_name: str,
    context: TaskSpecAuthoringToolContext,
    provider_call_id: str,
    failure_class: str,
    message: str,
) -> NativeToolResult:
    data = {"failure_class": failure_class, "message": message}
    return NativeToolResult(
        tool_name=tool_name,
        request_id=context.request_id,
        provider_call_id=provider_call_id,
        status="failed",
        output_text=tool_output_json_text(data, tool_name=tool_name),
        data=data,
        metadata={"failure_class": failure_class},
    )


def _failed_call_result(
    *,
    call: NativeToolCall,
    failure_class: str,
    message: str,
) -> NativeToolResult:
    data = {"failure_class": failure_class, "message": message[:500]}
    return NativeToolResult(
        tool_name=call.tool_name,
        request_id=call.request_id or call.provider_call_id,
        provider_call_id=call.provider_call_id,
        status="failed",
        output_text=tool_output_json_text(data, tool_name=call.tool_name),
        data=data,
        metadata={"failure_class": failure_class},
    )


__all__ = [
    "TASK_SPEC_HEADINGS",
    "TaskSpecAuthoringToolContext",
    "artifact_from_write_result",
    "execute_task_spec_authoring_call",
    "handle_inspect_user_path",
    "handle_write_task_spec",
    "validate_task_spec_content",
]
