"""Reconstruct rollout facts and restore missing approved resource copies on demand."""

from __future__ import annotations

from dataclasses import dataclass, field
from functools import cached_property
from hashlib import sha256
from pathlib import Path
from typing import Any

from ownpi.runtime.limits.tool_output import TruncationPolicy
from ownpi.runtime.native_tools.history import NativeHistory
from ownpi.runtime.native_tools.history_items import HistoryEntry
from ownpi.runtime.native_tools.provider import ProviderOutputItem, ProviderOutputItemKind
from ownpi.runtime.native_tools.provider_json import FrozenProviderJson
from ownpi.runtime.rollout.business_events import BusinessEvent
from ownpi.runtime.rollout.codec import RolloutRecord, decode_entry
from ownpi.runtime.rollout.agent_relationships import AgentRelationships, read_agent_relationships
from ownpi.runtime.rollout.run_events import run_events_path
from ownpi.runtime.rollout.business_scope import business_stage
from ownpi.runtime.rollout.resume_read_session import (
    ConversationIndex, ConversationMetadata, ResumeReadSession,
)


@dataclass(frozen=True)
class ResumedConversation:
    path: Path
    conversation_id: str
    metadata: dict[str, Any]
    entries: tuple[HistoryEntry, ...]
    item_ids: tuple[str, ...]
    consumed_ids: frozenset[str]
    context: FrozenProviderJson | None
    usage_exchanges: frozenset[str]
    turn_id: str
    generation: int
    parse_errors: int
    turn_status: str
    replacement_count: int
    token_info: FrozenProviderJson | None
    original_item_ids: frozenset[str]
    source_contexts: frozenset[FrozenProviderJson]
    read_session: ResumeReadSession | None = field(default=None, repr=False, compare=False)
    file_version: tuple[int, int, int, int] | None = field(default=None, repr=False, compare=False)

    def build_history(self, policy: TruncationPolicy) -> tuple[NativeHistory, list[tuple[HistoryEntry, str]]]:
        """Install replacement verbatim and project only the raw suffix normally."""
        history = NativeHistory(policy)
        history.replace(self.entries[:self.replacement_count])
        retained = list(zip(history.snapshot, self.item_ids[:self.replacement_count], strict=True))
        for entry, identity in zip(self.entries[self.replacement_count:],
                                   self.item_ids[self.replacement_count:], strict=True):
            retained.extend((item, identity) for item in history.record_items((entry,)))
        history.restore(history.snapshot, generation=self.generation,
                        consumed=(entry for entry, identity in retained
                                  if identity and identity in self.consumed_ids))
        history.reference_context = self.context
        history.turn_status = self.turn_status
        history.token_info = self.token_info
        return history, retained

    def check_identity(self, expected: dict[str, Any]) -> None:
        for key, value in expected.items():
            if self.metadata.get(key) != value:
                raise ValueError(f'Resumed conversation {key} does not match current actor scope')


class NoActorConversationError(ValueError):
    """The run has durable business facts but no actor rollout yet."""


def replay_conversation(path: Path, *, conversation_id: str | None = None,
                        read_session: ResumeReadSession | None = None) -> ResumedConversation:
    return (read_session or ResumeReadSession()).replay(Path(path), conversation_id=conversation_id)


