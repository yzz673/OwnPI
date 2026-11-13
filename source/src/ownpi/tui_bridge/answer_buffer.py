"""Frontend review buffer, rebuilt from durable original records on every revision.

The journal is the persistence layer: no Runtime state or feedback files are
written. A release is a deterministic display item, not an append/send operation.
New Runtime records require its validated answer_published authority. Legacy
records require a source-bound, digest-verified Reviewer receipt. Review context
is never answer text; message bodies and citation ownership are not rewritten.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from hashlib import sha256
import json
from pathlib import Path
from typing import Any


def digest(text: str) -> str:
    return sha256(text.encode("utf-8")).hexdigest()


def source_key(item: dict[str, Any]) -> tuple[str, str]:
    source = item.get("source") or {}
    return str(source.get("path") or ""), str(source.get("item_id") or "")


def arguments(item: dict[str, Any]) -> dict[str, Any]:
    try:
        value = json.loads(item.get("raw_source") or "{}").get("arguments", {})
        return value if isinstance(value, dict) else {}
    except (ValueError, TypeError, AttributeError):
        return {}


def is_core(item: dict[str, Any]) -> bool:
    return item.get("scope", {}).get("actor_kind") == "core" and not item.get(
        "audit_context"
    )


def is_candidate(item: dict[str, Any]) -> bool:
    payload = item.get("payload", {})
    return (
        is_core(item)
        and item.get("kind") == "agent_message"
        and not payload.get("source_list")
        and payload.get("message_purpose") != "progress"
        and bool(payload.get("text", "").strip())
        and (
            payload.get("phase") in {"final", "final_answer"}
            or payload.get("message_purpose") == "answer_candidate"
            or not payload.get("phase_recorded", True)
        )
    )


def review_passed(content: str) -> bool:
    # The current Reviewer contract's terminal success phrase. Do not match a
    # quote, a negation, a paragraph mentioning it, or an overall with findings.
    return content.strip().casefold() in {
        "no findings.",
        "no findings",
        "未发现问题。",
        "未发现问题",
    }


def stream_answer_policy(data: dict[str, Any], remembered: str = "") -> dict[str, Any]:
    """Core previews require an explicit progress phase, including legacy streams.

    Unclassified completed messages remain recoverable from the durable rollout;
    no provider-specific phase is guessed from prose. Reviewer/child activity is
    independent of the Core answer buffer.
    """
    if data.get("scope", {}).get("actor_kind") != "core" or data.get("audit_context"):
        return data
    result = dict(data)
    provider = result.get("provider_item") or {}
    phase = provider.get("phase", "") if isinstance(provider, dict) else ""
    purpose = result.get("message_purpose") or remembered
    if phase == "commentary":
        purpose = "progress"
    elif phase in {"final", "final_answer"}:
        purpose = "answer_candidate"
    if result.get("canonical_event_type") in {
        "assistant_text_delta",
        "item_started",
        "item_completed",
        "response_completed",
        "tool_call_arguments_delta",
    }:
        result["message_purpose"] = purpose or "unclassified"
        if result["message_purpose"] != "progress":
            for key in ("delta", "completed_text", "provider_item", "response_payload"):
                result.pop(key, None)
    return result


@dataclass
class Candidate:
    submission: dict[str, Any]
    text: str
    origin: dict[str, Any]
    messages: list[dict[str, Any]] = field(default_factory=list)
    source_lists: list[dict[str, Any]] = field(default_factory=list)
    state: str = "buffered"
    review_id: str = ""
    feedback_id: str = ""
    group: tuple[Any, ...] = ()
    order: int = -1
    publication_required: bool = False

    @property
    def identity(self) -> str:
        return f"{self.submission['item_id']}:reviewed-answer:{self.content_digest}"

    @property
    def content_digest(self) -> str:
        # Message boundaries and attached links are part of the delivery. Equal
        # prose with updated citations must not be mistaken for a duplicate.
        parts = self.messages or [{"payload": {"text": self.text}}]
        return digest(
            json.dumps(
                [
                    {
                        "text": part["payload"]["text"],
                        "sources": [
                            source["payload"]["text"]
                            for source in self.source_lists
                            if not self.messages
                            or source.get("parent_item_id") == part.get("item_id")
                        ],
                    }
                    for part in parts
                ],
                ensure_ascii=False,
            )
        )


class AnswerBuffer:
    def __init__(
        self,
        items: list[dict[str, Any]],
        events: Any,
        *,
        run_dir: Path | None = None,
        read_session: Any = None,
    ) -> None:
        self.items = items
        self.by_source = {
            source_key(item): item for item in items if all(source_key(item))
        }
        self.candidates: dict[tuple[str, str], Candidate] = {}
        self.releases: dict[str, list[dict[str, Any]]] = {}
        self.warnings: list[str] = []
        self._collect()
        events = tuple(events)
        self._bind(events)
        self._bind_publications(events, run_dir=run_dir, read_session=read_session)

    def _collect(self) -> None:
        lanes: dict[str, list[dict[str, Any]]] = {}
        held = set()
        for item in self.items:
            if is_core(item):
                lanes.setdefault(source_key(item)[0], []).append(item)
            if is_candidate(item):
                item["payload"]["detail_only"] = True
                item["payload"]["review_withheld"] = True
                item["visibility"] = "audit"
                held.add(item["item_id"])
        for item in self.items:
            if item.get("parent_item_id") in held:
                item["payload"]["detail_only"] = True
                item["visibility"] = "audit"
        for lane in lanes.values():
            pending: list[dict[str, Any]] = []
            for item in sorted(lane, key=lambda row: row.get("source_offset", 0)):
                if (
                    item["kind"] == "user"
                    or item.get("tool_name") == "complete_feedback"
                ):
                    pending = []
                if is_candidate(item):
                    pending.append(item)
                if item.get("tool_name") != "request_review":
                    continue
                args = arguments(item)
                explicit = args.get("answer")
                if isinstance(explicit, dict):
                    explicit = explicit.get("text")
                messages = pending
                if messages:
                    # Earlier exchanges can contain unphased narration. The
                    # last answer exchange is the actual submitted text.
                    exchange = messages[-1].get("exchange_id")
                    if exchange:
                        messages = [
                            m for m in messages if m.get("exchange_id") == exchange
                        ]
                if messages:
                    # This aggregate is only for receipt diagnostics. Release
                    # each original message separately, without Markdown joins.
                    text = "\n\n".join(m["payload"]["text"] for m in messages)
                    origin = {
                        "field": "assistant_message",
                        "items": [m["source"] for m in messages],
                    }
                elif isinstance(explicit, str) and explicit.strip():
                    # Legacy candidate fallback. For the current protocol,
                    # _bind selects answer.text regardless of assistant messages.
                    text, origin = explicit, {"field": "answer"}
                else:
                    # review_context is mixed-purpose Reviewer metadata. Labels,
                    # tags and prose cannot establish an answer boundary.
                    text, origin = "", {"reason": "missing_assistant_answer"}
                parents = {message["item_id"] for message in messages}
                self.candidates[source_key(item)] = Candidate(
                    item,
                    text,
                    origin,
                    messages,
                    source_lists=[
                        source
                        for source in self.items
                        if source.get("parent_item_id") in parents
                        and source["payload"].get("source_list")
                    ],
                    state="buffered" if text else "unavailable",
                )
                pending = []

    def _bind(self, events: tuple[Any, ...]) -> None:
        by_id = {event.event_id: event for event in events}
        positions = {event.event_id: index for index, event in enumerate(events)}
        protocol_start = next(
            (
                index
                for index, event in enumerate(events)
                if event.kind == "answer_protocol_enabled"
            ),
            len(events),
        )
        reviews: dict[str, Candidate] = {}
        for order, event in enumerate(events):
            data = event.data.to_object()
            if (
                event.kind != "critic_loop_transition"
                or data.get("action") != "review_started"
            ):
                continue
            accepted = by_id.get(data.get("accepted_event_id"))
            if not accepted or accepted.kind != "critic_loop_transition":
                continue
            intent = accepted.data.to_object()
            if intent.get("action") != "core_intent_accepted":
                continue
            source = intent.get("source") or {}
            candidate = self.candidates.get((source.get("path"), source.get("item_id")))
            if (
                not candidate
                or candidate.review_id
                or candidate.submission.get("status") in {"failed", "unverified"}
            ):
                continue
            candidate.review_id = event.event_id
            candidate.group = (
                intent.get("stage"),
                intent.get("attempt"),
                intent.get("stage_loop_index"),
            )
            candidate.order = order
            candidate.publication_required = (
                "answer_candidate" in intent
                or positions[accepted.event_id] >= protocol_start
            )
            candidate.state = "reviewing" if candidate.text else "unavailable"
            if candidate.publication_required:
                # New Runtime owns the reviewed body, which may intentionally
                # differ from the last assistant message. Only its validated
                # answer_published event can authorize display.
                candidate.state = "awaiting_publication"
                answer = arguments(candidate.submission).get("answer")
                candidate.text = (
                    answer.get("text", "") if isinstance(answer, dict) else ""
                )
                if not isinstance(candidate.text, str):
                    candidate.text = ""
                candidate.origin = {
                    "field": "answer",
                    "authority": "runtime_publication_required",
                }
            reviews[event.event_id] = candidate
        feedbacks: dict[str, list[tuple[int, Any]]] = {}
        for order, event in enumerate(events):
            if event.kind == "feedback_submitted":
                feedbacks.setdefault(
                    event.data.to_object().get("review_event_id", ""), []
                ).append((order, event))
        published: set[tuple[Any, ...]] = set()
        for review_id, candidate in reviews.items():
            if candidate.publication_required:
                continue
            matches = feedbacks.get(review_id, [])
            if len(matches) != 1:
                if matches:
                    candidate.state = "ambiguous_feedback"
                continue
            order, feedback = matches[0]
            data = feedback.data.to_object()
            source = data.get("source") or {}
            item = self.by_source.get((source.get("path"), source.get("item_id")), {})
            content = arguments(item).get("content")
            if (
                order <= candidate.order
                or item.get("tool_name") != "submit_feedback"
                or item.get("scope", {}).get("actor_kind") != "reviewer"
                or item.get("audit_context", {}).get("audit_id") != review_id
                or item.get("status") in {"failed", "unverified"}
                or not isinstance(content, str)
                or digest(content) != data.get("content_hash")
            ):
                candidate.state = "invalid_feedback"
                continue
            candidate.feedback_id = data.get("record_id", "")
            if not review_passed(content):
                candidate.state = "rejected"
                continue
            if any(
                other.group == candidate.group and candidate.order < other.order < order
                for other in reviews.values()
            ):
                candidate.state = "superseded"
                continue
            if not candidate.text:
                self.releases[feedback.event_id] = [
                    self._missing_answer(candidate, feedback.timestamp, order)
                ]
                continue
            publication = (*candidate.group, candidate.content_digest)
            if publication in published:
                candidate.state = "already_released"
                continue
            candidate.state = "released"
            published.add(publication)
            self.releases[feedback.event_id] = self._release(
                candidate, feedback.timestamp, order
            )

    def _bind_publications(
        self, events: tuple[Any, ...], *, run_dir: Path | None, read_session: Any
    ) -> None:
        by_review = {
            candidate.review_id: candidate
            for candidate in self.candidates.values()
            if candidate.publication_required
        }
        for order, event in enumerate(events):
            if event.kind != "answer_published":
                continue
            data = event.data.to_object()
            candidate = by_review.get(data.get("review_event_id"))
            try:
                if run_dir is None or candidate is None:
                    raise ValueError("publication has no bound original submission")
                # Reuse the installed Runtime's read-only authority checks. Do
                # not duplicate or weaken its candidate/hash/closure validation.
                from ownpi.runtime.rollout.answer_publication import (
                    read_published_answer,
                )

                published = read_published_answer(
                    events, event, run_dir=run_dir, read_session=read_session
                )
                if published.text != arguments(candidate.submission).get(
                    "answer", {}
                ).get("text"):
                    raise ValueError("publication differs from original answer.text")
            except (
                ImportError,
                OSError,
                ValueError,
                KeyError,
                TypeError,
                AttributeError,
            ) as error:
                self.warnings.append(
                    f"Answer publication unavailable ({event.event_id}): {error}"
                )
                if candidate is not None:
                    candidate.state = "invalid_publication"
                continue
            candidate.text = published.text
            candidate.origin = {
                "field": "answer",
                "answer_id": published.answer_id,
                "publication_id": event.event_id,
            }
            # Provider annotations belong to their original message. Transfer
            # them only if the whole body matches exactly, never by similarity.
            matches = [
                m for m in candidate.messages if m["payload"]["text"] == published.text
            ]
            parents = {message["item_id"] for message in matches}
            candidate.source_lists = [
                s for s in candidate.source_lists if s.get("parent_item_id") in parents
            ]
            # Render the authoritative tool body once, carrying every exact
            # matching message's sources without repeating the answer itself.
            candidate.messages = []
            candidate.feedback_id = data["feedback_record_id"]
            candidate.state = "released"
            released = self._release(candidate, event.timestamp, order)
            for item in released:
                if item.get("projection_kind") == "reviewed_answer":
                    item["payload"].update(
                        message_purpose="published_answer",
                        publication_id=event.event_id,
                        answer_id=published.answer_id,
                        revision=published.revision,
                        content_sha256=published.content_sha256,
                        artifacts=list(published.artifacts),
                    )
            self.releases[event.event_id] = released

    def _release(
        self, candidate: Candidate, timestamp: str, order: int
    ) -> list[dict[str, Any]]:
        result = []
        parts = candidate.messages or [candidate.submission]
        for index, original in enumerate(parts):
            text = original["payload"]["text"] if candidate.messages else candidate.text
            result.extend(
                self._release_part(candidate, original, text, index, timestamp, order)
            )
        return result

    def _release_part(
        self,
        candidate: Candidate,
        original: dict[str, Any],
        text: str,
        index: int,
        timestamp: str,
        order: int,
    ) -> list[dict[str, Any]]:
        receipt = {
            "submission": candidate.submission["source"],
            "review_event_id": candidate.review_id,
            "feedback_record_id": candidate.feedback_id,
            "body_sha256": digest(text),
            "content_sha256": candidate.content_digest,
            "part_index": index,
            "part_count": len(candidate.messages) or 1,
            "origin": candidate.origin,
        }
        item = {
            key: original[key]
            for key in (
                "schema_version",
                "scope",
                "agent_ref",
                "source",
                "stream_key",
                "exchange_id",
            )
            if key in original
        }
        item.update(
            item_id=f"{candidate.identity}:part:{index}",
            kind="agent_message",
            tool_name="",
            call_id="",
            visibility="user",
            status="completed",
            timestamp=timestamp,
            business_order=order + 0.5 + index / (2 * (len(candidate.messages) + 1)),
            projection_kind="reviewed_answer",
            payload={
                "text": text,
                "role": "assistant",
                "phase": "final",
                "phase_recorded": False,
                "message_purpose": "reviewed_answer",
                "answer_review": receipt,
            },
            raw_source=json.dumps(receipt, ensure_ascii=False, indent=2),
        )
        result = [item]
        for source in candidate.source_lists:
            if (
                candidate.publication_required
                or source.get("parent_item_id") == original["item_id"]
            ):
                result.append(
                    {
                        **source,
                        "item_id": f"{candidate.identity}:{source['item_id']}:sources",
                        "parent_item_id": item["item_id"],
                        "visibility": "user",
                        "timestamp": timestamp,
                        "business_order": item["business_order"]
                        + 1 / (4 * (len(candidate.messages) + 1)),
                        "payload": {**source["payload"], "detail_only": False},
                    }
                )
        return result

    def _missing_answer(
        self, candidate: Candidate, timestamp: str, order: int
    ) -> dict[str, Any]:
        return {
            "schema_version": "ownpi.tui.item.v1",
            "item_id": f"{candidate.identity}:missing",
            "kind": "answer_buffer_status",
            "projection_kind": "answer_buffer_status",
            "visibility": "user",
            "status": "unavailable",
            "timestamp": timestamp,
            "business_order": order + 0.5,
            "source": candidate.submission["source"],
            "payload": {
                "text": "前端交付提示：审核已通过，但本次送审没有独立的答案正文。"
                "送审说明未作为答案展示；可在 /audit 查看 request_review 原始记录。",
                "review_event_id": candidate.review_id,
                "reason": "missing_assistant_answer",
            },
        }

    def annotate(self) -> None:
        for candidate in self.candidates.values():
            payload = candidate.submission["payload"]
            payload["answer_buffer"] = {
                "state": candidate.state,
                "body_chars": len(candidate.text),
                "body_sha256": digest(candidate.text),
                "origin": candidate.origin,
                "review_event_id": candidate.review_id,
                "feedback_record_id": candidate.feedback_id,
            }
            payload["transcript"] += "\n\nAnswer buffer\n" + json.dumps(
                payload["answer_buffer"], ensure_ascii=False, indent=2
            )
