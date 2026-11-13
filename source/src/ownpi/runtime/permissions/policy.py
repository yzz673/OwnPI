from __future__ import annotations

from dataclasses import asdict, dataclass
import fnmatch
import hashlib
import json
import os
from pathlib import Path
from typing import Iterable

from ownpi.runtime.permissions.models import (
    AdditionalPermissionProfile,
    EffectivePermissions,
    FileSystemAccessMode,
    FileSystemPathKind,
    FileSystemSandboxEntry,
    FileSystemSandboxKind,
    FileSystemSandboxPolicy,
    NetworkSandboxPolicy,
    PermissionProfile,
)


_ACCESS_PRECEDENCE = {
    FileSystemAccessMode.READ: 1,
    FileSystemAccessMode.WRITE: 2,
    FileSystemAccessMode.DENY: 3,
}


@dataclass(frozen=True)
class PolicyDecision:
    allowed: bool
    requested_access: FileSystemAccessMode
    effective_access: FileSystemAccessMode | None
    matched_entry: FileSystemSandboxEntry | None
    normalized_path: Path
    reason: str


class FileSystemPolicyMatcher:
    def __init__(self, policy: FileSystemSandboxPolicy) -> None:
        self._policy = policy
        _validate_policy(policy)

    def decide(
        self,
        path: str | Path,
        *,
        requested_access: FileSystemAccessMode,
        cwd: Path | None = None,
    ) -> PolicyDecision:
        normalized = lexical_absolute_path(path, cwd=cwd)
        if self._policy.kind is FileSystemSandboxKind.UNRESTRICTED:
            return PolicyDecision(
                allowed=True,
                requested_access=requested_access,
                effective_access=FileSystemAccessMode.WRITE,
                matched_entry=None,
                normalized_path=normalized,
                reason='filesystem policy is unrestricted',
            )
        if self._policy.kind is FileSystemSandboxKind.EXTERNAL:
            return PolicyDecision(
                allowed=False,
                requested_access=requested_access,
                effective_access=None,
                matched_entry=None,
                normalized_path=normalized,
                reason='filesystem access is controlled by an external sandbox',
            )
        candidates = normalized_and_canonical_candidates(normalized)
        candidate_matches = tuple(
            max(
                (
                    entry
                    for entry in self._policy.entries
                    if _entry_matches(entry, (candidate,))
                ),
                key=_entry_precedence,
                default=None,
            )
            for candidate in candidates
        )
        allowed_by_candidate = tuple(
            _access_allows(
                matched.access if matched is not None else None,
                requested_access=requested_access,
            )
            for matched in candidate_matches
        )
        allowed = bool(allowed_by_candidate) and all(allowed_by_candidate)
        failing_index = next(
            (index for index, candidate_allowed in enumerate(allowed_by_candidate) if not candidate_allowed),
            None,
        )
        matched = (
            candidate_matches[failing_index]
            if failing_index is not None
            else max(
                (item for item in candidate_matches if item is not None),
                key=_entry_precedence,
                default=None,
            )
        )
        effective = matched.access if matched is not None else None
        return PolicyDecision(
            allowed=allowed,
            requested_access=requested_access,
            effective_access=effective,
            matched_entry=matched,
            normalized_path=normalized,
            reason=(
                f'access resolved to {effective.value}'
                if effective is not None
                else 'no filesystem policy entry matched'
            ),
        )

    def can_read(self, path: str | Path, *, cwd: Path | None = None) -> bool:
        return self.decide(path, requested_access=FileSystemAccessMode.READ, cwd=cwd).allowed

    def can_write(self, path: str | Path, *, cwd: Path | None = None) -> bool:
        return self.decide(path, requested_access=FileSystemAccessMode.WRITE, cwd=cwd).allowed

    def has_explicit_deny(self, path: str | Path, *, cwd: Path | None = None) -> bool:
        """Return whether a concrete deny entry matches this path."""
        normalized = lexical_absolute_path(path, cwd=cwd)
        candidates = normalized_and_canonical_candidates(normalized)
        return any(
            entry.access is FileSystemAccessMode.DENY
            and _entry_matches(entry, (candidate,))
            for entry in self._policy.entries
            for candidate in candidates
        )


def require_file_access(
    permissions: EffectivePermissions,
    path: str | Path,
    access: FileSystemAccessMode,
) -> None:
    """Check host-side I/O against the same policy used by the tool gate."""
    decision = FileSystemPolicyMatcher(permissions.profile.file_system).decide(
        path, requested_access=access,
    )
    if not decision.allowed:
        raise PermissionError(
            f'filesystem {access.value} denied: {decision.normalized_path}; {decision.reason}'
        )


def lexical_absolute_path(path: str | Path, *, cwd: Path | None = None) -> Path:
    expanded = os.path.expanduser(str(path))
    if not os.path.isabs(expanded):
        expanded = os.path.join(str(cwd or Path.cwd()), expanded)
    return Path(os.path.abspath(os.path.normpath(expanded)))


def _access_allows(
    effective: FileSystemAccessMode | None,
    *,
    requested_access: FileSystemAccessMode,
) -> bool:
    return bool(
        effective is not None
        and effective.can_read
        and (requested_access is FileSystemAccessMode.READ or effective.can_write)
    )


