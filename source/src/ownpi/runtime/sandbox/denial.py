from __future__ import annotations

import signal
from dataclasses import dataclass, field

from ownpi.runtime.limits.projection import MAX_SANDBOX_DENIAL_EXCERPT_CHARS

SANDBOX_DENIAL_MARKERS = (
    'operation not permitted',
    'permission denied',
    'read-only file system',
)
_MARKER_OVERLAP = max(map(len, SANDBOX_DENIAL_MARKERS)) - 1


@dataclass
class DenialOutputEvidence:
    """Bounded stream evidence, independent of the consumer's replay cursor."""

    matched_marker: str = ''
    output_excerpt: str = ''
    stream_tails: dict[str, str] = field(default_factory=dict)

    def observe(self, stream: str, text: str) -> None:
        if self.matched_marker:
            return
        combined = self.stream_tails.get(stream, '') + text
        normalized = combined.casefold()
        for marker in SANDBOX_DENIAL_MARKERS:
            offset = normalized.find(marker)
            if offset >= 0:
                self.matched_marker = marker
                self.output_excerpt = combined[max(0, offset - 128):][:MAX_SANDBOX_DENIAL_EXCERPT_CHARS]
                self.stream_tails.clear()
                return
        self.stream_tails[stream] = combined[-_MARKER_OVERLAP:]

    def to_dict(self) -> dict[str, str]:
        return {
            'matched_marker': self.matched_marker,
            'output_excerpt': self.output_excerpt,
        }


def is_sandbox_denied(
    *,
    sandbox_backend: str,
    closed: bool,
    exit_code: int | None,
    matched_marker: str = '',
    executor_reported_denial: bool = False,
    timed_out: bool = False,
    interrupted: bool = False,
) -> bool:
    """Classify execution evidence, never an agent/run terminal condition."""
    if not closed or timed_out or interrupted:
        return False
    if executor_reported_denial:
        return True
    if sandbox_backend in {'', 'unknown', 'direct'} or exit_code in {None, 0}:
        return False
    if matched_marker in SANDBOX_DENIAL_MARKERS:
        return True
    if exit_code in {2, 126, 127}:
        return False
    # Only an actual seccomp backend can attribute SIGSYS to its enforcement.
    sigsys = getattr(signal, 'SIGSYS', None)
    return bool(
        sandbox_backend == 'linux_seccomp'
        and sigsys is not None
        and exit_code in {-sigsys, 128 + sigsys}
    )
