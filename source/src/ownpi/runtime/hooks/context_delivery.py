from __future__ import annotations

import hashlib
import json
import logging
from typing import Any

from ownpi.runtime.events import now_utc_iso
from ownpi.runtime.hooks.output_parser import normalized_event_output
from ownpi.runtime.hooks.types import (
    HookDeliveryBatch,
    HookDeliveryScope,
    HookDeliveryTarget,
    HookEventName,
    HookModelContextFragment,
    HookOutputAtom,
    HookOutputField,
    HookRequest,
    HookRunReceipt,
    HookUIEffect,
)


MAX_HOOK_DELIVERY_RECORDS = 256
MAX_HOOK_DELIVERY_CONSUMPTIONS = 256


_MODEL_TARGETS = frozenset(
    {
        HookDeliveryTarget.IMMUTABLE_PREFIX,
        HookDeliveryTarget.CONVERSATION,
    }
)

_ADDITIONAL_CONTEXT_TARGETS: dict[HookEventName, HookDeliveryTarget] = {
    "SessionStart": HookDeliveryTarget.IMMUTABLE_PREFIX,
    "UserPromptSubmit": HookDeliveryTarget.IMMUTABLE_PREFIX,
    "PreToolUse": HookDeliveryTarget.CONVERSATION,
    "PostToolUse": HookDeliveryTarget.CONVERSATION,
    "PermissionRequest": HookDeliveryTarget.CONVERSATION,
    "PreCompact": HookDeliveryTarget.CONVERSATION,
    "PostCompact": HookDeliveryTarget.CONVERSATION,
    "Stop": HookDeliveryTarget.OBSERVABILITY,
    "SessionEnd": HookDeliveryTarget.OBSERVABILITY,
    "Elicitation": HookDeliveryTarget.CONVERSATION,
}

_FIELD_TARGETS: dict[HookOutputField, HookDeliveryTarget] = {
    HookOutputField.SYSTEM_MESSAGE: HookDeliveryTarget.UI,
    HookOutputField.DECISION: HookDeliveryTarget.RUNTIME_CONTROL,
    HookOutputField.CONTINUE: HookDeliveryTarget.RUNTIME_CONTROL,
    HookOutputField.REASON: HookDeliveryTarget.RUNTIME_CONTROL,
    HookOutputField.UPDATED_INPUT: HookDeliveryTarget.RUNTIME_MUTATION,
    HookOutputField.UPDATED_MCP_TOOL_OUTPUT: HookDeliveryTarget.RUNTIME_MUTATION,
    HookOutputField.ACTION: HookDeliveryTarget.RUNTIME_CONTROL,
    HookOutputField.CONTENT: HookDeliveryTarget.RUNTIME_CONTROL,
}


def build_delivery_batch(
    request: HookRequest,
    receipts: tuple[HookRunReceipt, ...],
) -> HookDeliveryBatch:
    atoms: list[HookOutputAtom] = []
    fragments: list[HookModelContextFragment] = []
    ui_effects: list[HookUIEffect] = []
    for receipt in receipts:
        if not receipt.output or receipt.schema_error or receipt.status == "scheduled":
            continue
        output = normalized_event_output(receipt.output, event_name=request.event_name)
        for raw_field, value in output.items():
            field_kind = HookOutputField(raw_field)
            target = _target_for(request.event_name, field_kind)
            atom = HookOutputAtom(
                field_kind=field_kind,
                target=target,
                event_id=request.event_id,
                event_name=request.event_name,
                handler_key=receipt.handler_key,
                handler_name=receipt.handler_name,
                source=receipt.source,
                trust_status=receipt.trust_status,
                execution_mode=receipt.execution_mode,
                value=value,
            )
            atoms.append(atom)
            if field_kind is HookOutputField.ADDITIONAL_CONTEXT:
                fragments.extend(
                    _project_additional_context(
                        atom,
                        request=request,
                    )
                )
            elif field_kind is HookOutputField.SYSTEM_MESSAGE:
                message = str(value or "").strip()
                if message:
                    ui_effects.append(
                        HookUIEffect(
                            event_id=request.event_id,
                            event_name=request.event_name,
                            handler_key=receipt.handler_key,
                            source=receipt.source,
                            message=message,
                        )
                    )
    return HookDeliveryBatch(
        atoms=tuple(atoms),
        model_context_fragments=tuple(fragments),
        ui_effects=tuple(ui_effects),
        receipt_ids=tuple(receipt.receipt_id for receipt in receipts),
    )


