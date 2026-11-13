from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any


LIGHT_ARTIFACT_DIRNAME = 'light'
HEAVY_ARTIFACT_DIRNAME = 'heavy'
LOG_DIRNAME = 'logs'
STATUS_FILENAME = 'status.json'
MANIFEST_FILENAME = 'manifest.json'
RUN_LOG_FILENAME = 'run.log'


class ExecutionJobState(StrEnum):
    UNKNOWN = 'unknown'
    RUNNING = 'running'
    COMPLETED = 'completed'
    FAILED = 'failed'
    STALLED = 'stalled'
    CONTRACT_VIOLATION = 'contract_violation'


class ExecutionFailureType(StrEnum):
    NONE = 'none'
    PYTHON_EXCEPTION = 'python_exception'
    CUDA_OOM = 'cuda_oom'
    PROCESS_KILLED = 'process_killed'
    DISK_FULL = 'disk_full'
    MISSING_ARTIFACT_CONTRACT = 'missing_artifact_contract'
    UNKNOWN = 'unknown'


@dataclass(frozen=True)
class ExecutionArtifactLayout:
    artifact_root: str
    light_artifact_dir: str
    heavy_artifact_dir: str
    log_dir: str
    status_path: str
    manifest_path: str
    run_log_path: str

    @classmethod
    def from_artifact_root(cls, artifact_root: str | Path) -> 'ExecutionArtifactLayout':
        root = Path(str(artifact_root)).expanduser()
        light_dir = root / LIGHT_ARTIFACT_DIRNAME
        heavy_dir = root / HEAVY_ARTIFACT_DIRNAME
        log_dir = root / LOG_DIRNAME
        return cls(
            artifact_root=str(root),
            light_artifact_dir=str(light_dir),
            heavy_artifact_dir=str(heavy_dir),
            log_dir=str(log_dir),
            status_path=str(light_dir / STATUS_FILENAME),
            manifest_path=str(light_dir / MANIFEST_FILENAME),
            run_log_path=str(log_dir / RUN_LOG_FILENAME),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def to_env(self) -> dict[str, str]:
        return execution_artifact_env(self)


@dataclass(frozen=True)
class ExecutionJobHandle:
    job_id: str
    backend: str
    workdir: str
    artifact_layout: ExecutionArtifactLayout
    submitted_at: str = ''
    remote_workdir: str = ''
    tmux_session: str = ''

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload['artifact_layout'] = self.artifact_layout.to_dict()
        return {key: value for key, value in payload.items() if value not in ('', None)}


@dataclass(frozen=True)
class ExecutionMonitorResult:
    job_state: ExecutionJobState
    backend: str
    artifacts_ready: bool = False
    light_artifacts: tuple[str, ...] = ()
    missing_required_artifacts: tuple[str, ...] = ()
    failure_type: ExecutionFailureType = ExecutionFailureType.NONE
    evidence: tuple[str, ...] = ()
    recommended_next_action: str = ''

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload['job_state'] = self.job_state.value
        payload['failure_type'] = self.failure_type.value
        payload['light_artifacts'] = list(self.light_artifacts)
        payload['missing_required_artifacts'] = list(self.missing_required_artifacts)
        payload['evidence'] = list(self.evidence)
        return {key: value for key, value in payload.items() if value not in ('', None, [], ())}


def execution_artifact_env(layout: ExecutionArtifactLayout) -> dict[str, str]:
    return {
        'OWNPI_ARTIFACT_ROOT': layout.artifact_root,
        'OWNPI_LIGHT_ARTIFACT_DIR': layout.light_artifact_dir,
        'OWNPI_HEAVY_ARTIFACT_DIR': layout.heavy_artifact_dir,
        'OWNPI_LOG_DIR': layout.log_dir,
        'OWNPI_STATUS_PATH': layout.status_path,
        'OWNPI_MANIFEST_PATH': layout.manifest_path,
        'OWNPI_RUN_LOG_PATH': layout.run_log_path,
    }


def artifact_layout_shell_exports(layout: ExecutionArtifactLayout) -> str:
    assignments = execution_artifact_env(layout)
    mkdir_targets = [
        layout.light_artifact_dir,
        layout.heavy_artifact_dir,
        layout.log_dir,
    ]
    export_lines = [
        f"export {key}={_shell_quote(value)}"
        for key, value in assignments.items()
    ]
    mkdir_line = 'mkdir -p ' + ' '.join(_shell_quote(item) for item in mkdir_targets)
    return '\n'.join([*export_lines, mkdir_line])


def build_execution_job_handle(
    *,
    job_id: str,
    backend: str,
    workdir: str,
    artifact_layout: ExecutionArtifactLayout,
    submitted_at: str = '',
    remote_workdir: str = '',
    tmux_session: str = '',
) -> ExecutionJobHandle:
    return ExecutionJobHandle(
        job_id=str(job_id or '').strip(),
        backend=str(backend or '').strip(),
        workdir=str(workdir or '').strip(),
        artifact_layout=artifact_layout,
        submitted_at=str(submitted_at or '').strip(),
        remote_workdir=str(remote_workdir or '').strip(),
        tmux_session=str(tmux_session or '').strip(),
    )


def _shell_quote(value: str) -> str:
    return "'" + str(value).replace("'", "'\"'\"'") + "'"
