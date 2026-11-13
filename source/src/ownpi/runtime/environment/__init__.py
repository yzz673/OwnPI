from pathlib import Path

from ownpi.runtime.environment.contracts import (
    EnvironmentDescriptor,
    EnvironmentExecOutcome,
    EnvironmentExecRequest,
    EnvironmentExecutionReceipt,
    EnvironmentProviderKind,
    SandboxPolicy,
    normalize_environment_provider_kind,
)
from ownpi.runtime.environment.docker_sandbox import DockerSandboxEnvironmentProvider
from ownpi.runtime.environment.host_local import HostLocalEnvironmentProvider
from ownpi.runtime.environment.providers import (
    EnvironmentProvider,
    EnvironmentSessionLaunch,
    ResolvedEnvironmentSelection,
)
from ownpi.runtime.environment.preflight import EnvironmentPreflightDecision, preflight_environment_provider
from ownpi.runtime.environment.resolver import (
    EnvironmentResolver,
    build_provider_runtime_config,
    default_environment_config,
)
from ownpi.runtime.environment.ssh_remote import SSHRemoteEnvironmentProvider


__all__ = [
    'DockerSandboxEnvironmentProvider',
    'EnvironmentDescriptor',
    'EnvironmentExecOutcome',
    'EnvironmentExecRequest',
    'EnvironmentExecutionReceipt',
    'EnvironmentProvider',
    'EnvironmentProviderKind',
    'EnvironmentPreflightDecision',
    'EnvironmentResolver',
    'EnvironmentSessionLaunch',
    'HostLocalEnvironmentProvider',
    'ResolvedEnvironmentSelection',
    'SandboxPolicy',
    'SSHRemoteEnvironmentProvider',
    'build_environment_provider',
    'build_provider_runtime_config',
    'default_environment_config',
    'preflight_environment_provider',
    'normalize_environment_provider_kind',
]


def build_environment_provider(
    *,
    project_root: Path,
    selection: ResolvedEnvironmentSelection,
    config_snapshot: dict[str, object] | None,
) -> EnvironmentProvider:
    provider_kind = normalize_environment_provider_kind(selection.provider_kind)
    runtime_config = build_provider_runtime_config(selection=selection, config_snapshot=config_snapshot)
    if provider_kind == 'docker_sandbox':
        return DockerSandboxEnvironmentProvider(
            project_root=project_root,
            selection=selection,
            runtime_config=runtime_config,
        )
    if provider_kind == 'ssh_remote':
        return SSHRemoteEnvironmentProvider(
            project_root=project_root,
            selection=selection,
            runtime_config=runtime_config,
        )
    if provider_kind == 'host_local':
        return HostLocalEnvironmentProvider(
            project_root=project_root,
            selection=selection,
            runtime_config=runtime_config,
        )
    raise ValueError(f'unsupported environment provider: {provider_kind!r}')
