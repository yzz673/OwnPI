from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping

from ownpi.runtime.permissions.models import EffectivePermissions


@dataclass(frozen=True)
class SandboxBackendCapability:
    backend_kind: str
    available: bool
    filesystem_enforced: bool
    network_enforced: bool
    reason: str = ''


@dataclass(frozen=True)
class ExternalSandboxCapability:
    provider_kind: str
    policy_hash: str
    filesystem_enforced: bool
    network_enforced: bool
    protocol_version: int = 1


@dataclass(frozen=True)
class SandboxCommandRequest:
    argv: tuple[str, ...]
    cwd: Path
    env: Mapping[str, str]
    effective_permissions: EffectivePermissions
    timeout_seconds: float = 120.0
    stdin_text: str | None = None
    external_capability: ExternalSandboxCapability | None = None

    def __post_init__(self) -> None:
        if not self.argv:
            raise ValueError('sandbox command argv must not be empty')
        if not self.cwd.is_absolute():
            raise ValueError('sandbox command cwd must be absolute')
        if self.timeout_seconds <= 0:
            raise ValueError('sandbox command timeout must be positive')


@dataclass(frozen=True)
class SandboxWrappedCommand:
    argv: tuple[str, ...]
    sandbox_backend: str


@dataclass(frozen=True)
class SandboxExecutionResult:
    backend_kind: str
    policy_hash: str
    exit_code: int
    stdout: str
    stderr: str
    filesystem_enforced: bool
    network_enforced: bool
    timed_out: bool = False
    metadata: Mapping[str, str] = field(default_factory=dict)
