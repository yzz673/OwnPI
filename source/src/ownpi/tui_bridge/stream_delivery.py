"""Bounded, ordered delivery of transient events after canonical owner binding.

No durable execution state, guessed owners, or private reasoning in this buffer.
"""
from __future__ import annotations

import json
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Any
from ownpi.tui_bridge.answer_buffer import stream_answer_policy


@dataclass
class PendingStream:
    data: dict[str, Any]
    request_id: str
    received_at: float
    size: int
    lane: tuple[str, ...]


class StreamDelivery:
    def __init__(self, *, limit: int = 512, byte_limit: int = 2 * 1024 * 1024,
                 ttl: float = 30.0, clock: Callable[[], float] = time.monotonic) -> None:
        self.limit, self.byte_limit, self.ttl, self.clock = limit, byte_limit, ttl, clock
        self.run_dir: Path | None = None
        self.pending: deque[PendingStream] = deque()
        self.journal: deque[dict[str, Any]] = deque(maxlen=128)
        self.size = self.dropped = self.received = self.delivered = 0
        self._last_health: tuple[int, int] | None = None
        self.purposes: dict[tuple[Any, ...], str] = {}

    def select_run(self, run_dir: Path | None) -> None:
        if run_dir != self.run_dir:
            self.run_dir = run_dir
            self.pending.clear()
            self.journal.clear()
            self.size = self.dropped = self.received = self.delivered = 0
            self.purposes.clear()
            self._last_health = None

    def _public(self, data: dict[str, Any]) -> dict[str, Any]:
        key = tuple(data.get(field) for field in (
            "component", "conversation_id", "response_id", "provider_attempt", "item_id"))
        public = stream_answer_policy(data, self.purposes.get(key, ""))
        if data.get("item_id") and public.get("message_purpose"):
            self.purposes[key] = public["message_purpose"]
            if len(self.purposes) > self.limit:
                del self.purposes[next(iter(self.purposes))]
        return public

    def _record(self, data: dict[str, Any], outcome: str) -> None:
        public = str(data.get('delta') or '') if data.get('canonical_event_type') == 'assistant_text_delta' else ''
        self.journal.append({
            'event': data.get('canonical_event_type', data.get('event_kind', '')),
            'component': data.get('component'), 'conversation_id': data.get('conversation_id'),
            'stage_attempt': data.get('attempt'), 'provider_attempt': data.get('provider_attempt'),
            'response_id': data.get('response_id'), 'item_id': data.get('item_id'),
            'outcome': outcome, 'public_text_bytes': len(public.encode('utf-8')),
            'public_text_sha256': sha256(public.encode('utf-8')).hexdigest() if public else None,
        })

    def offer(self, data: dict[str, Any], request_id: str,
              resolve: Callable[[Path, dict[str, Any]], dict[str, Any]],
              emit: Callable[[str, dict[str, Any], str], None]) -> None:
        self.received += 1
        data = dict(data)
        kind = data.get('canonical_event_type')
        if data.get('message_purpose') in {'answer_candidate', 'unclassified'}:
            # Retain activity telemetry, never candidate text in a public preview.
            data.pop('delta', None)
            data.pop('completed_text', None)
            data.pop('provider_item', None)
            data.pop('response_payload', None)
        if kind == 'assistant_reasoning_delta':
            data.pop('delta', None)  # Activity only; never retain private text.
        if not kind or data.get('event_kind') == 'llm_status':
            emit('stream', data, request_id)  # Request telemetry does not need an item id.
            self.delivered += 1
            return
        if self.run_dir is None or not data.get('component'):
            self.dropped += 1
            self._record(data, 'missing_run_or_actor')
            self.publish_health(emit)
            return
        scope = data.get('stream_key')
        if isinstance(scope, list) and scope and scope[0] != self.run_dir.name:
            self.dropped += 1
            self._record(data, 'wrong_run')
            self.publish_health(emit)
            return
        if kind == 'assistant_text_delta' and (
            not data.get('item_id') or not isinstance(scope, list) or len(scope) != 4
            or scope[1] != data.get('component') or scope[3] != data.get('item_id')
        ):
            self.dropped += 1
            self._record(data, 'missing_or_conflicting_text_identity')
            self.publish_health(emit)
            return
        size = len(json.dumps(data, ensure_ascii=False).encode('utf-8'))
        lane = tuple(str(data.get(key) or '') for key in (
            'component', 'attempt', 'conversation_id', 'response_id', 'provider_attempt'))
        # The bound lane must keep flowing even when another actor fills the
        # unresolved buffer. Apply the same size bound to immediate delivery.
        self.pump(resolve, emit)
        if size <= self.byte_limit and not any(item.lane == lane for item in self.pending):
            try:
                bound = resolve(self.run_dir, data)
            except (OSError, ValueError, KeyError, TypeError):
                bound = None
            if bound and not bound.get('unresolved_agent', True):
                emit('stream', self._public(bound), request_id)
                self.delivered += 1
                self._record(bound, 'delivered')
                return
        if size > self.byte_limit or len(self.pending) >= self.limit or self.size + size > self.byte_limit:
            self.dropped += 1
            self._record(data, 'binding_capacity_exceeded')
            self.publish_health(emit)
            return
        self.pending.append(PendingStream(data, request_id, self.clock(), size, lane))
        self.size += size
        self.pump(resolve, emit)

    def pump(self, resolve: Callable[[Path, dict[str, Any]], dict[str, Any]],
             emit: Callable[[str, dict[str, Any], str], None]) -> None:
        remaining: deque[PendingStream] = deque()
        blocked: set[tuple[str, ...]] = set()
        for item in self.pending:
            if self.clock() - item.received_at >= self.ttl:
                self.size -= item.size
                self.dropped += 1
                self._record(item.data, 'binding_timeout')
                continue
            bound = None
            if item.lane not in blocked and self.run_dir is not None:
                try:
                    bound = resolve(self.run_dir, item.data)
                except (OSError, ValueError, KeyError, TypeError):
                    pass  # Keep the source event until retry or explicit timeout.
            if bound and not bound.get('unresolved_agent', True):
                emit('stream', self._public(bound), item.request_id)
                self.size -= item.size
                self.delivered += 1
                self._record(bound, 'delivered')
            else:
                blocked.add(item.lane)
                remaining.append(item)
        self.pending = remaining
        self.publish_health(emit)

    def publish_health(self, emit: Callable[[str, dict[str, Any], str], None]) -> None:
        key = (len(self.pending), self.dropped)
        if key != self._last_health:
            self._last_health = key
            emit('stream_health', {'pending': key[0], 'dropped': key[1]}, '')

    def audit(self) -> str:
        return '\n\n# Frontend stream delivery\n\n```json\n' + json.dumps({
            'received': self.received, 'delivered': self.delivered,
            'pending': len(self.pending), 'dropped': self.dropped,
            'buffer_bytes': self.size, 'recent_events': list(self.journal),
        }, ensure_ascii=False, indent=2) + '\n```\n'
