"""Read-only presentation of canonical original history and business facts.

No state restoration, tool execution, feedback materialization, or UI persistence.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any

from ownpi.runtime.limits.context import resolve_context_budget_limits
from ownpi.schemas.runtime import LLMConfig

from ownpi.controller.pending_interactions import (
    pending_human_interactions,
    pending_permission_interactions,
)
from ownpi.runtime.native_tools.history_items import (
    HistoryMessage,
    ToolOutputItem,
    ToolSearchOutputItem,
)
from ownpi.runtime.native_tools.provider import (
    ProviderOutputItem,
    ProviderOutputItemKind,
)
from ownpi.runtime.rollout.codec import decode_entry
from ownpi.runtime.rollout.feedback_projection import review_target
from ownpi.runtime.rollout.execution_events import project_execution_events
from ownpi.runtime.rollout.interaction_events import project_interactions
from ownpi.runtime.rollout.resume import list_conversations
from ownpi.runtime.rollout.direct_resume import is_direct_session
from ownpi.runtime.rollout.resume_read_session import ResumeReadSession
from ownpi.runtime.rollout.run_events import run_events_path
from ownpi.runtime.rollout.source_reader import read_original_call
from ownpi.tui_bridge.source_display import read_display_call
from ownpi.tui_bridge.presentation import present_tool, present_hosted_tool
from ownpi.tui_bridge.facts import approval_view, outcome_payload
from ownpi.tui_bridge.outcome_display import failure_diagnostics, outcome_timing
from ownpi.tui_bridge.display_context import bind_contexts, stream_context
from ownpi.tui_bridge.subagents import agent_ref, project_subagents
from ownpi.tui_bridge.message_sources import message_source_text
from ownpi.tui_bridge.direct_delivery import direct_reply_observation
from ownpi.tui_bridge.activity import ReviewTurns, current_activity
from ownpi.tui_bridge.answer_buffer import AnswerBuffer


def display_item(
    identity: str,
    kind: str,
    payload: dict[str, Any],
    *,
    status: str = "completed",
    tool_name: str = "",
    call_id: str = "",
    timestamp: str = "",
    **extra: Any,
) -> dict[str, Any]:
    return {
        "schema_version": "ownpi.tui.item.v1",
        "visibility": "user",
        "item_id": identity,
        "kind": kind,
        "payload": payload,
        "status": status,
        "tool_name": tool_name,
        "call_id": call_id,
        "timestamp": timestamp,
        **extra,
    }


def _body(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, (list, tuple)):
        return "\n".join(
            str(getattr(item, "text", ""))
            for item in value
            if getattr(item, "text", "")
        )
    return json.dumps(value, ensure_ascii=False)


def _reasoning(item: ProviderOutputItem) -> dict[str, Any]:
    raw = item.provider_payload.to_object()

    # Only declared provider fields; never turn a raw reasoning string into a summary.
    def parts(key: str) -> list[str]:
        value = raw.get(key, [])
        if isinstance(value, str):
            return [value] if value else []
        return (
            [
                part["text"]
                for part in value
                if isinstance(part, dict) and isinstance(part.get("text"), str)
            ]
            if isinstance(value, list)
            else []
        )

    return {
        "summary": parts("summary"),
        "content": parts("content"),
        "text": item.text,
        "transcript": item.text,
    }


def context_capacity(model: dict[str, Any]) -> tuple[int | None, str]:
    """Use recorded configuration and the backend's known model capabilities only."""
    explicit = model.get("context_window_limit")
    if type(explicit) is int and explicit > 0:
        return explicit, "llm_config"
    if not model.get("model") or not model.get("provider"):
        return None, "unknown"
    try:
        limits = resolve_context_budget_limits(LLMConfig.from_dict(model))
    except (TypeError, ValueError):
        return None, "unknown"
    if limits["source"] not in {"llm_config", "model_capability"}:
        return None, "unknown"
    return limits["context_window_limit"], limits["source"]