def normalized_and_canonical_candidates(path: str | Path) -> tuple[Path, ...]:
    lexical = lexical_absolute_path(path)
    canonical = _canonical_candidate(lexical)
    return (lexical,) if canonical == lexical else (lexical, canonical)


def merge_additional_permissions(
    profile: PermissionProfile,
    overlay: AdditionalPermissionProfile | None,
) -> PermissionProfile:
    if overlay is None or overlay.is_empty:
        return profile
    if profile.file_system.kind is FileSystemSandboxKind.RESTRICTED:
        base_matcher = FileSystemPolicyMatcher(profile.file_system)
        for entry in overlay.file_system_entries:
            if entry.path.kind is not FileSystemPathKind.PATH:
                raise ValueError('additional filesystem permissions require absolute paths')
            base_decision = base_matcher.decide(
                entry.path.value,
                requested_access=entry.access,
            )
            if base_decision.effective_access is FileSystemAccessMode.DENY:
                raise ValueError(
                    'additional permissions cannot override an explicit filesystem deny: '
                    f'{entry.path.value}'
                )
        entries = (*profile.file_system.entries, *overlay.file_system_entries)
        file_system = FileSystemSandboxPolicy(
            kind=FileSystemSandboxKind.RESTRICTED,
            entries=entries,
            glob_scan_max_depth=profile.file_system.glob_scan_max_depth,
        )
    else:
        file_system = profile.file_system
    network = (
        NetworkSandboxPolicy.ENABLED
        if overlay.network_enabled is True
        else profile.network
    )
    return PermissionProfile(
        profile_id=profile.profile_id,
        enforcement=profile.enforcement,
        file_system=file_system,
        network=network,
        description=profile.description,
        extends=profile.extends,
    )


def permission_policy_hash(
    profile: PermissionProfile,
    *,
    approval_policy: object | None = None,
    approvals_reviewer: object | None = None,
    granular_approval: object | None = None,
) -> str:
    payload = json.dumps(
        {
            'profile': asdict(profile),
            'approval_policy': getattr(approval_policy, 'value', approval_policy),
            'approvals_reviewer': getattr(approvals_reviewer, 'value', approvals_reviewer),
            'granular_approval': asdict(granular_approval)
            if hasattr(granular_approval, '__dataclass_fields__')
            else granular_approval,
        },
        sort_keys=True,
        separators=(',', ':'),
        default=str,
    )
    return hashlib.sha256(payload.encode('utf-8')).hexdigest()


def _entry_matches(
    entry: FileSystemSandboxEntry,
    candidates: tuple[Path, ...],
) -> bool:
    if entry.path.kind is FileSystemPathKind.SPECIAL:
        raise ValueError('filesystem policy must be materialized before matching')
    if entry.path.kind is FileSystemPathKind.GLOB:
        pattern = entry.path.value
        return any(fnmatch.fnmatchcase(candidate.as_posix(), pattern) for candidate in candidates)
    entry_candidates = normalized_and_canonical_candidates(entry.path.value)
    return any(
        _is_same_or_descendant(candidate, entry_candidate)
        for candidate in candidates
        for entry_candidate in entry_candidates
    )


def _entry_precedence(entry: FileSystemSandboxEntry) -> tuple[int, int]:
    if entry.path.kind is FileSystemPathKind.GLOB:
        specificity = len(Path(entry.path.value).parts)
    else:
        specificity = len(lexical_absolute_path(entry.path.value).parts)
    return specificity, _ACCESS_PRECEDENCE[entry.access]


def _canonical_candidate(path: Path) -> Path:
    if path.exists() or path.is_symlink():
        try:
            return path.resolve(strict=True)
        except OSError:
            return path
    missing: list[str] = []
    ancestor = path
    while not ancestor.exists() and ancestor.parent != ancestor:
        missing.append(ancestor.name)
        ancestor = ancestor.parent
    try:
        canonical = ancestor.resolve(strict=True)
    except OSError:
        return path
    for part in reversed(missing):
        canonical /= part
    return canonical


def _is_same_or_descendant(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _validate_policy(policy: FileSystemSandboxPolicy) -> None:
    for entry in policy.entries:
        if entry.path.kind is FileSystemPathKind.GLOB:
            if entry.access is not FileSystemAccessMode.DENY:
                raise ValueError('glob filesystem entries may only deny access')
            _validate_glob(entry.path.value)
        if entry.path.kind is FileSystemPathKind.PATH and not os.path.isabs(
            os.path.expanduser(entry.path.value)
        ):
            raise ValueError(f'filesystem path must be absolute: {entry.path.value!r}')


def _validate_glob(pattern: str) -> None:
    if not pattern:
        raise ValueError('filesystem deny glob must not be empty')
    depth = 0
    for char in pattern:
        if char == '[':
            depth += 1
        elif char == ']':
            depth -= 1
            if depth < 0:
                raise ValueError(f'invalid filesystem deny glob: {pattern!r}')
    if depth:
        raise ValueError(f'invalid filesystem deny glob: {pattern!r}')


def dedupe_entries(entries: Iterable[FileSystemSandboxEntry]) -> tuple[FileSystemSandboxEntry, ...]:
    values: list[FileSystemSandboxEntry] = []
    for entry in entries:
        if entry not in values:
            values.append(entry)
    return tuple(values)