def _replay_index(session: ResumeReadSession, index: ConversationIndex) -> ResumedConversation:
    entries: list[HistoryEntry] = []
    ids: list[str] = []
    consumed: set[str] = set()
    context = None
    usage: set[str] = set()
    turn_id = ''
    generation = 0
    turn_status = 'none'
    replacement_count = 0
    token_info = None
    original_ids = {value.item_id for value in index.records
                    if value.type == 'response_item' and value.item_id}
    contexts: set[FrozenProviderJson] = set()
    checkpoint = max((value.offset for value in index.records if value.type == 'compacted'), default=-1)
    for location, raw in session.replay_records(index, checkpoint):
        if location.type == 'compacted' and location.offset != checkpoint:
            consumed.intersection_update(location.replacement_ids)
            replacement_count = len(entries)
            generation += 1
            continue
        assert raw is not None
        payload = raw['payload']
        if location.type == 'response_item':
            entries.append(decode_entry(RolloutRecord.from_dict(raw)))
            ids.append(location.item_id)
        elif location.type == 'compacted':
            for value, meta in zip(payload['replacement_history'],
                                   payload['replacement_history_metadata'], strict=True):
                entries.append(decode_entry(RolloutRecord.create('response_item', value, meta)))
                ids.append(str(meta.get('item_id') or ''))
            consumed.intersection_update(ids)
            replacement_count = len(entries)
            generation += 1
        elif location.type == 'turn_context':
            context = FrozenProviderJson.from_value(payload)
            contexts.add(context)
            turn_id = str(payload.get('turn_id') or turn_id)
        elif location.type == 'event_msg':
            kind = payload.get('type')
            if kind == 'protected_output_consumed':
                consumed = set(payload.get('item_ids') or ())
            elif kind == 'token_count':
                if isinstance(payload.get('exchange_id'), str):
                    usage.add(payload['exchange_id'])
                if payload.get('purpose', 'normal') == 'normal':
                    token_info = FrozenProviderJson.from_value(payload)
            elif kind == 'turn_started':
                turn_id = str(payload.get('turn_id') or turn_id)
                turn_status = 'interrupted'
            elif kind == 'turn_complete':
                turn_status = 'completed'
            elif kind == 'turn_aborted':
                turn_status = 'aborted'
        elif location.type == 'inter_agent_communication':
            message = {'type': 'agent_message', 'author': payload['source'],
                       'recipient': payload['target'],
                       'content': [{'type': 'input_text', 'text': payload['text']}]}
            entries.append(HistoryEntry(ProviderOutputItem(
                kind=ProviderOutputItemKind.MESSAGE, sequence_index=0,
                provider_type='agent_message', provider_payload=FrozenProviderJson.from_value(message),
                text=payload['text'],
            ), turn_id=turn_id))
            ids.append(location.item_id)
    descriptor = index.descriptor
    return ResumedConversation(descriptor.path, descriptor.conversation_id, descriptor.metadata, tuple(entries),
                               tuple(ids), frozenset(consumed), context,
                               frozenset(usage),
                               turn_id, generation, descriptor.parse_errors, turn_status,
                               replacement_count, token_info, frozenset(original_ids), frozenset(contexts),
                               session, index.version)


def list_conversations(run_dir: Path, *, read_session: ResumeReadSession | None = None) -> tuple[ConversationMetadata, ...]:
    """Inventory valid identities without selecting an actor or changing any file."""
    return conversation_catalog(run_dir, read_session=read_session)


def _run_conversation_inventory(run_dir: Path, *, read_session: ResumeReadSession | None = None) -> tuple[tuple[ConversationMetadata, ...], tuple[tuple[Path, dict[str, Any] | None, str], ...]]:
    session = read_session or ResumeReadSession()
    root = Path(run_dir).resolve() / 'rollouts'
    candidates: list[ConversationMetadata] = []
    failures: list[tuple[Path, dict[str, Any] | None, str]] = []
    for path in sorted(root.glob('*.jsonl')):
        if path.name == 'run-events.jsonl' or not path.is_file():
            continue
        try:
            replay = session.index(path).descriptor
        except (OSError, ValueError, TypeError, KeyError, UnicodeError) as error:
            metadata = None
            try:
                metadata = session.metadata_hint(path)
                if not isinstance(metadata, dict):
                    metadata = None
            except (OSError, ValueError, TypeError, KeyError, UnicodeError):
                pass
            failures.append((path, metadata, str(error)))
            continue
        if all(candidate.path != replay.path for candidate in candidates):
            candidates.append(replay)
    return tuple(candidates), tuple(failures)


def conversation_catalog(run_dir: Path, *, read_session: ResumeReadSession | None = None) -> tuple[ConversationMetadata, ...]:
    return _run_conversation_inventory(run_dir, read_session=read_session)[0]


def find_conversation_metadata(run_dir: Path, *, conversation_id: str | None = None,
                               read_session: ResumeReadSession | None = None) -> ConversationMetadata:
    """Select an existing conversation by identity, never by file modification time."""
    if conversation_id is not None:
        if not conversation_id or '/' in conversation_id or '\\' in conversation_id or conversation_id in {'.', '..'}:
            raise ValueError('Invalid conversation ID')
        path = Path(run_dir).resolve() / 'rollouts' / f'{conversation_id}.jsonl'
        if path.is_file():
            descriptor = (read_session or ResumeReadSession()).index(path).descriptor
            if descriptor.conversation_id != conversation_id:
                raise ValueError('Conversation ID does not match rollout metadata')
            return descriptor
    candidates = conversation_catalog(run_dir, read_session=read_session)
    if conversation_id is not None:
        candidates = tuple(item for item in candidates if item.conversation_id == conversation_id)
    if len(candidates) != 1:
        raise ValueError(f'Expected one conversation, found {len(candidates)}; pass conversation_id')
    return candidates[0]


