from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Any, Mapping

from ownpi.runtime.events import now_utc_iso
from ownpi.runtime.tools.runtime_text_resources import (
    RuntimeTextResourceRecord,
    runtime_text_resource_record_for_request,
)


@dataclass(frozen=True)
class RuntimeTextResourceMutationSnapshot:
    grant: RuntimeTextResourceRecord
    before_sha256: str
    before_bytes: int
    before_content: bytes


def capture_runtime_text_resource_mutation(
    *,
    context: Any,
    tool_name: str,
    input_payload: Mapping[str, Any],
) -> RuntimeTextResourceMutationSnapshot | None:
    grant = runtime_text_resource_record_for_request(
        context=context,
        tool_name=tool_name,
        input_payload=input_payload,
    )
    if grant is None:
        return None
    try:
        raw_content = grant.path.read_bytes()
    except OSError:
        return None
    return RuntimeTextResourceMutationSnapshot(
        grant=grant,
        before_sha256=sha256(raw_content).hexdigest(),
        before_bytes=len(raw_content),
        before_content=raw_content,
    )


def reconcile_runtime_text_resource_mutation(
    *,
    context: Any,
    snapshot: RuntimeTextResourceMutationSnapshot | None,
    tool_name: str,
    request_id: str,
    tool_succeeded: bool,
) -> dict[str, Any] | None:
    if snapshot is None:
        return None
    grant = snapshot.grant
    timestamp = now_utc_iso()
    try:
        raw_content = grant.path.read_bytes()
    except (OSError, ValueError) as exc:
        receipt = {
            "resource_kind": grant.resource_kind,
            "relative_path": _relative_runtime_path(context.run_dir, grant.path),
            "before_sha256": snapshot.before_sha256,
            "after_sha256": "",
            "before_bytes": snapshot.before_bytes,
            "after_bytes": 0,
            "changed": True,
            "anomalous": True,
            "integrity_error": str(exc),
            "timestamp": timestamp,
        }
        _record_runtime_text_resource_event(
            context=context,
            tool_name=tool_name,
            request_id=request_id,
            status="integrity_failed",
            receipt=receipt,
        )
        return receipt

    after_sha256 = sha256(raw_content).hexdigest()
    changed = after_sha256 != snapshot.before_sha256
    anomalous = changed and not tool_succeeded
    if grant.digest_state_field:
        setattr(context.state_store.state, grant.digest_state_field, after_sha256)
    receipt = {
        "resource_kind": grant.resource_kind,
        "relative_path": _relative_runtime_path(context.run_dir, grant.path),
        "before_sha256": snapshot.before_sha256,
        "after_sha256": after_sha256,
        "before_bytes": snapshot.before_bytes,
        "after_bytes": len(raw_content),
        "changed": changed,
        "anomalous": anomalous,
        "timestamp": timestamp,
    }
    status = "anomalous_mutation" if anomalous else ("updated" if changed else "unchanged")
    _record_runtime_text_resource_event(
        context=context,
        tool_name=tool_name,
        request_id=request_id,
        status=status,
        receipt=receipt,
    )
    return receipt


def _record_runtime_text_resource_event(
    *,
    context: Any,
    tool_name: str,
    request_id: str,
    status: str,
    receipt: dict[str, Any],
) -> None:
    context.state_store.record_runtime_event(
        event_name="runtime_text_resource_reconciled",
        category="runtime_text_resource",
        status=status,
        stage=context.stage.value,
        attempt=context.attempt,
        state_domain="runtime_text_resource",
        mutation_kind="digest_reconciliation",
        mutation_refs=(
            {
                "resource_kind": receipt["resource_kind"],
                "relative_path": receipt["relative_path"],
                "before_sha256": receipt["before_sha256"],
                "after_sha256": receipt["after_sha256"],
            },
        ),
        before_payload={
            "sha256": receipt["before_sha256"],
            "bytes": receipt["before_bytes"],
        },
        after_payload={
            "sha256": receipt["after_sha256"],
            "bytes": receipt["after_bytes"],
        },
        replay_policy="replay_as_observation",
        inspect_summary=(
            f"{receipt['resource_kind']} {status}: {receipt['relative_path']}"
        ),
        query_chain_id=str(getattr(context, "query_chain_id", "") or ""),
        query_chain_depth=int(getattr(context, "query_chain_depth", 0) or 0),
        continuation_index=int(context.continuation_index or 0),
        tool_name=tool_name,
        request_id=request_id,
        payload={"actor_kind": str(context.actor_kind or ""), **receipt},
        created_at=str(receipt["timestamp"]),
    )


def _relative_runtime_path(run_dir: Path, path: Path) -> str:
    try:
        return str(path.relative_to(Path(run_dir).expanduser().resolve()))
    except ValueError:
        return path.name


__all__ = [
    "RuntimeTextResourceMutationSnapshot",
    "capture_runtime_text_resource_mutation",
    "reconcile_runtime_text_resource_mutation",
]
