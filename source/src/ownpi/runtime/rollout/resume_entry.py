"""Resolve the initial workflow continuation before installing or executing it."""

from __future__ import annotations

from dataclasses import dataclass, replace
from hashlib import sha256
from typing import Literal

from ownpi.controller.stages import Stage
from ownpi.runtime.rollout.business_scope import business_stage, reviewer_round
from ownpi.runtime.rollout.interaction_events import project_actor_interactions
from ownpi.runtime.rollout.research_projection import (
    project_research_feedback,
    project_revisit,
)
from ownpi.runtime.rollout.resume import RunResumeFacts, conversation_catalog
from ownpi.runtime.rollout.resume_cursor import ResumeCursor
from ownpi.runtime.stage_prep.identity import stage_prep_id
from ownpi.runtime.stage_prep.models import StagePrepRecord
from ownpi.runtime.state_models import FeedbackState, RevisitState
from ownpi.schemas.artifacts import GateTrace
from ownpi.schemas.runtime_human_clarification import HumanClarificationSession
from ownpi.schemas.workflow_plan import ExecutionTier, StageRuntimeProfile


@dataclass(frozen=True)
class StageResumeEntry:
    facts: RunResumeFacts
    stage: Stage
    attempt: int
    attempt_started: bool
    counters: dict[str, int]
    query_chain_id: str
    query_chain_depth: int
    loop_index: int
    continuation_index: int
    action: Literal["core", "reviewer", "analysis_route", "gate", "plan_mode"]
    feedback: FeedbackState
    revisit: RevisitState
    interaction: HumanClarificationSession | None = None
    gate: GateTrace | None = None
    stage_run_id: str = ""
    approved_plan_path: str = ""
    approved_plan_sha256: str = ""
    prep: StagePrepRecord | None = None
    plan_conversation_id: str | None = None
    cursor: ResumeCursor | None = None
    plan_resume_boundary: (
        Literal["human_input_requested", "approval_requested"] | None
    ) = None


def resolve_stage_resume_entry(facts: RunResumeFacts) -> StageResumeEntry:
    """Select business boundaries once; this function does not change runtime state."""
    from ownpi.runtime.rollout.resume_state import WorkflowResumePaused

    try:
        return _resolve(facts)
    except (OSError, ValueError, TypeError, KeyError) as error:
        raise WorkflowResumePaused(
            f"workflow resume entry cannot be resolved: {error}"
        ) from error