def find_conversation(run_dir: Path, *, conversation_id: str | None = None,
                      read_session: ResumeReadSession | None = None) -> ResumedConversation:
    session = read_session or ResumeReadSession()
    selected = find_conversation_metadata(run_dir, conversation_id=conversation_id, read_session=session)
    return session.replay(selected.path, conversation_id=selected.conversation_id)


def find_run_conversation(run_dir: Path, *, read_session: ResumeReadSession | None = None,
                          metadata_only: bool = False) -> ResumedConversation | ConversationMetadata:
    """Select the actor history for a run from durable business scope."""
    session = read_session or ResumeReadSession()
    result = session.business_events(run_events_path(run_dir))
    events = result.events
    from ownpi.runtime.rollout.direct_resume import is_direct_session, core_conversation_id

    if is_direct_session(events):
        selected = find_conversation_metadata(
            run_dir, conversation_id=core_conversation_id(events), read_session=session,
        )
        return selected if metadata_only else session.replay(selected.path)
    finalized = [event.data.to_object() for event in events if event.kind == 'run_finalized']
    if finalized and finalized[-1].get('status') == 'completed':
        raise ValueError('Run is already completed')
    starts = [event.data.to_object() for event in events
              if event.kind == 'stage_attempt_started']
    latest = starts[-1] if starts else {}
    stage = latest.get('stage')
    attempt = latest.get('attempt')
    loop_index = 1
    for event in events:
        data = event.data.to_object()
        if (event.kind == 'stage_loop_advanced' and data.get('stage') == stage
                and data.get('attempt') == attempt):
            loop_index = data['loop_index']
    candidates, failures = _run_conversation_inventory(run_dir, read_session=session)
    scoped = [item for item in candidates
              if business_stage(str(item.metadata.get('stage') or '')) == stage
              and item.metadata.get('attempt') == attempt
              and item.metadata.get('stage_loop_index') == loop_index]

    role, critic_round = 'core', 0
    accepted_rounds: dict[str, int] = {}
    active_review = ''
    for event in events:
        data = event.data.to_object()
        if event.kind == 'critic_loop_transition' and data.get('action') == 'core_intent_accepted':
            if (data.get('stage'), data.get('attempt'), data.get('stage_loop_index')) == (stage, attempt, loop_index):
                accepted_rounds[event.event_id] = data['round']
                role, critic_round = 'core', data['round']
        elif event.kind == 'critic_loop_transition' and data.get('action') == 'review_started':
            if data.get('accepted_event_id') in accepted_rounds:
                active_review = event.event_id
                role, critic_round = 'reviewer', accepted_rounds[data['accepted_event_id']] + 1
        elif event.kind == 'feedback_submitted' and data.get('review_event_id') == active_review:
            role = 'core'

    primary = [item for item in scoped if item.metadata.get('actor_kind') == role
               and item.metadata.get('critic_round') == critic_round]
    fallback = (('reviewer', critic_round) if role == 'core' and active_review else
                ('core', critic_round - 1) if role == 'reviewer' else None)
    blocking_failures: list[tuple[Path, str]] = []
    fallback_failures: list[tuple[Path, str]] = []
    uncertain_failures: list[tuple[Path, str]] = []
    for path, metadata, reason in failures:
        if metadata is not None and isinstance(metadata.get('run_id'), str) and metadata['run_id'] and metadata['run_id'] != result.run_id:
            continue
        if (metadata is not None and stage is not None and (
                isinstance(metadata.get('stage'), str)
                and bool(metadata['stage']) and business_stage(metadata['stage']) != stage
                or type(metadata.get('attempt')) is int and metadata['attempt'] != attempt
                or type(metadata.get('stage_loop_index')) is int
                and metadata['stage_loop_index'] != loop_index)):
            continue
        if (metadata is not None and isinstance(metadata.get('actor_kind'), str)
                and type(metadata.get('critic_round')) is int and
                (metadata.get('actor_kind'), metadata.get('critic_round')) != (role, critic_round)
                and (primary or fallback is None or
                     (metadata.get('actor_kind'), metadata.get('critic_round')) != fallback)):
            continue
        scope = (metadata.get('actor_kind'), metadata.get('critic_round')) if metadata else None
        if scope == fallback:
            fallback_failures.append((path, reason))
        elif metadata is None:
            uncertain_failures.append((path, reason))
        else:
            blocking_failures.append((path, reason))
    if blocking_failures or (
            (fallback_failures or uncertain_failures) and not scoped
    ):
        failures = blocking_failures + fallback_failures + uncertain_failures
        raise ValueError(
            'Run actor rollout cannot be read: '
            + '; '.join(f'{path}: {reason}' for path, reason in failures)
        )
    if not candidates:
        raise NoActorConversationError('Run has no actor conversation to resume')
    if not scoped:
        raise NoActorConversationError('Current stage loop has no actor conversation yet')
    scoped = [item for item in scoped
              if item.metadata.get('actor_kind') == role
              and item.metadata.get('critic_round') == critic_round]
    if not scoped and role == 'core' and active_review:
        # Feedback can be committed before the next Core rollout is created.
        scoped = [item for item in candidates
                  if business_stage(str(item.metadata.get('stage') or '')) == stage
                  and item.metadata.get('attempt') == attempt
                  and item.metadata.get('stage_loop_index') == loop_index
                  and item.metadata.get('actor_kind') == 'reviewer'
                  and item.metadata.get('critic_round') == critic_round]
    if not scoped and role == 'reviewer':
        scoped = [item for item in candidates
                  if business_stage(str(item.metadata.get('stage') or '')) == stage
                  and item.metadata.get('attempt') == attempt
                  and item.metadata.get('stage_loop_index') == loop_index
                  and item.metadata.get('actor_kind') == 'core'
                  and item.metadata.get('critic_round') == critic_round - 1]
    if not scoped:
        raise NoActorConversationError('Current actor has no conversation yet')
    if len(scoped) != 1:
        raise ValueError('Run recovery point is ambiguous; durable actor scope is not unique')
    selected = scoped[0]
    return selected if metadata_only else session.replay(selected.path)