def is_eligible_hook_prefix(
    value: HookOutputAtom | HookModelContextFragment,
) -> bool:
    eligible = (
        value.target is HookDeliveryTarget.IMMUTABLE_PREFIX
        and value.trust_status in {"managed", "trusted"}
        and value.execution_mode == "sync"
    )
    if not eligible:
        logging.getLogger(__name__).warning(
            "Skipping ineligible hook prefix: event=%s handler=%s target=%s trust=%s mode=%s",
            value.event_id, value.handler_key, value.target.value,
            value.trust_status, value.execution_mode,
        )
    return eligible


def persist_hook_delivery_batch(
    context_state: Any,
    batch: HookDeliveryBatch,
) -> tuple[str, ...]:
    records = _delivery_records(context_state)
    persisted: list[str] = []
    for fragment in batch.model_context_fragments:
        if fragment.delivery_id in records:
            continue
        event_store = getattr(context_state, '_run_event_store', None)
        confirmed = True
        if event_store is not None:
            from ownpi.runtime.rollout.hook_events import register_hook_context

            try:
                register_hook_context(event_store, fragment.to_dict())
            except OSError:
                confirmed = False
                logging.getLogger(__name__).warning('Hook registration unconfirmed', exc_info=True)
        records[fragment.delivery_id] = {
            "fragment": fragment.to_dict(),
            "status": "active",
            "persisted_at": now_utc_iso(),
            "persistence_confirmed": confirmed,
        }
        persisted.append(fragment.delivery_id)
    _trim_delivery_records(records)
    return tuple(persisted)


def merge_hook_delivery_batches(
    *batches: HookDeliveryBatch,
) -> HookDeliveryBatch:
    return HookDeliveryBatch(
        atoms=tuple(atom for batch in batches for atom in batch.atoms),
        model_context_fragments=tuple(
            fragment
            for batch in batches
            for fragment in batch.model_context_fragments
        ),
        ui_effects=tuple(effect for batch in batches for effect in batch.ui_effects),
        receipt_ids=tuple(
            dict.fromkeys(
                receipt_id
                for batch in batches
                for receipt_id in batch.receipt_ids
            )
        ),
    )


def select_active_hook_fragments(
    context_state: Any,
    *,
    target: HookDeliveryTarget,
    session_id: str = "",
    run_id: str = "",
    loop_kind: str = "",
    stage: str = "",
    attempt: int | None = None,
) -> tuple[HookModelContextFragment, ...]:
    selected: list[HookModelContextFragment] = []
    for record in _delivery_records(context_state).values():
        if str(record.get("status", "")) != "active":
            continue
        payload = record.get("fragment")
        if not isinstance(payload, dict):
            continue
        try:
            fragment = HookModelContextFragment.from_dict(payload)
        except (KeyError, TypeError, ValueError):
            continue
        if fragment.target is not target:
            continue
        if target is HookDeliveryTarget.IMMUTABLE_PREFIX and not is_eligible_hook_prefix(fragment):
            continue
        if not _scope_matches(
            fragment.scope,
            session_id=session_id,
            run_id=run_id,
            loop_kind=loop_kind,
            stage=stage,
            attempt=attempt,
        ):
            continue
        selected.append(fragment)
    return tuple(selected)


def mark_hook_deliveries_consumed(
    context_state: Any,
    delivery_ids: tuple[str, ...],
    *,
    consumer: str,
) -> tuple[str, ...]:
    records = _delivery_records(context_state)
    consumptions = _delivery_consumptions(context_state)
    consumed: list[str] = []
    timestamp = now_utc_iso()
    for delivery_id in dict.fromkeys(delivery_ids):
        record = records.get(delivery_id)
        if not isinstance(record, dict) or record.get("status") != "active":
            continue
        record["status"] = "consumed"
        record["consumed_at"] = timestamp
        record["consumer"] = str(consumer or "").strip()
        consumptions.append(
            {
                "delivery_id": delivery_id,
                "consumer": str(consumer or "").strip(),
                "consumed_at": timestamp,
            }
        )
        consumed.append(delivery_id)
    del consumptions[:-MAX_HOOK_DELIVERY_CONSUMPTIONS]
    _trim_delivery_records(records)
    return tuple(consumed)


def _delivery_records(context_state: Any) -> dict[str, dict[str, Any]]:
    records = getattr(context_state, "hook_delivery_records", None)
    if not isinstance(records, dict):
        records = {}
        setattr(context_state, "hook_delivery_records", records)
    return records


def _delivery_consumptions(context_state: Any) -> list[dict[str, Any]]:
    consumptions = getattr(context_state, "hook_delivery_consumptions", None)
    if not isinstance(consumptions, list):
        consumptions = []
        setattr(context_state, "hook_delivery_consumptions", consumptions)
    return consumptions