def _resolve(facts: RunResumeFacts) -> StageResumeEntry:
    projection = facts.stage_projection()
    identity = projection.run_identity
    if identity is None:
        raise ValueError("run initialization fact is absent")
    tier = ExecutionTier(identity["execution_tier"])
    if tier is not ExecutionTier.FULL:
        raise ValueError("stage resume requires staged execution")
    plan = projection.workflow_plan
    starts = [
        event.data.to_object()
        for event in projection.events
        if event.kind == "stage_attempt_started"
    ]
    counters: dict[str, int] = {}
    for data in starts:
        name, value = data.get("stage"), data.get("attempt")
        if isinstance(name, str) and type(value) is int and value > 0:
            counters[name] = max(counters.get(name, 0), value)
        else:
            raise ValueError("invalid stage attempt fact")
    cursor = facts.cursor() if facts.conversation is not None else None
    latest = starts[-1] if starts else {}
    stage = Stage(
        cursor.stage
        if cursor
        else latest.get("stage")
        or (
            plan.entry_stage
            if plan
            else ""
        )
    )
    attempt = cursor.attempt if cursor else int(latest.get("attempt") or 1)
    if attempt is None:
        raise ValueError("conversation lacks an attempt identity")
    if tier is ExecutionTier.FULL and (plan is None or not plan.contains(stage)):
        raise ValueError("stage-entry fact is outside the recorded workflow plan")
    loop_index = cursor.stage_loop_index if cursor else 1
    query_id = (
        cursor.query_chain_id if cursor else str(latest.get("query_chain_id") or "")
    )
    query_depth = (
        cursor.query_chain_depth
        if cursor
        else int(latest.get("query_chain_depth") or 0)
    )
    continuation = cursor.continuation_index if cursor else 0
    for event in projection.events:
        data = event.data.to_object()
        if event.kind == "stage_loop_advanced" and (
            data.get("stage"), data.get("attempt")
        ) == (stage.value, attempt) and data["loop_index"] > loop_index:
            # A committed advance can precede creation of the next conversation.
            loop_index = data["loop_index"]
            facts = replace(facts, conversation=None)
            cursor = None
            continuation = 0
    if facts.conversation is not None and (
        facts.conversation.metadata.get('stage_loop_index', 1) < loop_index
    ):
        facts = replace(facts, conversation=None)
        cursor = None
        continuation = 0

    def scoped(data):
        return (data.get("stage"), data.get("attempt")) == (stage.value, attempt)

    gates = [
        event.data.to_object()
        for event in projection.gates
        if scoped(event.data.to_object())
    ]
    gate_data = gates[-1] if gates else {}
    gate = GateTrace.from_dict(gate_data["gate"]) if gates else None
    if gate is not None and (gate.stage, gate.attempt) != (stage, attempt):
        raise ValueError("gate identity differs from its stage scope")
    approved = [
        event.data.to_object()
        for event in projection.plans
        if scoped(event.data.to_object())
    ]
    plan_fact = approved[-1] if approved else {}
    approved_path, approved_hash = "", ""

    interactions = project_actor_interactions(
        facts.events,
        {
            "stage": stage.value,
            "attempt": attempt,
            "actor_kind": "plan_mode",
            "actor_id": "plan_mode",
        },
    )
    plan_sessions = [
        item.session
        for item in interactions.values()
        if item.closed_event_id is None
        and item.session.actor_kind == "plan_mode"
        and (item.session.stage, item.session.attempt) == (stage.value, attempt)
    ]
    # Planning is an earlier boundary than the stage actor selected by the reader.
    prep_facts = [
        event
        for event in projection.stage_prep
        if scoped(event.data.to_object().get("record", {}))
    ]
    planning = tier is ExecutionTier.FULL and (
        bool(plan_sessions) or bool(approved and not prep_facts)
    )
    if planning:
        facts = replace(facts, conversation=None)
        cursor = None
    if cursor is not None:
        allowed = {"stage gate already decided; continue through the gate boundary"}
        allowed.update(
            f"interaction {key} requires its current runtime entry"
            for key in cursor.pending_interactions
        )
        for action, reason in cursor.pauses:
            if reason not in allowed:
                raise ValueError(f"{action} cannot continue: {reason}")

    feedback = (
        project_research_feedback(
            facts, stage=stage.value, attempt=attempt, loop_index=loop_index
        )
        if facts.conversation is not None and gate is None
        else FeedbackState()
    )
    loop = feedback.critic_loop
    action = "core"
    if gate is not None:
        action = "gate"
    elif planning or plan_sessions:
        action = "plan_mode"
    elif feedback.analysis_transition.next_stage_receipt is not None:
        action = "analysis_route"
    else:
        resume_critic = (
            loop.source_stage == stage.value
            and loop.attempt == attempt
            and loop.phase != "idle"
        ) or (
            loop.core_intent_receipt is not None
            and loop.core_intent_receipt.source_stage == stage.value
            and loop.core_intent_receipt.attempt == attempt
        )
        if resume_critic and (
            loop.critic_role == "reviewer" or loop.core_intent_receipt is not None
        ):
            action = "reviewer"
        if (
            cursor is not None
            and cursor.actor_kind == "reviewer"
            and reviewer_round(facts.conversation.metadata["stage"]) != loop.round
        ):
            raise ValueError("selected reviewer is not in the active round")
        if loop.review_target is not None:
            role = "reviewer" if loop.phase == "critic_pending" else "core"
            target = loop.review_target
            candidates = [
                item
                for item in conversation_catalog(facts.run_root, read_session=facts.read_session)
                if item.metadata.get("actor_kind") == role
                and business_stage(str(item.metadata.get("stage") or ""))
                == target.stage
                and item.metadata.get("attempt") == target.attempt
                and item.metadata.get("stage_loop_index") == target.loop_index
                and item.metadata.get("critic_round") == loop.round
            ]
            if len(candidates) > 1:
                raise ValueError("Active critic scope matches multiple conversations")
            if candidates:
                facts = replace(facts, conversation=candidates[0])
                cursor = facts.cursor()
                continuation = cursor.continuation_index

    if cursor is not None:
        interactions = facts.interaction_projection()
    pending = (
        [interactions[key].session for key in cursor.pending_interactions]
        if cursor is not None
        else plan_sessions[-1:]
    )
    if len(pending) > 1:
        raise ValueError("multiple interactions match the resumed business actor")
    interaction = pending[0] if pending else None
    if (
        interaction is not None
        and interaction.status == "answered"
        and interaction.origin_kind == "controller"
        and gate is not None
        and gate.decision.value == "clarify_human"
    ):
        gate = None
        action = "core"
    if plan_fact and gate is None:
        facts.read_approved_plan(plan_fact)
        approved_path = str((facts.run_root / plan_fact["plan_path"]).resolve())
        approved_hash = plan_fact["content_sha256"]
    plan_conversation_id = None
    if action == "plan_mode":
        matches = [
            item
            for item in conversation_catalog(facts.run_root, read_session=facts.read_session)
            if item.metadata.get("run_id") == facts.run_id
            and item.metadata.get("actor_kind") == "plan_mode"
            and (item.metadata.get("stage"), item.metadata.get("attempt"))
            == (stage.value, attempt)
        ]
        if len(matches) > 1:
            raise ValueError("Plan Mode has ambiguous rollout history")
        plan_conversation_id = matches[0].conversation_id if matches else None

    prep = None
    completed = [event for event in prep_facts if event.kind == "stage_prep_completed"]
    if completed and gate is None:
        profile = StageRuntimeProfile(identity["stage_runtime_profile"])
        expected = stage_prep_id(
            run_id=facts.run_id,
            stage=stage.value,
            level=profile.value,
            attempt=attempt,
            raw_hash=sha256(
                facts.read_canonical_text("raw_user_message.txt")
                .strip()
                .encode("utf-8")
            ).hexdigest(),
            task_hash=sha256(
                facts.read_canonical_text("task_spec.md").strip().encode("utf-8")
            ).hexdigest(),
            approved_stage_plan_hash=approved_hash,
        )
        for event in reversed(completed):
            record = StagePrepRecord.from_dict(event.data.to_object()["record"])
            if record.prep_id == expected and record.level == profile.value:
                prep = record
                break
    return StageResumeEntry(
        facts=facts,
        stage=stage,
        attempt=attempt,
        attempt_started=counters.get(stage.value, 0) >= attempt,
        counters=counters,
        query_chain_id=query_id,
        query_chain_depth=query_depth,
        loop_index=loop_index,
        continuation_index=continuation,
        action=action,
        feedback=feedback,
        revisit=project_revisit(facts),
        interaction=interaction,
        gate=gate,
        stage_run_id=gate_data.get("stage_run_id", ""),
        approved_plan_path=approved_path,
        approved_plan_sha256=approved_hash,
        prep=prep,
        plan_conversation_id=plan_conversation_id,
        cursor=cursor,
        plan_resume_boundary=(
            "human_input_requested"
            if interaction.origin_kind == "tool"
            else "approval_requested"
        )
        if action == "plan_mode" and interaction is not None
        else None,
    )
