from __future__ import annotations

from dataclasses import dataclass
import logging
from pathlib import Path
from typing import Any

from ownpi.controller.pending_interactions import (
    pending_human_interactions,
    pending_permission_interactions,
)


@dataclass(frozen=True)
class InteractionRoute:
    run_dir: Path
    interaction_id: str
    kind: str
    origin_kind: str
    interaction_request_id: str
    resume_target: str
    waiting_for: str
    renderer_key: str
    pending: dict[str, Any]


class InteractionRouter:
    def list_pending_interactions(self, run_dir: Path) -> list[InteractionRoute]:
        run_dir = run_dir.expanduser().resolve()
        candidates: list[InteractionRoute] = []
        errors: list[Exception] = []
        for read, build in (
            (pending_human_interactions, self._build_human_route),
            (pending_permission_interactions, self._build_permission_route),
        ):
            try:
                candidates.extend(build(run_dir, pending) for pending in read(run_dir))
            except (OSError, ValueError, TypeError, KeyError) as error:
                logging.getLogger(__name__).warning(
                    'Cannot read %s for %s: %s', read.__name__, run_dir, error,
                )
                errors.append(error)
        if not candidates and errors:
            raise errors[0]
        candidates.sort(key=self._route_sort_key)
        return candidates

    def route_pending_interaction(self, run_dir: Path) -> InteractionRoute | None:
        routes = self.list_pending_interactions(run_dir)
        return routes[0] if routes else None

    @staticmethod
    def _human_kind(pending: dict[str, Any]) -> str:
        return 'human_question' if str(pending.get('origin_kind', 'controller') or 'controller') == 'tool' else 'human_clarification'

    @staticmethod
    def _resume_target(pending: dict[str, Any]) -> str:
        display_target = str(pending.get('display_target', '') or '').strip()
        if display_target:
            return display_target
        if str(pending.get('continuation_kind', '') or '') == 'direct_runtime':
            return 'direct_runtime'
        stage = str(pending.get('stage', '<unknown>') or '<unknown>')
        attempt = int(pending.get('attempt', 0) or 0)
        continuation_index = int(pending.get('continuation_index', 0) or 0)
        return f'{stage} a{attempt} continuation={continuation_index}'

    def _build_human_route(self, run_dir: Path, pending: dict[str, Any]) -> InteractionRoute:
        session_id = str(pending.get('session_id', '') or '')
        interaction_request_id = str(
            pending.get('interaction_request_id') or session_id or ''
        ).strip()
        return InteractionRoute(
            run_dir=run_dir,
            interaction_id=session_id,
            kind=self._human_kind(pending),
            origin_kind=str(pending.get('origin_kind', 'controller') or 'controller'),
            interaction_request_id=interaction_request_id,
            resume_target=self._resume_target(pending),
            waiting_for=str(pending.get('waiting_for', 'answer questions') or 'answer questions'),
            renderer_key='human_interaction',
            pending=dict(pending),
        )

    def _build_permission_route(self, run_dir: Path, pending: dict[str, Any]) -> InteractionRoute:
        session_id = str(pending.get('session_id', '') or '')
        interaction_request_id = str(
            pending.get('interaction_request_id') or pending.get('request_id') or session_id or ''
        ).strip()
        tool_name = str(pending.get('tool_name', '<unknown>') or '<unknown>')
        return InteractionRoute(
            run_dir=run_dir,
            interaction_id=session_id,
            kind='permission_request',
            origin_kind=str(pending.get('origin_kind', 'runtime') or 'runtime'),
            interaction_request_id=interaction_request_id,
            resume_target=self._resume_target(pending),
            waiting_for=str(pending.get('waiting_for') or f'approve {tool_name}'),
            renderer_key='permission_request',
            pending=dict(pending),
        )

    def _route_sort_key(
        self,
        route: InteractionRoute,
    ) -> tuple[int, str, int, str]:
        created_at = str(route.pending.get('created_at', '') or '').strip()
        missing_created = 1 if not created_at else 0
        fallback_kind_priority = 0 if route.kind in {'human_question', 'human_clarification'} else 1
        return (
            missing_created,
            created_at,
            fallback_kind_priority,
            route.interaction_id,
        )