def find_child_conversation(
    run_dir: Path, *, child_id: str, conversation_id: str,
    parent_conversation_id: str,
) -> ResumedConversation:
    """Locate a child rollout by explicit identities, without consulting old records."""
    return read_agent_relationships(run_dir).read_child(
        child_id=child_id, conversation_id=conversation_id,
        parent_conversation_id=parent_conversation_id,
    )


@dataclass(frozen=True)
class RunResumeFacts:
    conversation: ResumedConversation | ConversationMetadata | None
    events: tuple[BusinessEvent, ...]
    business_issue: str | None
    business_parse_errors: int
    run_root: Path
    run_id: str
    agent_relationships: AgentRelationships | None = None
    read_session: ResumeReadSession = field(default_factory=ResumeReadSession, repr=False, compare=False)

    @cached_property
    def _stage_projection(self):
        from ownpi.runtime.rollout.stage_business_events import project_stage_business_events

        return project_stage_business_events(self.events)

    def stage_projection(self):
        return self._stage_projection

    @cached_property
    def _cursor(self):
        from ownpi.runtime.rollout.resume_cursor import project_resume_cursor

        return project_resume_cursor(self)

    def cursor(self):
        return self._cursor

    @cached_property
    def _execution_projection(self):
        from ownpi.runtime.rollout.execution_events import project_execution_events

        return project_execution_events(self.events)

    def execution_projection(self):
        return self._execution_projection

    @cached_property
    def _interaction_projection(self):
        from ownpi.runtime.rollout.interaction_events import project_actor_interactions

        return project_actor_interactions(self.events, self.conversation.metadata)

    def interaction_projection(self):
        return self._interaction_projection

    def admitted_child(self, child_id: str):
        """Return the durable task needed to rebuild a child actor, if known."""
        from ownpi.runtime.rollout.child_projection import project_children

        projection = project_children(self.events, run_root=self.run_root, run_id=self.run_id,
                                      parent_conversation_id=self.conversation.metadata.get('parent_id'),
                                      selected_child_id=child_id, read_session=self.read_session)
        return next((state for state in projection.states if state.child_id == child_id), None)

    def _canonical_paths(self) -> frozenset[Path]:
        root = self.run_root
        paths = {root / name for name in (
            'research_state.md', 'execution_board.md', 'task_spec.md', 'raw_user_message.txt',
        )}
        for event in self.events:
            data = event.data.to_object()
            if event.kind == 'plan_approved' and isinstance(data.get('plan_path'), str):
                paths.add((root / Path(data['plan_path']).expanduser()).resolve())
            elif event.kind == 'feedback_submitted' and isinstance(data.get('path'), str):
                paths.add((root / Path(data['path']).expanduser()).resolve())
        return frozenset(paths)

    def read_canonical_text(self, path: str | Path, *, expected_sha256: str | None = None) -> str:
        """Read a resource only when the pending business operation needs its body."""
        root = self.run_root
        candidate = Path(path).expanduser()
        target = (candidate if candidate.is_absolute() else root / candidate).resolve()
        if not target.is_relative_to(root) or target not in self._canonical_paths() or not target.is_file():
            raise ValueError(f'Canonical resource is outside this run or missing: {path}')
        body = target.read_text(encoding='utf-8')
        if expected_sha256 is not None and sha256(body.encode('utf-8')).hexdigest() != expected_sha256:
            raise ValueError(f'Canonical resource digest differs from recorded fact: {path}')
        return body

    def read_approved_plan(self, fact: dict[str, Any]) -> str:
        """Rebuild only a missing approved copy from its original planning history."""
        from ownpi.runtime.native_tools.plan_mode.parser import ProposedPlanParser
        from ownpi.utils.io import write_text_atomic

        path = self.run_root / fact['plan_path']
        target = path.resolve()
        digest = fact['content_sha256']
        if not target.is_relative_to(self.run_root) or target not in self._canonical_paths():
            raise ValueError('Approved plan is outside its run')
        if path.exists() or path.is_symlink():
            return self.read_canonical_text(path, expected_sha256=digest)
        parser = ProposedPlanParser()
        for conversation in conversation_catalog(self.run_root, read_session=self.read_session):
            meta = conversation.metadata
            if (meta.get('run_id'), meta.get('actor_kind'), meta.get('stage'), meta.get('attempt')) != (
                self.run_id, 'plan_mode', fact['stage'], fact['attempt'],
            ):
                continue
            records = self.read_session.original_records(conversation.path, variants=frozenset({'provider'}))
            for record in records:
                if record.metadata.to_object().get('kind') != ProviderOutputItemKind.MESSAGE.value:
                    continue
                item = decode_entry(record).item
                if not isinstance(item, ProviderOutputItem) or item.kind != ProviderOutputItemKind.MESSAGE:
                    continue
                parsed = parser.parse(item.text)
                if (not parsed.found or not parsed.plan_text or parsed.has_unclosed_block
                        or parsed.has_invalid_nested_block):
                    continue
                body = parsed.plan_text.strip() + '\n'
                if sha256(body.encode('utf-8')).hexdigest() == digest:
                    write_text_atomic(target, body)
                    return self.read_canonical_text(path, expected_sha256=digest)
        raise ValueError('Approved plan has no matching original proposal')