def _trim_delivery_records(records: dict[str, dict[str, Any]]) -> None:
    overflow = len(records) - MAX_HOOK_DELIVERY_RECORDS
    if overflow <= 0:
        return
    removable = [
        key
        for key, record in records.items()
        if str(record.get("status", "")) == "consumed"
    ]
    for key in removable[:overflow]:
        records.pop(key, None)


def _scope_matches(
    scope: HookDeliveryScope,
    *,
    session_id: str,
    run_id: str,
    loop_kind: str,
    stage: str,
    attempt: int | None,
) -> bool:
    checks = (
        (scope.session_id, session_id),
        (scope.run_id, run_id),
        (scope.loop_kind, loop_kind),
        (scope.stage, stage),
    )
    if any(expected and expected != actual for expected, actual in checks):
        return False
    return scope.attempt is None or scope.attempt == attempt


def _target_for(
    event_name: HookEventName,
    field_kind: HookOutputField,
) -> HookDeliveryTarget:
    if field_kind is HookOutputField.ADDITIONAL_CONTEXT:
        return _ADDITIONAL_CONTEXT_TARGETS[event_name]
    return _FIELD_TARGETS[field_kind]


def _project_additional_context(
    atom: HookOutputAtom,
    *,
    request: HookRequest,
) -> tuple[HookModelContextFragment, ...]:
    if atom.target not in _MODEL_TARGETS:
        return ()
    if atom.target is HookDeliveryTarget.IMMUTABLE_PREFIX:
        if not is_eligible_hook_prefix(atom):
            return ()
    elif atom.trust_status not in {"managed", "trusted"}:
        return ()
    values = atom.value if isinstance(atom.value, list) else [atom.value]
    scope = _delivery_scope(request, target=atom.target)
    fragments: list[HookModelContextFragment] = []
    for index, value in enumerate(values, start=1):
        full_text = str(value or "").strip()
        if not full_text:
            continue
        content_digest = _sha256(full_text)
        preview = full_text
        fragments.append(
            HookModelContextFragment(
                delivery_id=_delivery_id(
                    atom=atom,
                    scope=scope,
                    content_digest=content_digest,
                    index=index,
                ),
                target=atom.target,
                text=preview,
                event_id=atom.event_id,
                event_name=atom.event_name,
                handler_key=atom.handler_key,
                handler_name=atom.handler_name,
                source=atom.source,
                trust_status=atom.trust_status,
                execution_mode=atom.execution_mode,
                content_digest=content_digest,
                scope=scope,
                locator="",
                source_ref=None,
                reader_route="",
                content_truncated=False,
            )
        )
    return tuple(fragments)


def _delivery_scope(
    request: HookRequest,
    *,
    target: HookDeliveryTarget,
) -> HookDeliveryScope:
    payload = request.payload
    attempt = payload.get("attempt")
    return HookDeliveryScope(
        session_id=_first_text(payload, "session_id"),
        run_id=_first_text(payload, "run_id"),
        loop_kind=_first_text(payload, "loop_kind", "actor_kind"),
        stage=_first_text(payload, "stage"),
        attempt=int(attempt) if attempt is not None else None,
        turn_id=_first_text(payload, "turn_id", "provider_call_id", "request_id"),
        tool_call_id=_first_text(payload, "tool_call_id", "call_id"),
        valid_from=request.created_at,
        one_shot=target in {
            HookDeliveryTarget.CONVERSATION,
        },
    )


def _first_text(payload: dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = str(payload.get(key, "") or "").strip()
        if value:
            return value
    return ""


def _delivery_id(
    *,
    atom: HookOutputAtom,
    scope: HookDeliveryScope,
    content_digest: str,
    index: int,
) -> str:
    payload = {
        "event_id": atom.event_id,
        "handler_key": atom.handler_key,
        "field": atom.field_kind.value,
        "target": atom.target.value,
        "content_digest": content_digest,
        "scope": scope.to_dict(),
        "index": index,
    }
    encoded = json.dumps(
        payload,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    )
    return f"hook-delivery:{_sha256(encoded)}"


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


__all__ = [
    "MAX_HOOK_DELIVERY_CONSUMPTIONS",
    "MAX_HOOK_DELIVERY_RECORDS",
    "build_delivery_batch",
    "is_eligible_hook_prefix",
    "mark_hook_deliveries_consumed",
    "merge_hook_delivery_batches",
    "persist_hook_delivery_batch",
    "select_active_hook_fragments",
]
