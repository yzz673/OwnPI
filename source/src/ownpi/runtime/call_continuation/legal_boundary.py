from __future__ import annotations

from dataclasses import dataclass
from typing import Any


LEGAL_TERMINAL_BOUNDARY_KINDS = frozenset(
    {
        'missing_user_owned_input',
        'permission_or_external_service_unavailable_after_recovery',
    }
)


@dataclass(frozen=True)
class LegalTerminalBoundary:
    exists: bool
    boundary_kind: str = ''
    source: str = ''
    evidence_refs: tuple[str, ...] = ()
    rationale: str = ''
    stage: str = ''
    attempt: int = 0
    created_at: str = ''

    def to_dict(self) -> dict[str, Any]:
        return {
            'exists': self.exists,
            'boundary_kind': self.boundary_kind,
            'source': self.source,
            'evidence_refs': list(self.evidence_refs),
            'rationale': self.rationale,
            'stage': self.stage,
            'attempt': self.attempt,
            'created_at': self.created_at,
        }


def boundary_from_missing_user_input(
    *,
    evidence_refs: tuple[str, ...] = (),
    rationale: str = 'required user-owned input is missing',
    stage: str = '',
    attempt: int = 0,
) -> LegalTerminalBoundary:
    if not evidence_refs and not rationale:
        return LegalTerminalBoundary(exists=False, stage=stage, attempt=attempt)
    return LegalTerminalBoundary(
        exists=True,
        boundary_kind='missing_user_owned_input',
        source='runtime',
        evidence_refs=evidence_refs,
        rationale=rationale,
        stage=stage,
        attempt=attempt,
    )


def boundary_from_permission_or_external_unavailable(
    *,
    evidence_refs: tuple[str, ...] = (),
    rationale: str = 'permission or external service unavailable after recovery attempts',
    stage: str = '',
    attempt: int = 0,
) -> LegalTerminalBoundary:
    if not evidence_refs and not rationale:
        return LegalTerminalBoundary(exists=False, stage=stage, attempt=attempt)
    return LegalTerminalBoundary(
        exists=True,
        boundary_kind='permission_or_external_service_unavailable_after_recovery',
        source='runtime',
        evidence_refs=evidence_refs,
        rationale=rationale,
        stage=stage,
        attempt=attempt,
    )


def has_runtime_legal_terminal_boundary(
    *,
    boundary: LegalTerminalBoundary | dict[str, Any] | None = None,
) -> bool:
    if isinstance(boundary, LegalTerminalBoundary):
        return boundary.exists and boundary.boundary_kind in LEGAL_TERMINAL_BOUNDARY_KINDS
    if isinstance(boundary, dict):
        return bool(boundary.get('exists')) and str(boundary.get('boundary_kind') or '') in LEGAL_TERMINAL_BOUNDARY_KINDS
    return False
