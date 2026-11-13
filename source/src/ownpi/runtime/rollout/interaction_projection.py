"""Read answer delivery evidence independently of interaction lifecycle flags."""

from datetime import datetime
from pathlib import Path

from ownpi.runtime.native_tools.history_items import HistoryMessage
from ownpi.runtime.rollout.codec import decode_entry
from ownpi.runtime.rollout.source_reader import OriginalCall, read_original_call
from ownpi.schemas.runtime_human_clarification import HumanClarificationSession
from ownpi.runtime.rollout.resume_read_session import ResumeReadSession


def interaction_call(path: Path, session: HumanClarificationSession, *, read_session: ResumeReadSession | None = None) -> OriginalCall | None:
    """Locate the request occurrence, not another call reusing its wire ID."""
    reader = read_session or ResumeReadSession()
    matches = []
    for location in reader.index(path).wire_calls.get(session.tool_request_id, ()):
        if (location.type != 'response_item' or location.variant != 'provider'
                or location.kind != 'tool_call' or location.tool_name != 'human_question'):
            continue
        if session.tool_request_id not in {location.call_id, location.request_id}:
            continue
        if session.created_at and datetime.fromisoformat(location.timestamp) > datetime.fromisoformat(session.created_at):
            continue
        matches.append(location)
    if not matches:
        return None
    if not session.created_at and len(matches) != 1:
        raise ValueError('Clarification has multiple original call occurrences')
    source = {'path': path.name, 'item_id': matches[-1].item_id}
    return read_original_call(path.parent, source, tool_name='human_question', read_session=reader)


def answer_is_delivered(path: Path, session: HumanClarificationSession, *, read_session: ResumeReadSession | None = None) -> bool:
    if session.origin_kind == 'tool':
        call = interaction_call(path, session, read_session=read_session)
        return call is not None and any(output.success is True for output in call.outputs)
    records = (read_session or ResumeReadSession()).original_records(path, variants=frozenset({'message'}))
    for record in records:
        item = decode_entry(record).item
        if session.origin_kind == 'controller':
            if (isinstance(item, HistoryMessage)
                    and item.metadata.to_object().get('controller_clarification_session') == session.session_id):
                return True
    return False
