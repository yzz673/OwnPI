from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable

from ownpi.runtime.events import now_utc_iso
from ownpi.runtime.rollout.business_reader import read_business_events
from ownpi.runtime.rollout.interaction_events import accept_answer, project_interactions
from ownpi.runtime.rollout.run_events import RunEventStore, run_events_path
from ownpi.schemas.runtime_human_clarification import HumanClarificationSession

if TYPE_CHECKING:
    from ownpi.app.paths import AppPaths
    from ownpi.schemas.artifacts import FinalPackage
    from ownpi.schemas.runtime import UserConfig


@dataclass(frozen=True)
class ClarificationAnswerReceipt:
    """Acceptance is confirmed even if releasing the writer needs a retry."""

    session: HumanClarificationSession
    answer_event_id: str
    writer_closed: bool
    retry_store: RunEventStore | None = field(default=None, repr=False, compare=False)


class AcceptedAnswerPresentationError(RuntimeError):
    """The answer is durable; only presentation or writer close failed."""

    accepted = True

    def __init__(self, receipt: ClarificationAnswerReceipt, cause: Exception) -> None:
        self.receipt = receipt
        self.presentation_error = cause
        super().__init__(
            f'Answer durably accepted ({receipt.answer_event_id}); '
            f'presentation unavailable: {type(cause).__name__}. '
            'Do not resubmit the answer as unaccepted.'
        )


def accept_stopped_interaction_answer(
    run_dir: Path, *, answers: dict[str, Any], session_id: str | None = None,
) -> ClarificationAnswerReceipt:
    """Accept an answer using only run events, without a workflow/CLI adapter."""
    run_dir = run_dir.expanduser().resolve()
    header = read_business_events(run_events_path(run_dir))
    if header.parse_errors:
        raise ValueError('Cannot accept an answer from an incomplete run event stream')
    store = RunEventStore(run_dir, header.run_id)
    try:
        if store.parse_errors:
            raise ValueError('Cannot accept an answer from an incomplete run event stream')
        requests = project_interactions(store.events)
        if session_id is None:
            candidates = [item for item in requests.values() if item.closed_event_id is None]
            pending = [item for item in candidates if item.answer_event_id is None]
            candidates = pending or candidates
            if len(candidates) != 1:
                raise ValueError('Specify session_id to identify one clarification request')
            session_id = candidates[0].session.session_id
        session = accept_answer(
            store, session_id=session_id, answers=answers, answered_at=now_utc_iso(),
        )
    except BaseException as error:
        if not store.close():
            # Retain the owner so callers can retry releasing its exclusive lock.
            error.run_event_store = store
        raise
    closed = store.close()
    return ClarificationAnswerReceipt(
        session=session, answer_event_id=f'interaction_answer_accepted:{session_id}',
        writer_closed=closed, retry_store=None if closed else store,
    )


class InteractionAnswerService:
    """Coordinates paused-run answers without resuming the Runtime."""

    accept_stopped_interaction_answer = staticmethod(accept_stopped_interaction_answer)

    def __init__(
        self,
        *,
        user_config: UserConfig,
        project_root: Path,
        app_paths: AppPaths | None,
        build_paused_interaction_result: Callable[..., FinalPackage],
    ) -> None:
        self._user_config = user_config
        self._project_root = project_root
        self._app_paths = app_paths
        self._build_paused_interaction_result = build_paused_interaction_result

    def answer_pending_interaction(
        self,
        run_dir: Path,
        *,
        answers: dict[str, Any],
        session_id: str | None = None,
    ) -> FinalPackage:
        run_dir = run_dir.expanduser().resolve()
        receipt = accept_stopped_interaction_answer(run_dir, answers=answers, session_id=session_id)
        if not receipt.writer_closed:
            raise AcceptedAnswerPresentationError(receipt, OSError('Run writer close unconfirmed'))
        from ownpi.runtime.rollout.resume_read_session import ResumeReadSession

        reader = ResumeReadSession()
        try:
            from ownpi.runtime.rollout.business_scope import interaction_matches
            from ownpi.runtime.rollout.resume import list_conversations, read_run_resume, read_child_resume
            from ownpi.runtime.rollout.resume_state import restore_workflow_state, restore_child_parent_state
            from ownpi.runtime.rollout.resume_entry import resolve_stage_resume_entry
            from ownpi.runtime.rollout.interaction_projection import interaction_call

            candidates = [item for item in list_conversations(run_dir, read_session=reader)
                          if interaction_matches(receipt.session, item.metadata)]
            child = receipt.session.actor_kind == 'subagent'
            if child:
                relationships = reader.relationships(run_dir)
                for relation in relationships.relations:
                    if relation.actor_id != receipt.session.actor_id:
                        continue
                    selected = relationships.read_child(child_id=relation.actor_id,
                        conversation_id=relation.conversation_id, parent_conversation_id=relation.parent_id,
                        read_session=reader, metadata_only=True)
                    if interaction_matches(receipt.session, selected.metadata):
                        candidates.append(selected)
            if receipt.session.origin_kind == 'tool':
                candidates = [item for item in candidates if interaction_call(item.path, receipt.session, read_session=reader) is not None]
            if len(candidates) != 1:
                raise ValueError('Answer presentation requires one matching actor conversation')
            selected = candidates[0]
            facts = (read_child_resume(run_dir, child_id=receipt.session.actor_id,
                     conversation_id=selected.conversation_id,
                     parent_conversation_id=selected.metadata['parent_id'], read_session=reader) if child
                     else read_run_resume(run_dir, conversation_id=selected.conversation_id, read_session=reader))
            if receipt.session.actor_kind == 'plan_mode':
                from dataclasses import replace

                facts = replace(facts, conversation=None)
            restore = restore_child_parent_state if child else restore_workflow_state
            from ownpi.runtime.rollout.direct_resume import is_direct_session, resolve_direct_resume

            entry = (resolve_direct_resume(facts).entry if not child and is_direct_session(facts.events)
                     else facts if child else resolve_stage_resume_entry(facts))
            state_store = restore(
                entry,
                project_root=self._project_root, user_config=self._user_config,
                app_paths=self._app_paths,
            )
            state_store.state.workflow_state.pending_human_clarification = receipt.session
            state_store.state.workflow_state.pause_reason = 'interaction_answered'
            return self._build_paused_interaction_result(
                state_store=state_store, run_dir=run_dir, saved_files=[],
                final_decision='interaction_answered',
            )
        except Exception as error:
            raise AcceptedAnswerPresentationError(receipt, error) from error
        finally:
            reader.clear()
