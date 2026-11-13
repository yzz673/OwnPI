"""Read pending requests from their current owners without resuming a run."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from ownpi.runtime.interactions.live_approval import pending_live_approvals
from ownpi.runtime.rollout.business_reader import read_business_events
from ownpi.runtime.rollout.interaction_events import project_interactions
from ownpi.runtime.rollout.run_events import run_events_path


def pending_human_interactions(run_dir: Path) -> list[dict[str, Any]]:
    try:
        result = read_business_events(run_events_path(run_dir))
    except FileNotFoundError:
        return []
    if result.parse_errors:
        logging.getLogger(__name__).warning(
            'Pending questions for %s: skipped %s unreadable rollout records',
            run_dir, result.parse_errors,
        )
    sessions = [
        item.session.to_dict() for item in project_interactions(result.events).values()
        if item.answer_event_id is None and item.closed_event_id is None
        and item.session.status == 'pending'
    ]
    return sorted(sessions, key=lambda item: (
        not bool(item.get('created_at')), item.get('created_at') or '', item['session_id'],
    ))


def pending_permission_interactions(run_dir: Path) -> list[dict[str, Any]]:
    return [session.to_dict() for _, session in pending_live_approvals(run_dir)]
