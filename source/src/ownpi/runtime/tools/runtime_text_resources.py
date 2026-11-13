from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from ownpi.runtime.execution.board import execution_board_path
from ownpi.runtime.research_state_resource import (
    research_state_path,
    runtime_uses_research_state,
)


@dataclass(frozen=True)
class RuntimeTextResourceRecord:
    """Resource identity for change observation, without authorization semantics."""

    resource_kind: str
    path: Path
    digest_state_field: str = ""


def build_runtime_text_resource_records(context: Any) -> tuple[RuntimeTextResourceRecord, ...]:
    state = context.state_store.state
    grants: list[RuntimeTextResourceRecord] = []
    resource_run_dir = getattr(context, "shared_resource_run_dir", None) or context.run_dir
    if runtime_uses_research_state(state):
        grants.append(
            RuntimeTextResourceRecord(
                resource_kind="research_state",
                path=research_state_path(resource_run_dir),
                digest_state_field="research_state_sha256",
            )
        )
    board_path = execution_board_path(run_dir=resource_run_dir)
    if board_path.is_file():
        grants.append(
            RuntimeTextResourceRecord(
                resource_kind="execution_board",
                path=board_path,
            )
        )
    return tuple(grants)


def runtime_text_resource_record_for_request(
    *,
    context: Any,
    tool_name: str,
    input_payload: Mapping[str, Any],
) -> RuntimeTextResourceRecord | None:
    del tool_name
    resources = _request_targeted_grants(
        input_payload,
        grants=build_runtime_text_resource_records(context),
        run_dir=context.run_dir,
        project_root=context.project_root,
    )
    return next(iter(sorted(resources, key=lambda item: str(item.path))), None)


def _targeted_grant(
    raw_path: Any,
    *,
    grants: tuple[RuntimeTextResourceRecord, ...],
    run_dir: Path,
    project_root: Path | None = None,
) -> RuntimeTextResourceRecord | None:
    raw = str(raw_path or "").strip()
    if not raw:
        return None
    candidate = Path(raw).expanduser()
    if not candidate.is_absolute():
        candidate = (project_root or run_dir) / candidate
    try:
        resolved = candidate.resolve(strict=False)
        return next(
            (grant for grant in grants if resolved == grant.path.resolve(strict=False)),
            None,
        )
    except (OSError, ValueError):
        return None


def _request_targeted_grants(
    value: Any,
    *,
    grants: tuple[RuntimeTextResourceRecord, ...],
    run_dir: Path,
    project_root: Path | None = None,
) -> set[RuntimeTextResourceRecord]:
    targeted: set[RuntimeTextResourceRecord] = set()

    def visit(item: Any) -> None:
        if isinstance(item, Mapping):
            for child in item.values():
                visit(child)
            return
        if isinstance(item, (list, tuple)):
            for child in item:
                visit(child)
            return
        if not isinstance(item, str):
            return
        for grant in grants:
            if str(grant.path) in item:
                targeted.add(grant)
        if item.strip() and "\n" not in item and "\x00" not in item:
            grant = _targeted_grant(
                item,
                grants=grants,
                run_dir=run_dir,
                project_root=project_root,
            )
            if grant is not None:
                targeted.add(grant)

    visit(value)
    return targeted
