"""Stable model-facing projections of canonical runtime source records."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from ownpi.runtime.contracts import (
    ArtifactRecord,
)
from ownpi.runtime.p_assembly.contracts import (
    PSourceAtom,
    PSourceKind,
    PSourceResolutionError,
)
from ownpi.runtime.stage_execution.feedback_handoff import (
    render_feedback_envelope,
)
from ownpi.runtime.state_models import FeedbackState, RevisitState
from ownpi.runtime.stage_execution.feedback_store import load_feedback_metadata, load_feedback_record


_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".gif"}


def project_task_images(
    records: tuple[ArtifactRecord, ...],
) -> tuple[PSourceAtom, ...]:
    atoms: list[PSourceAtom] = []
    for record in records:
        if "user_input" not in set(record.tags):
            continue
        for source_path in record.source_paths:
            if Path(source_path).suffix.lower() not in _IMAGE_SUFFIXES:
                continue
            payload = {
                "artifact_id": record.artifact_id,
                "artifact_type": record.artifact_type,
                "source_path": source_path,
                "summary": record.summary,
                "user_provided": True,
            }
            atoms.append(
                PSourceAtom.create(
                    source_kind=PSourceKind.TASK_IMAGES,
                    identity=f"task-image:{record.artifact_id}:{source_path}",
                    content=_stable_json(payload),
                )
            )
    return tuple(atoms)


def project_revisit_assignment(
    state: RevisitState | None,
    *,
    target_stage: str | None,
) -> tuple[PSourceAtom, ...]:
    if state is None or target_stage is None:
        return ()
    if state.pending_target_stage != target_stage:
        return ()
    if not isinstance(state.pending_revisit_contract, dict):
        raise PSourceResolutionError(
            "active revisit assignment lacks a canonical revisit contract"
        )
    payload = {
        "target_stage": state.pending_target_stage,
        "source_stage": state.pending_source_stage,
        "decision": state.pending_decision,
        "reason": state.pending_reason,
        "revisit_contract": dict(state.pending_revisit_contract),
    }
    content = _stable_json(payload)
    return (
        PSourceAtom.create(
            source_kind=PSourceKind.REVISIT_ASSIGNMENT,
            identity=f"revisit-assignment:{_content_digest(content)}",
            content=content,
        ),
    )


def project_feedback_handoff(
    state: FeedbackState | None,
    *, run_dir: Path | None = None,
) -> tuple[PSourceAtom, ...]:
    if state is None:
        return ()
    handoff = state.active_handoff
    if not handoff.record_id or handoff.status not in {"pending", "consumed"}:
        return ()
    content = render_feedback_envelope(state, record_id=handoff.record_id, run_dir=run_dir)
    return (
        PSourceAtom.create(
            source_kind=PSourceKind.FEEDBACK_HANDOFF,
            identity=f"feedback-handoff:{handoff.record_id}",
            content=content,
        ),
    )

def project_review_history(
    state: FeedbackState | None, *, stage: str | None, loop_index: int,
    run_dir: Path | None = None,
) -> tuple[PSourceAtom, ...]:
    entries = (
        [f"Historical evidence gap recorded at recovery; verify current availability: {item}"
         for item in state.history_evidence_gaps]
        if state is not None else []
    )
    if state is not None and stage is not None:
        for ref in state.record_refs:
            try:
                record = load_feedback_metadata(ref)
            except (OSError, ValueError) as exc:
                entries.append(
                    f"Historical feedback scope cannot be verified: {ref.feedback_path}; "
                    f"evidence gap: {exc}"
                )
                continue
            target = state.critic_loop.review_target
            earlier_submission = (
                record.review_target != target.identity
                if target is not None
                else record.round < state.critic_loop.round
            )
            if (
                record.author_role != "reviewer"
                or record.source_stage != stage
                or record.attempt != state.critic_loop.attempt
                or record.stage_loop_index != loop_index
                or not earlier_submission
            ):
                continue
            try:
                load_feedback_record(ref, run_dir=run_dir)
            except (OSError, ValueError) as exc:
                entries.append(f"Round {record.round}: {record.feedback_path}; evidence gap: {exc}")
            else:
                entries.append(f"Round {record.round}: {record.feedback_path}")
    return (PSourceAtom.create(
        source_kind=PSourceKind.REVIEW_HISTORY,
        identity="review-history:current-loop",
        content="\n".join(entries) if entries else "No earlier review feedback in the current loop.",
    ),)


def _stable_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _content_digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


__all__ = [
    "project_feedback_handoff",
    "project_revisit_assignment",
    "project_review_history",
    "project_task_images",
]
