"""Transient display ownership from verified canonical metadata and relations."""

from typing import Any


SCOPE_FIELDS = (
    "run_id", "actor_id", "actor_kind", "stage", "attempt", "critic_round", "stage_loop_index", "actor_label",
)


def bind_contexts(
    metadata: dict[str, dict[str, Any]],
    parents: dict[str, str],
    audits: dict[str, dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    bound = dict(audits)
    # Parent edges were validated by the backend relationship reader, including
    # cycle/ambiguity rejection. No filename, timestamp or content-based owner.
    for _ in range(len(parents)):
        changed = False
        for child, parent in parents.items():
            if child not in bound and parent in bound:
                bound[child] = bound[parent]
                changed = True
        if not changed:
            break
    return {
        identity: {
            "conversation_id": identity,
            "scope": {key: value.get(key) for key in SCOPE_FIELDS},
            **({"audit_context": bound[identity]} if identity in bound else {}),
        }
        for identity, value in metadata.items()
    }


def stream_context(
    contexts: dict[str, dict[str, Any]],
    active_audits: set[str],
    event: dict[str, Any],
) -> dict[str, Any]:
    actor, stage = event.get("component"), event.get("stage")
    candidates = [
        context for context in contexts.values()
        if context["scope"].get("actor_id") == actor
        and (not stage or context["scope"].get("stage") == stage
             or context.get("audit_context", {}).get("stage") == stage)
        and (event.get("attempt") is None or context["scope"].get("attempt") == event["attempt"])
        and (not event.get("conversation_id") or context["conversation_id"] == event["conversation_id"])
    ]
    active = [value for value in candidates if value.get("audit_context", {}).get("audit_id") in active_audits]
    if active:
        candidates = active
    if len(candidates) == 1:
        return candidates[0]
    return {}  # Ambiguous metadata never authorizes assigning a different owner.
