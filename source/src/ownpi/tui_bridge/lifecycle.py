"""Current Runtime admission and read-only capabilities for the terminal client."""
from pathlib import Path
from typing import Any

from ownpi.runtime.rollout.direct_resume import is_direct_session, resolve_direct_resume
from ownpi.runtime.rollout.resume import read_run_resume
from ownpi.runtime.rollout.run_events import run_events_path
from ownpi.runtime.rollout.resume_entry import resolve_stage_resume_entry
from ownpi.runtime.rollout.resume_read_session import ResumeReadSession
from ownpi.schemas.workflow_plan import StageRuntimeProfile, resolve_runtime_profile


def uses_direct(config: Any) -> bool:
    return resolve_runtime_profile(config_selection=config.runtime.default_profile).resolved is StageRuntimeProfile.TASK_LEVEL


def run_capabilities(run_dir: Path) -> dict[str, Any]:
    """Inspect backend facts without installing runtime state or acquiring an owner."""
    result = {"available": False, "reason": "", "mode": "unknown", "can_submit": False}
    if not run_events_path(run_dir).is_file():
        legacy = (run_dir / "run_manifest.json").is_file() or (run_dir / "runtime_records").is_dir()
        return {**result, "mode": "historical" if legacy else "unknown", "reason": (
            "Legacy recording format: this Runtime cannot resume it. Inspect saved records or start a new task; the original run is unchanged."
            if legacy else "Recovery records are missing (rollouts/run-events.jsonl). Inspect this run; no recovery was started."
        )}
    try:
        with ResumeReadSession() as reader:
            header = reader.business_events(run_events_path(run_dir), run_id=run_dir.name)
            if header.parse_errors:
                raise ValueError("Run events are incomplete or unreadable")
            initialized = [e.data.to_object() for e in header.events if e.kind == "run_initialized"]
            identity = initialized[0] if len(initialized) == 1 else {}
            if identity.get("stage_runtime_profile") == "research_level":
                result["mode"] = "research"
                if any(e.kind == "run_finalized" and e.data.to_object().get("status") == "completed" for e in header.events):
                    return {**result, "reason": "Completed run; open its history without executing."}
            if not is_direct_session(header.events) and (identity.get("execution_tier") == "direct" or identity.get("stage_runtime_profile") == "task_level"):
                return {**result, "mode": "historical", "reason": "This historical execution mode is no longer supported by Runtime. Open it for inspection or start a new session."}
            facts = read_run_resume(run_dir, read_session=reader)
            if is_direct_session(facts.events):
                entry = resolve_direct_resume(facts)
                return {**result, "available": True, "mode": "direct", "can_submit": not entry.active}
            projection = facts.stage_projection()
            if identity.get("execution_tier") == "full" and identity.get("stage_runtime_profile") == "research_level":
                result["mode"] = "research"
                if projection.workflow_plan is None:
                    # Current Runtime supports resuming its authoring admission.
                    facts.read_canonical_text("raw_user_message.txt")
                else:
                    resolve_stage_resume_entry(facts)
                return {**result, "available": True}
            result["reason"] = "Runtime admission is not durably recorded."
    except (OSError, ValueError, RuntimeError, KeyError, TypeError) as error:
        result["reason"] = str(error)
    return result


def recovery_availability(run_dir: Path) -> dict[str, Any]:
    capability = run_capabilities(run_dir)
    return {key: capability[key] for key in ("available", "reason")}
