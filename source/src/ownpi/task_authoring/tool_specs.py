from __future__ import annotations

from ownpi.runtime.native_tools.spec import NativeToolSpec
from ownpi.schemas.runtime_tooling import ToolCallBatchPolicy


INSPECT_USER_PATH_TOOL = NativeToolSpec(
    name="inspect_user_path",
    description=(
        "Inspect existence and basic metadata for one path copied verbatim from "
        "the raw user message. This tool never reads file contents or executes commands."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "path": {"type": "string", "minLength": 1},
            "reason": {"type": "string", "minLength": 1, "maxLength": 300},
        },
        "required": ["path", "reason"],
        "additionalProperties": False,
    },
    execution_class="read_only",
    side_effect_level="none",
    permission_class="task_authoring",
    read_only=True,
    concurrency_safe=True,
    hosted_execution_allowed=False,
)


WRITE_TASK_SPEC_TOOL = NativeToolSpec(
    name="write_task_spec",
    description=(
        "Submit the completed concise Markdown task specification for runtime "
        "validation and atomic persistence to the admission artifact directory."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "content": {"type": "string", "minLength": 1, "maxLength": 12000},
        },
        "required": ["content"],
        "additionalProperties": False,
    },
    execution_class="artifact_authoring",
    side_effect_level="artifact_write",
    permission_class="task_authoring",
    read_only=False,
    concurrency_safe=False,
    batch_policy=ToolCallBatchPolicy.TURN_EXCLUSIVE,
    hosted_execution_allowed=False,
)


TASK_SPEC_AUTHORING_TOOLS = (INSPECT_USER_PATH_TOOL, WRITE_TASK_SPEC_TOOL)


__all__ = [
    "INSPECT_USER_PATH_TOOL",
    "TASK_SPEC_AUTHORING_TOOLS",
    "WRITE_TASK_SPEC_TOOL",
]