def _read_run_facts(run_dir: Path, conversation: ResumedConversation | ConversationMetadata | None,
                    *, run_id: str,
                    agent_relationships: AgentRelationships | None = None,
                    read_session: ResumeReadSession | None = None) -> RunResumeFacts:
    session = read_session or ResumeReadSession()
    relationships = agent_relationships or session.relationships(run_dir)
    path = run_events_path(run_dir)
    if not path.exists():
        return RunResumeFacts(conversation, (), 'run-events file is absent', 0,
                              Path(run_dir).resolve(), run_id, relationships, session)
    try:
        result = session.business_events(path, run_id=run_id or None)
    except (OSError, ValueError, TypeError, KeyError) as error:
        return RunResumeFacts(conversation, (), f'run-events cannot be projected: {error}', 0,
                              Path(run_dir).resolve(), run_id, relationships, session)
    return RunResumeFacts(conversation, result.events, None, result.parse_errors,
                          Path(run_dir).resolve(), result.run_id, relationships, session)


def read_run_resume(run_dir: Path, *, conversation_id: str | None = None,
                     read_session: ResumeReadSession | None = None) -> RunResumeFacts:
    session = read_session or ResumeReadSession()
    if conversation_id is not None:
        conversation = find_conversation_metadata(run_dir, conversation_id=conversation_id, read_session=session)
        return _read_run_facts(run_dir, conversation,
                               run_id=str(conversation.metadata.get('run_id') or ''), read_session=session)
    try:
        conversation = find_run_conversation(run_dir, read_session=session, metadata_only=True)
    except NoActorConversationError:
        # The run event log is sufficient before the first actor rollout exists.
        return _read_run_facts(run_dir, None, run_id='', read_session=session)
    return _read_run_facts(run_dir, conversation,
                           run_id=str(conversation.metadata.get('run_id') or ''), read_session=session)


def read_child_resume(run_dir: Path, *, child_id: str, conversation_id: str,
                      parent_conversation_id: str, read_session: ResumeReadSession | None = None) -> RunResumeFacts:
    session = read_session or ResumeReadSession()
    relationships = session.relationships(run_dir)
    child = relationships.read_child(
        child_id=child_id, conversation_id=conversation_id,
        parent_conversation_id=parent_conversation_id, read_session=session,
        metadata_only=True,
    )
    # The execution wrapper still needs parent business context; the A reader above does not.
    parent = find_conversation_metadata(run_dir, conversation_id=parent_conversation_id, read_session=session)
    return _read_run_facts(run_dir, child, run_id=str(parent.metadata.get('run_id') or ''),
                           agent_relationships=relationships, read_session=session)