@dataclass
class RunProjection:
    signature: tuple[Any, ...]
    items: list[dict[str, Any]]
    warnings: list[str]
    usage: dict[str, Any]
    contexts: dict[str, dict[str, Any]]
    active_audits: set[str]
    subagents: list[dict[str, Any]]
    activity: dict[str, Any]


class CanonicalProjector:
    def __init__(self) -> None:
        self.reader = ResumeReadSession()
        self.cache: dict[Path, RunProjection] = {}

    def close(self) -> None:
        self.reader.clear()
        self.cache.clear()

    def read_run(self, run_dir: Path) -> RunProjection:
        run_dir = run_dir.resolve()
        raw_message = run_dir / "raw_user_message.txt"
        signature = self.reader.signature(run_dir) + tuple(
            (str(path), path.stat().st_size, path.stat().st_mtime_ns)
            for path in [raw_message, run_events_path(run_dir),
                         run_dir / "diagnostics/runtime.jsonl", run_dir.parent.parent / "task.json"]
            if path.exists()
        )
        cached = self.cache.get(run_dir)
        if cached is not None and cached.signature == signature:
            return cached
        items: list[dict[str, Any]] = []
        warnings: list[str] = []
        usage: dict[str, Any] = {}
        usage_candidates: list[tuple[Any, ...]] = []
        run_id = run_dir.name
        events_path = run_events_path(run_dir)
        events = (
            self.reader.business_events(events_path, run_id=run_id).events
            if events_path.exists()
            else ()
        )
        try:
            execution_records = project_execution_events(events).registry.records
        except (ValueError, KeyError, TypeError) as error:
            # A malformed execution fact cannot turn an acknowledgement into a
            # success or hide otherwise verifiable conversation/review history.
            execution_records = ()
            warnings.append(f"Execution status unavailable: {error}")
        direct = is_direct_session(events)
        requests = {event.data.to_object().get("input_id"): event for event in events
                    if event.kind == "direct_request_started"}
        delivered_inputs = {}
        for event in events:
            data = event.data.to_object()
            if event.kind == "direct_input_delivered" and data.get("input_id") in requests:
                source = data.get("source") or {}
                delivered_inputs[(source.get("path"), source.get("item_id"))] = data["input_id"]
        review_scopes: dict[tuple[Any, ...], list[dict[str, Any]]] = {}
        for event in events:
            if (
                event.kind != "critic_loop_transition"
                or event.data.to_object().get("action") != "review_started"
            ):
                continue
            target, round_index = review_target(
                events,
                event.event_id,
                run_root=run_dir,
                run_id=run_id,
                read_session=self.reader,
            )
            # Match the backend's explicit actor/stage/attempt/loop/round
            # metadata, never filenames, workspace hashes or message text.
            key = (
                "reviewer",
                f"{target.stage}__reviewer_round_{round_index:03}",
                target.attempt,
                target.loop_index,
                round_index,
            )
            review_scopes.setdefault(key, []).append(
                {
                    "audit_id": event.event_id,
                    "run_id": run_id,
                    "role": "reviewer",
                    "stage": target.stage,
                    "attempt": target.attempt,
                    "review_round": round_index,
                }
            )
        if raw_message.exists() and not direct:
            items.append(
                display_item(
                    f"{run_id}:request",
                    "user",
                    {"text": raw_message.read_text(encoding="utf-8")},
                    timestamp="",
                )
            )
        conversations = list(list_conversations(run_dir, read_session=self.reader))
        relationships = self.reader.relationships(run_dir)
        warnings.extend(relationships.diagnostics)
        for relation in relationships.relations:
            if relation.issues:
                warnings.extend(relation.issues)
                continue
            child = relationships.read_child(
                child_id=relation.actor_id,
                conversation_id=relation.conversation_id,
                parent_conversation_id=relation.parent_id,
                metadata_only=True,
                read_session=self.reader,
            )
            if child.path not in {value.path for value in conversations}:
                conversations.append(child)
        metadata_by_id = {
            value.conversation_id: dict(self.reader.index(value.path).descriptor.metadata)
            for value in conversations
        }
        child_labels = {}
        for event in events:
            if event.kind != "child_admitted":
                continue
            data = event.data.to_object()
            try:
                original = read_original_call(run_dir, data["source"], run_id=run_id,
                                              tool_name="run_subagents", read_session=self.reader)
                task_index = data["task_index"]
                if isinstance(task_index, bool) or not isinstance(task_index, int) or task_index < 0:
                    continue
                task = original.arguments["tasks"][task_index]
                label = task.get("task_name")
                if isinstance(label, str) and label:
                    child_labels[data["child_id"]] = label
            except (ValueError, KeyError, IndexError, TypeError):
                continue  # No original task binding means no invented task label.
        for metadata in metadata_by_id.values():
            if metadata.get("actor_kind") == "subagent":
                actor = str(metadata.get("actor_id") or "")
                label = child_labels.get(actor)
                metadata["actor_label"] = f"Subagent · {label} · {actor[:8]}" if label else f"Subagent · {actor[:8]}"
        audits = {}
        for identity, metadata in metadata_by_id.items():
            matching = review_scopes.get(tuple(metadata.get(key) for key in (
                "actor_kind", "stage", "attempt", "stage_loop_index", "critic_round",
            )), [])
            if len(matching) == 1:
                audits[identity] = matching[0]
        contexts = bind_contexts(metadata_by_id, {
            relation.conversation_id: relation.parent_id
            for relation in relationships.relations if not relation.issues
        }, audits)
        for identity, context in contexts.items():
            context["agent_ref"] = agent_ref(run_id, identity, metadata_by_id[identity])
        submitted_reviews = {
            value.data.to_object().get("review_event_id")
            for value in events if value.kind == "feedback_submitted"
        }
        review_turns = ReviewTurns()
        for conversation in conversations:
            index = self.reader.index(conversation.path)
            if index.descriptor.parse_errors:
                warnings.append(
                    f"{conversation.path.name}: {index.descriptor.parse_errors} unreadable records"
                )
            metadata = index.descriptor.metadata
            context = contexts[conversation.conversation_id]
            context_fields = {key: value for key, value in context.items() if key in {"scope", "audit_context", "agent_ref"}}
            records = list(self.reader.raw_records(index, index.records))
            for location, raw in records:
                review_turns.observe(raw, context, {"path": str(conversation.path.relative_to(run_dir)), "offset": location.offset})
            from ownpi.runtime.rollout.codec import RolloutRecord

            pages = {}
            search_results = {}
            for location, raw in records:
                if location.type != "response_item":
                    continue
                entry = decode_entry(RolloutRecord.from_dict(raw))
                if isinstance(entry.item, ToolOutputItem) and entry.page is not None:
                    output = entry.item
                    pages[
                        (
                            output.tool_name,
                            output.call_id,
                            output.request_id,
                            _body(output.body),
                        )
                    ] = entry.page
                elif isinstance(entry.item, ToolSearchOutputItem):
                    output = entry.item
                    search_results.setdefault(
                        (output.tool_name, output.call_id, output.request_id), []
                    ).append((location.offset, output))
            compacted_at = ""
            normal_usage = None
            context_limit, context_limit_source = context_capacity(metadata.get("model") or {})
            for location, raw in records:
                payload = raw["payload"]
                if location.type == "turn_context" and isinstance(payload.get("model"), dict):
                    context_limit, context_limit_source = context_capacity(payload["model"])
                identity = f"{run_id}:{conversation.conversation_id}:{location.item_id or location.offset}"
                if location.type == "compacted":
                    compacted_at = raw["timestamp"]
                    items.append(
                        display_item(
                            identity,
                            "context_compaction",
                            {
                                "title": "Context compacted",
                                "text": f"{len(payload.get('replacement_history', []))} canonical history entries retained",
                                "transcript": "Canonical context compaction; original history remains inspectable.",
                            },
                            timestamp=raw["timestamp"],
                            **context_fields,
                        )
                    )
                elif location.type == "inter_agent_communication":
                    items.append(
                        display_item(
                            identity,
                            "sub_agent_activity",
                            {
                                "title": f"{payload['source']} → {payload['target']}",
                                "text": payload["kind"],
                                "transcript": payload["text"],
                            },
                            timestamp=raw["timestamp"],
                            **context_fields,
                        )
                    )
                if (
                    location.type == "event_msg"
                    and payload.get("type") == "token_count"
                ):
                    if payload.get("purpose", "normal") == "normal":
                        normal_usage = (
                            metadata.get("actor_kind") == "core",
                            raw["timestamp"],
                            {
                                **dict(payload.get("usage") or {}),
                                "is_estimate": not bool(payload.get("exact_usage")),
                                "conversation_id": conversation.conversation_id,
                                "actor_id": metadata.get("actor_id"),
                                "context_window_limit": context_limit,
                                "context_limit_source": context_limit_source,
                            },
                        )
                    receipt = display_item(
                        identity,
                        "model_activity",
                        {
                            "label": "Model response",
                            "component": metadata.get("actor_id", ""),
                            "operation": payload.get("purpose", "normal"),
                            "provider_request_id": payload.get("response_id")
                            or payload.get("exchange_id")
                            or "",
                            "transcript": json.dumps(
                                {
                                    "timestamp": raw["timestamp"],
                                    "actor": metadata.get("actor_id"),
                                    "exchange_id": payload.get("exchange_id"),
                                    "purpose": payload.get("purpose", "normal"),
                                    "usage": payload.get("usage"),
                                    "exact_usage": payload.get("exact_usage"),
                                },
                                ensure_ascii=False,
                                indent=2,
                            ),
                        },
                        timestamp=raw["timestamp"],
                        **context_fields,
                    )
                    receipt["visibility"] = "audit"
                    items.append(receipt)
                if location.type != "response_item":
                    continue
                record = RolloutRecord.from_dict(raw)
                entry = decode_entry(record)
                item = entry.item
                identity = f"{run_id}:{conversation.conversation_id}:{location.item_id or location.offset}"
                source = {
                    "path": str(conversation.path.relative_to(run_dir)),
                    "item_id": location.item_id,
                }
                provider_id = getattr(item, "provider_item_id", "")
                extra = {
                    "source_offset": location.offset,
                    "exchange_id": entry.exchange_id,
                    "provider_item_id": provider_id,
                    "agent_ref": context["agent_ref"],
                    "source": source,
                    "scope": context["scope"],
                    "stream_key": [
                        run_id,
                        metadata.get("actor_id", ""),
                        metadata.get("attempt", 0),
                        provider_id,
                    ],
                }
                if context.get("audit_context"):
                    extra["audit_context"] = context["audit_context"]
                if isinstance(item, ProviderOutputItem):
                    if item.kind is ProviderOutputItemKind.MESSAGE and item.text:
                        items.append(
                            display_item(
                                identity,
                                "agent_message",
                                {
                                    "text": item.text,
                                    "role": "assistant",
                                    "phase": item.provider_payload.to_object().get(
                                        "phase"
                                    )
                                    or "commentary",
                                    "phase_recorded": bool(item.provider_payload.to_object().get("phase")),
                                    "message_purpose": item.provider_payload.to_object().get("message_purpose", ""),
                                },
                                timestamp=record.timestamp,
                                **extra,
                            )
                        )
                        sources = message_source_text(item)
                        if sources:
                            # A separate item keeps malformed Markdown in the answer
                            # from swallowing the source list. Share its stream key so
                            # the completed snapshot retires the original live text.
                            items.append(display_item(
                                f"{identity}:sources", "agent_message",
                                {"text": sources, "role": "assistant", "phase": "commentary",
                                 "source_list": True},
                                timestamp=record.timestamp, parent_item_id=identity,
                                projection_kind="provider_message_source_list", **extra,
                            ))
                    elif item.kind is ProviderOutputItemKind.REASONING:
                        items.append(
                            display_item(
                                identity,
                                "reasoning",
                                _reasoning(item),
                                timestamp=record.timestamp,
                                **extra,
                            )
                        )
                    elif item.kind is ProviderOutputItemKind.TOOL_CALL:
                        binding_error = ""
                        try:
                            original = read_display_call(
                                run_dir,
                                source,
                                run_id=str(metadata.get("run_id") or run_id),
                                read_session=self.reader,
                            )
                        except ValueError as error:
                            # A bad binding affects this source only. Do not guess
                            # the missing result or freeze the whole Runtime view.
                            warnings.append(f"{item.tool_name} ({identity}): {error}")
                            original = None
                            binding_error = str(error)
                        outputs = original.outputs if original else ()
                        # Native tool-search results have their own canonical
                        # type; the backend business-call reader intentionally
                        # returns only ToolOutputItem. Bind search definitions by
                        # the complete wire identity, within this invocation's
                        # source interval, without relaxing business validation.
                        if original and not outputs and item.tool_name == "tool_search":
                            end = min(
                                (
                                    other.offset
                                    for identity in {item.call_id, item.request_id}
                                    if identity
                                    for other in index.wire_calls.get(identity, ())
                                    if other.offset > location.offset
                                ),
                                default=index.version[2],
                            )
                            outputs = tuple(
                                value
                                for offset, value in search_results.get(
                                    (item.tool_name, item.call_id, item.request_id), ()
                                )
                                if location.offset < offset < end
                            )
                        output = "\n".join(
                            value.body
                            or json.dumps(
                                {"tools": [tool.to_object() for tool in value.tools]},
                                ensure_ascii=False,
                            )
                            if isinstance(value, ToolSearchOutputItem)
                            else _body(value.body)
                            for value in outputs
                        )
                        success = next(
                            (
                                value.success
                                for value in reversed(outputs)
                                if value.success is not None
                            ),
                            None,
                        )
                        state = (
                            "unverified"
                            if binding_error
                            else (
                                ("failed" if success is False else "completed")
                                if outputs
                                else "pending"
                            )
                        )
                        try:
                            arguments = (
                                original.arguments
                                if original
                                else json.loads(item.raw_arguments)
                            )
                            if not isinstance(arguments, dict):
                                arguments = {}
                        except (ValueError, TypeError):
                            arguments = {}
                        windows = []
                        for value in outputs:
                            page = pages.get(
                                (
                                    value.tool_name,
                                    value.call_id,
                                    value.request_id,
                                    _body(value.body),
                                )
                            )
                            if (
                                page
                                and page.visible_start is not None
                                and page.visible_end is not None
                            ):
                                windows.append(
                                    {
                                        "start_line": page.visible_start,
                                        "end_line": page.visible_end,
                                        "has_more": page.has_more,
                                        "next_offset": page.next_offset,
                                        "exact": True,
                                    }
                                )
                        view = present_tool(
                            item.tool_name,
                            arguments,
                            output,
                            state,
                            workspace=Path(metadata.get("workspace") or run_dir),
                            read_windows=windows,
                            execution_records=execution_records,
                        )
                        if view["kind"] == "file_change":
                            view["payload"]["compact_preview"] = not direct
                        items.append(
                            display_item(
                                identity,
                                view["kind"],
                                view["payload"],
                                action=view["action"],
                                tool_name=item.tool_name,
                                call_id=item.call_id or item.request_id,
                                status=view["status"],
                                timestamp=record.timestamp,
                                raw_source=json.dumps(
                                    {
                                        "arguments": arguments,
                                        "result": output,
                                        **({"binding_note": original.identity_compatibility}
                                           if original and original.identity_compatibility else {}),
                                        **(
                                            {"binding_error": binding_error}
                                            if binding_error
                                            else {}
                                        ),
                                    },
                                    ensure_ascii=False,
                                    indent=2,
                                ),
                                **extra,
                            )
                        )
                    elif item.kind in {
                        ProviderOutputItemKind.HOSTED_TOOL_CALL,
                        ProviderOutputItemKind.HOSTED_TOOL_RESULT,
                    }:
                        hosted = present_hosted_tool(item.provider_type, item.provider_payload.to_object())
                        items.append(
                            display_item(
                                identity,
                                hosted["kind"], hosted["payload"],
                                status=hosted["status"], action=hosted["action"],
                                tool_name=item.tool_name or item.provider_type,
                                raw_source=json.dumps(item.provider_payload.to_object(), ensure_ascii=False, indent=2),
                                timestamp=record.timestamp,
                                **extra,
                            )
                        )
                elif isinstance(item, (ToolOutputItem, ToolSearchOutputItem)):
                    # Results are bound to their original invocation above, never appended twice.
                    continue
                elif isinstance(item, HistoryMessage):
                    input_id = delivered_inputs.get((source["path"], source["item_id"]))
                    if (input_id and item.role == "user" and entry.exchange_id == input_id
                            and metadata.get("actor_kind") == "core"):
                        items.append(display_item(identity, "user", {"text": item.text},
                                                  timestamp=record.timestamp, **extra))
                    # Other HistoryMessages are internal context, not user inputs.
            if normal_usage:
                if compacted_at > normal_usage[1]:
                    normal_usage[2].pop("provider_prompt_tokens", None)
                    normal_usage[2]["is_estimate"] = True
                usage_candidates.append(normal_usage)
        answer_buffer = AnswerBuffer(items, events, run_dir=run_dir, read_session=self.reader)
        answer_buffer.annotate()
        warnings.extend(answer_buffer.warnings)
        interactions = project_interactions(events)
        for event_order, event in enumerate(events):
            data = event.data.to_object()
            identity = f"{run_id}:event:{event.event_id}"
            display_timestamp = event.timestamp
            kind, payload, state = "", {}, "completed"
            audit = None
            if event.kind == "run_initialized":
                kind, payload = (
                    "route_decision",
                    {
                        "route_kind": "runtime_profile",
                        "route_label": "Task" if data["execution_tier"] == "direct" else "Research",
                    },
                )
            elif event.kind == "workflow_plan_selected":
                stages = data.get("stage_sequence") or []
                if stages:
                    kind, payload = "command_execution", {"workflow_stages": stages,
                        "command": "Research workflow", "output": [],
                        "transcript": json.dumps(data, ensure_ascii=False, indent=2)}
            elif event.kind == "stage_attempt_started" and not direct:
                state = "running"
                kind, payload = (
                    "stage",
                    {**data, "run_id": run_id, "event": "started", "status": "running"},
                )
            elif event.kind == "stage_gate_decided" and not direct:
                gate = data.get("gate", {})
                kind, payload = (
                    "stage",
                    {**data, **gate, "run_id": run_id, "event": "completed", "reason_code": gate.get("reason", ""),
                     **({"reason_display": "Core feedback accepted after review"}
                        if gate.get("reason") == "core_completed_feedback" else {})},
                )
            elif event.kind == "critic_loop_transition" and data.get("action") == "core_feedback_started":
                feedback = next((value.data.to_object() for value in events
                                 if value.kind == "feedback_submitted"
                                 and value.data.to_object().get("record_id") == data.get("feedback_record_id")), None)
                if feedback and feedback.get("review_event_id"):
                    target, round_index = review_target(events, feedback["review_event_id"],
                        run_root=run_dir, run_id=run_id, read_session=self.reader)
                    kind, payload = "review", {
                        "title": "Core · processing review feedback", "text": f"{target.stage.replace('_', ' ')} · Attempt {target.attempt} · Round {round_index}",
                        "phase": "applying_feedback", "stage": target.stage, "attempt": target.attempt, "round": round_index,
                        "transcript": json.dumps(data, ensure_ascii=False, indent=2),
                    }
            elif (
                event.kind == "critic_loop_transition"
                and data.get("action") == "review_started"
            ):
                target, round_index = review_target(
                    events,
                    event.event_id,
                    run_root=run_dir,
                    run_id=run_id,
                    read_session=self.reader,
                )
                kind, state = "review_cycle", "running"
                submitted = next(
                    (
                        value
                        for value in events
                        if value.kind == "feedback_submitted"
                        and value.data.to_object().get("review_event_id")
                        == event.event_id
                    ),
                    None,
                )
                if submitted:
                    continue  # The submitted feedback is this review's canonical terminal card.
                payload = {
                    "review_kind": "reviewer",
                    "stage": target.stage,
                    "attempt": target.attempt,
                    "round": round_index,
                    "phase": "started",
                    "review_target": target.identity,
                }
                audit = {
                    "audit_id": event.event_id,
                    "run_id": run_id,
                    "role": "reviewer",
                    "stage": target.stage,
                    "attempt": target.attempt,
                    "review_round": round_index,
                }
                interrupted = review_turns.terminal(event.event_id)
                if interrupted:
                    state = interrupted["status"]
                    payload.update(phase=state, lifecycle_source=interrupted["source"])
                    display_timestamp = interrupted["timestamp"]
            elif event.kind == "feedback_submitted":
                call = read_original_call(
                    run_dir,
                    data["source"],
                    run_id=run_id,
                    tool_name="submit_feedback",
                    read_session=self.reader,
                )
                content = call.arguments.get("content", "")
                role, stage, attempt, round_index = (
                    data.get("author_role", ""),
                    data.get("stage", ""),
                    data.get("attempt", 0),
                    1,
                )
                if data.get("review_event_id"):
                    target, round_index = review_target(
                        events,
                        data["review_event_id"],
                        run_root=run_dir,
                        run_id=run_id,
                        read_session=self.reader,
                    )
                    role, stage, attempt = "reviewer", target.stage, target.attempt
                    audit = {
                        "audit_id": data["review_event_id"],
                        "run_id": run_id,
                        "role": "reviewer",
                        "stage": stage,
                        "attempt": attempt,
                        "review_round": round_index,
                    }
                    # The feedback's canonical source names its reviewer
                    # conversation. Bind all messages/tools in that conversation,
                    # rather than only the final submit_feedback tool card.
                    for item in items:
                        if item.get("source", {}).get("path") == data["source"]["path"]:
                            item["audit_context"] = audit
                kind, payload = (
                    "review_cycle",
                    {
                        "review_kind": role,
                        "stage": stage,
                        "attempt": attempt,
                        "round": round_index,
                        "phase": "completed",
                        "outcome": "submitted",
                        "summary": content,
                        "review_record_id": data["record_id"],
                        "feedback_content_hash": data["content_hash"],
                        "transcript": content,
                    },
                )
            elif event.kind == "interaction_answer_accepted":
                interaction = interactions[data["session_id"]].session
                kind, payload = (
                    "interaction_result",
                    {
                        "interaction_id": interaction.session_id,
                        "interaction_kind": "human_question",
                        "title": "Answers accepted",
                        "outcome": "answered",
                        "answers": [
                            {
                                "question_id": question.question_id,
                                "question": question.question,
                                "answer": ", ".join(
                                    interaction.answers.get(
                                        question.question_id, {}
                                    ).get("selected_labels", [])
                                ),
                                "note": interaction.answers.get(
                                    question.question_id, {}
                                ).get("free_text")
                                or "",
                            }
                            for question in interaction.questions
                        ],
                    },
                )
            elif event.kind in {
                "child_admitted",
                "child_execution_started",
                "child_execution_finished",
                "child_completion_committed",
                "child_resources_released",
                "child_worker_reclaimed",
            }:
                candidates = [value for value in contexts.values()
                              if value["scope"].get("actor_id") == data.get("child_id")]
                context = candidates[0] if len(candidates) == 1 else {}
                audit = context.get("audit_context")
                label = context.get("scope", {}).get("actor_label") or str(data.get("child_id") or "")
                kind, payload = (
                    "sub_agent_activity",
                    {
                        "title": event.kind.replace("_", " ").title(),
                        "detail_only": True,
                        "text": label,
                        "transcript": json.dumps(data, ensure_ascii=False, indent=2),
                    },
                )
            elif event.kind in {"direct_request_completed", "direct_request_failed"}:
                index = data["loop_index"]
                completed = event.kind == "direct_request_completed"
                state = "completed" if completed else "failed"
                kind, payload = "task_outcome", {
                    **outcome_payload("idle" if completed else "failed"),
                    "task_id": run_id, "request_index": index,
                    "outcome": "idle" if completed else "failed",
                    "outcome_source": event.kind,
                    "display_status": "Request completed" if completed else "Request failed",
                }
                started = next((value for value in reversed(events)
                    if value.kind == "direct_request_started"
                    and value.data.to_object().get("loop_index") == index
                    and value.timestamp <= event.timestamp), None)
                if started:
                    payload.update(outcome_timing(run_dir, started.timestamp, event.timestamp,
                                                  use_run_created_at=False))
                    if completed:
                        payload.update(direct_reply_observation(items, started.timestamp, event.timestamp))
                if not completed:
                    failure = data.get("failure") or {}
                    payload.update(failure_diagnostics(run_dir, event.timestamp, started.timestamp if started else ""))
                    payload["failure_summary"] = str(failure.get("message") or failure.get("reason_code") or "")
                    payload["failure_detail"] = json.dumps(failure, ensure_ascii=False)
                    payload["recovery_actions"] = ["Open /resume to inspect this request before resuming."]
            elif event.kind == "run_finalized":
                kind, payload = (
                    "task_outcome",
                    {
                        "task_id": run_id,
                        **outcome_payload(data.get("status", "")),
                        "runtime_rationale": [str(data.get("decision") or "")],
                    },
                )
                initialized = next(
                    (value for value in events if value.kind == "run_initialized"), None
                )
                if initialized:
                    payload.update(outcome_timing(run_dir, initialized.timestamp, event.timestamp))
                if data.get("status") == "failed":
                    earlier = [value.timestamp for value in events
                               if value.kind == "run_finalized" and value.timestamp < event.timestamp]
                    payload.update(failure_diagnostics(run_dir, event.timestamp, max(earlier, default="")))
                    payload["recovery_actions"] = ["Open /resume to inspect this run; resuming requires your action."]
            if kind:
                items.append(
                    display_item(
                        identity,
                        kind,
                        payload,
                        status=state,
                        tool_name="workflow_plan" if event.kind == "workflow_plan_selected" else "",
                        timestamp=display_timestamp,
                        business_order=event_order,
                        **({"audit_context": audit} if audit else {}),
                    )
                )
            items.extend(answer_buffer.releases.get(event.event_id, ()))
        # Preserve committed business-event order when timestamps tie, so review
        # and outcome cards retain their source order.
        first = 1 if raw_message.exists() and not direct else 0
        items[first:] = sorted(
            items[first:], key=lambda value: (
                value["timestamp"], "business_order" in value,
                value.get("business_order", 0), value["item_id"],
            )
        )
        if direct:
            # Initialization is recorded before the first input is delivered. Present
            # that verified input first without rewriting its canonical timestamp.
            initial_input = next((value for value in items if value["kind"] == "user"), None)
            if initial_input is not None:
                items.remove(initial_input)
                items.insert(0, initial_input)
        if usage_candidates:
            usage = max(usage_candidates, key=lambda value: value[:2])[2]
        subagents = project_subagents(run_dir, tuple(events), self.reader, contexts, relationships, items, warnings)
        active_audits = {audit["audit_id"] for audit in audits.values()
                         if audit["audit_id"] not in submitted_reviews and not review_turns.terminal(audit["audit_id"])}
        result = RunProjection(signature, items, warnings, usage, contexts, active_audits, subagents, current_activity(items))
        self.cache[run_dir] = result
        return result

    def present_stream(self, run_dir: Path, event: dict[str, Any]) -> dict[str, Any]:
        projected = self.read_run(run_dir)
        context = stream_context(projected.contexts, projected.active_audits, event)
        return {**event, **context, "unresolved_agent": not bool(context)}

    def interactions(self, run_dir: Path) -> list[dict[str, Any]]:
        pending = [
            {"kind": "human", **item} for item in pending_human_interactions(run_dir)
        ]
        pending.extend(
            approval_view(item) for item in pending_permission_interactions(run_dir)
        )
        return [{"run_id": run_dir.name, **value} for value in pending]


