"""Business stage selection keeps conversation lanes intact."""

import re
from typing import Any


def business_stage(lane: str) -> str:
    return re.sub(r'__reviewer_round_\d+$', '', lane)


def reviewer_round(lane: str) -> int | None:
    match = re.search(r'__reviewer_round_(\d+)$', lane)
    return int(match[1]) if match else None


def interaction_matches(session: Any, metadata: dict[str, Any]) -> bool:
    lane = str(metadata.get('stage') or '')
    return (session.stage == business_stage(lane)
            and session.attempt == metadata.get('attempt')
            and session.actor_kind == metadata.get('actor_kind')
            and (not session.actor_id or session.actor_id == metadata.get('actor_id'))
            and (not session.conversation_lane or session.conversation_lane == lane))
