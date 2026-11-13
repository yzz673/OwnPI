from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from ownpi.runtime.environment.contracts import (
    EnvironmentDescriptor,
    EnvironmentExecRequest,
    SandboxPolicy,
)


@dataclass(frozen=True)
class ResolvedEnvironmentSelection:
    provider_kind: str
    descriptor: EnvironmentDescriptor
    sandbox_policy: SandboxPolicy
    selection_reason: str
    environment_affinity: str
    execution_class: str
    backend_kind: str
    target_name: str | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            'provider_kind': self.provider_kind,
            'descriptor': self.descriptor.to_dict(),
            'sandbox_policy': self.sandbox_policy.to_dict(),
            'selection_reason': self.selection_reason,
            'environment_affinity': self.environment_affinity,
            'execution_class': self.execution_class,
            'backend_kind': self.backend_kind,
            'target_name': self.target_name,
        }


@dataclass(frozen=True)
class EnvironmentSessionLaunch:
    argv: tuple[str, ...]
    cwd: str
    env: dict[str, str]
    tty: bool
    pipe_stdin: bool
    pty_columns: int | None = None
    pty_rows: int | None = None
    sandbox_backend: str = 'unknown'


class EnvironmentProvider(Protocol):
    kind: str

    def prepare(self, *, descriptor: EnvironmentDescriptor, sandbox_policy: SandboxPolicy) -> None: ...

    def prepare_session_launch(
        self,
        *,
        request: EnvironmentExecRequest,
    ) -> EnvironmentSessionLaunch: ...

    def execution_metadata(
        self,
        *,
        request: EnvironmentExecRequest,
        stderr_text: str = '',
    ) -> dict[str, object]: ...


class EnvironmentProviderFactory(Protocol):
    def __call__(
        self,
        *,
        project_root: Path,
        selection: ResolvedEnvironmentSelection,
        runtime_config: dict[str, object] | None,
    ) -> EnvironmentProvider: ...
