"""Read historical agent identities without loading or starting child sessions."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from ownpi.runtime.rollout.resume_read_session import ConversationMetadata, ResumeReadSession

if TYPE_CHECKING:
    from ownpi.runtime.rollout.resume import ResumedConversation


@dataclass(frozen=True)
class AgentRelationship:
    parent_id: str
    conversation_id: str
    actor_id: str
    path: Path | None
    sources: tuple[str, ...]
    issues: tuple[str, ...] = ()


@dataclass(frozen=True)
class AgentRelationships:
    """Known metadata is independent of active ownership and resource state."""

    run_root: Path
    relations: tuple[AgentRelationship, ...] = ()
    diagnostics: tuple[str, ...] = ()

    def read_child(
        self, *, child_id: str, conversation_id: str, parent_conversation_id: str,
        read_session: ResumeReadSession | None = None,
        metadata_only: bool = False,
    ) -> ResumedConversation | ConversationMetadata:
        from ownpi.runtime.rollout.resume import replay_conversation

        matches = [relation for relation in self.relations
                   if (relation.parent_id, relation.conversation_id, relation.actor_id)
                   == (parent_conversation_id, conversation_id, child_id)]
        if len(matches) != 1:
            raise ValueError('Child conversation identity differs from its parent binding')
        relation = matches[0]
        if relation.issues or relation.path is None:
            raise ValueError(f'Child conversation is unresolved: {", ".join(relation.issues)}')
        # Recheck the file at selection time, including after metadata installation.
        session = read_session or ResumeReadSession()
        child = session.index(relation.path).descriptor
        expected = {
            'actor_kind': 'subagent', 'actor_id': child_id,
            'run_id': child_id, 'parent_id': parent_conversation_id,
            'id': conversation_id,
        }
        for key, value in expected.items():
            if child.metadata.get(key) != value:
                raise ValueError(f'Resumed conversation {key} does not match current actor scope')
        return child if metadata_only else replay_conversation(
            relation.path, conversation_id=conversation_id, read_session=session,
        )


def read_agent_relationships(run_dir: Path, *, read_session: ResumeReadSession | None = None) -> AgentRelationships:
    """Project only original metadata/spawn records in this run's rollout layout.

    No graph status is inferred from task completion, turn completion or release.
    Aliases of one file are deduplicated; distinct files are never picked by mtime.
    """
    root = Path(run_dir).expanduser().resolve()
    session = read_session or ResumeReadSession()
    paths = sorted({*root.glob('rollouts/*.jsonl'),
                    *root.glob('subagents/*/rollouts/*.jsonl')})
    seen: set[Path] = set()
    metadata: dict[str, list[tuple[Path, dict]]] = defaultdict(list)
    claims: dict[tuple[str, str, str], set[str]] = defaultdict(set)
    diagnostics: list[str] = []
    for candidate in paths:
        if candidate.name == 'run-events.jsonl':
            continue
        try:
            path = candidate.resolve(strict=True)
            if path in seen or not path.is_file():
                continue
            seen.add(path)
            result = session.index(path)
        except (OSError, ValueError, UnicodeError, RuntimeError) as error:
            diagnostics.append(f'{candidate}: {error}')
            continue
        if result.descriptor.parse_errors:
            diagnostics.append(f'{path}: {result.descriptor.parse_errors} malformed record(s) skipped')
        identity = result.descriptor.conversation_id
        if not isinstance(identity, str) or not identity:
            diagnostics.append(f'{path}: invalid conversation identity')
            continue
        metas = result.metadata
        for meta in metas:
            metadata[identity].append((path, meta))
            parent, actor = meta.get('parent_id'), meta.get('actor_id')
            if meta.get('actor_kind') == 'subagent' and _identity(parent) and _identity(actor):
                claims[parent, identity, actor].add('session_meta')
        # Ambiguous source metadata cannot establish a reliable parent identity.
        if any(meta != metas[0] for meta in metas):
            diagnostics.append(f'{path}: conflicting session metadata')
            continue
        for value in result.spawns:
            child, actor = value.get('child_conversation_id'), value.get('child_actor_id')
            if not _identity(child) or not _identity(actor):
                diagnostics.append(f'{path}: malformed thread_spawned identity')
                continue
            claims[identity, child, actor].add('thread_spawned')

    bindings: dict[str, set[tuple[str, str]]] = defaultdict(set)
    for parent, child, actor in claims:
        bindings[child].add((parent, actor))
    parents = {child: next(iter(values))[0] for child, values in bindings.items()
               if len(values) == 1}
    cyclic = _cycle_members(parents)
    relations = []
    for (parent, child, actor), sources in sorted(claims.items()):
        issues = []
        candidates = metadata.get(child, [])
        locators = {path for path, _ in candidates}
        if len(bindings[child]) != 1:
            issues.append('conflicting parent/actor identity')
        if not locators:
            issues.append('child rollout missing or unreadable')
        elif len(locators) > 1:
            issues.append('multiple child rollout candidates')
        if any(meta.get('id') != child or meta.get('actor_kind') != 'subagent'
               or meta.get('actor_id') != actor or meta.get('run_id') != actor
               or meta.get('parent_id') != parent for _, meta in candidates):
            issues.append('child metadata conflicts with parent binding')
        if child in cyclic:
            issues.append('cyclic parent relationship')
        relation = AgentRelationship(
            parent, child, actor, next(iter(locators)) if len(locators) == 1 else None,
            tuple(sorted(sources)), tuple(issues),
        )
        relations.append(relation)
    return AgentRelationships(root, tuple(relations), tuple(diagnostics))


def _identity(value: object) -> bool:
    return isinstance(value, str) and bool(value)


def _cycle_members(parents: dict[str, str]) -> set[str]:
    """Walk each identity once, without recursion or traversing the filesystem."""
    done: set[str] = set()
    cyclic: set[str] = set()
    for start in parents:
        chain: dict[str, int] = {}
        node = start
        while node in parents and node not in done and node not in chain:
            chain[node] = len(chain)
            node = parents[node]
        if node in chain:
            cyclic.update(list(chain)[chain[node]:])
        done.update(chain)
    return cyclic
