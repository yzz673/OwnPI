from __future__ import annotations

from pathlib import Path
from typing import Any

from ownpi.runtime.permissions.action_compiler import OperationEffects
from ownpi.runtime.permissions.models import FileSystemAccessMode
from ownpi.runtime.permissions.orchestrator import FileSystemAccessRequest
from ownpi.runtime.permissions.policy import lexical_absolute_path
from ownpi.runtime.tools.definitions import ToolDefinition, ToolExposure
from ownpi.tools.edit_tools import run_patch_apply
from ownpi.tools.patching.models import PatchError
from ownpi.tools.patching.parser import parse_patch


def build_file_mutation_tool_definitions() -> tuple[ToolDefinition, ...]:
    return (
        ToolDefinition(
            exposure=ToolExposure.DIRECT,
            name="patch_apply",
            kind="patch_edit",
            description=(
                "Apply one complete multiline patch transaction across workspace files. "
                "Use for creating, updating, deleting, moving, or renaming files. "
                "Add File creates a new path only; read existing files and use Update File to edit them. "
                "If a correctable path or context failure occurs, fix the complete patch "
                "and retry it as one transaction."
            ),
            detail=(
                "The patch argument must be bounded by *** Begin Patch and *** End Patch. "
                "Supported operations include *** Add File, *** Update File, *** Delete File, "
                "and *** Move to. If a patch fails because of path or context mismatch, "
                "correct the complete patch and retry it as one transaction. "
                "Use actual line breaks, with no Markdown fences or commentary. "
                "Add File fails if its target already exists, including empty files. "
                "Read the existing content and use Update File instead. "
                "All operations are preflighted before mutation."
            ),
            read_only=False,
            concurrency_safe=False,
            permission_class="edit",
            invocation_mode="agent_requested",
            result_family="edit",
            action_primitives=("create", "edit", "delete"),
            primary_intent="edit",
            grounding_lane="grounded",
            input_contract_family="codex_patch",
            selection_disambiguators=(
                "Use for a complete multiline patch transaction.",
                "Do not use for reading files, running commands, or running tests.",
            ),
            detail_builder=lambda: {
                "default_input_examples": [
                    {"patch": "*** Begin Patch\n*** Update File: README.md\n@@\n-old\n+new\n*** End Patch"}
                ],
                "result_highlights": ["changes", "touched_paths"],
            },
            input_schema_builder=lambda: {
                "type": "object",
                "properties": {"patch": {"type": "string", "minLength": 1}},
                "required": ["patch"],
                "additionalProperties": False,
            },
            handler=run_patch_apply,
            effect_builder=_patch_apply_effects,
            destructive_capable=True,
            destructive_kinds=("delete_content", "delete_file"),
            observation_family="patch_apply",
            writes_workspace=True,
        ),
    )


def _patch_apply_effects(context: Any, arguments: dict[str, Any]) -> OperationEffects:
    patch = arguments.get("patch")
    if not isinstance(patch, str):
        return _patch_root_write_effect(context)
    try:
        operations = parse_patch(patch)
    except PatchError:
        return _patch_root_write_effect(context)
    paths: list[Path] = []
    for operation in operations:
        for raw_path in (operation.path, operation.move_path):
            if raw_path is None:
                continue
            path = lexical_absolute_path(raw_path, cwd=context.project_root)
            if path not in paths:
                paths.append(path)
    if not paths:
        return _patch_root_write_effect(context)
    return OperationEffects.declared(
        file_system_accesses=tuple(
            FileSystemAccessRequest(path, FileSystemAccessMode.WRITE) for path in paths
        ),
        source="patch_parsed_operations",
    )


def _patch_root_write_effect(context: Any) -> OperationEffects:
    return OperationEffects.declared(
        file_system_accesses=(
            FileSystemAccessRequest(
                lexical_absolute_path(context.project_root), FileSystemAccessMode.WRITE
            ),
        ),
        source="patch_unparsed_workspace_fallback",
    )
