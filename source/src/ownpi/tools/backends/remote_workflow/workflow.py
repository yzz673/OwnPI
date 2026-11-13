from __future__ import annotations

from pathlib import Path
import shlex
from typing import Any

from ownpi.runtime.execution.job_contract import (
    ExecutionArtifactLayout,
    ExecutionFailureType,
    ExecutionJobState,
    ExecutionMonitorResult,
    artifact_layout_shell_exports,
)
from ownpi.runtime.process_owner.remote_transport import (
    RemoteOwnerLostError,
    RemoteTransportError,
)
from ownpi.tools.backends.remote_workflow.profiles import RemoteProfile
from ownpi.tools.backends.remote_workflow.paths import (
    _is_relative_to,
    _normalize_relative_path,
    _pull_source_destination,
    deploy_source_path,
    deploy_relative_path,
)
from ownpi.tools.backends.remote_workflow.effects import mkdir_with_permissions
from ownpi.runtime.permissions.models import FileSystemAccessMode
from ownpi.runtime.permissions.policy import require_file_access
from ownpi.tools.backends.remote_workflow.results import WorkflowResult
from ownpi.tools.backends.remote_workflow.shell import (
    RemoteWorkflowGateway,
    remote_arg,
    rsync_rsh,
)
from ownpi.tools.backends.remote_workflow import resources as resource_backend


async def check(profile: RemoteProfile, *, gateway: RemoteWorkflowGateway) -> WorkflowResult:
    quoted_remote_root = shlex.quote(profile.remote_root)
    quoted_conda_env = shlex.quote(profile.conda_env)
    script = "\n".join(
        [
            "set -euo pipefail",
            f"remote_root={quoted_remote_root}",
            f"conda_env={quoted_conda_env}",
            'printf "ssh=ok\\n"',
            'printf "remote_root_exists=%s\\n" "$(test -d "$remote_root" && echo yes || echo no)"',
            'printf "python=%s\\n" "$(command -v python || true)"',
            'printf "tmux=%s\\n" "$(command -v tmux || true)"',
            'printf "rsync=%s\\n" "$(command -v rsync || true)"',
            'if [ -n "$conda_env" ]; then',
            '  if command -v conda >/dev/null 2>&1; then :;',
            '  elif [ -f "$HOME/anaconda3/etc/profile.d/conda.sh" ]; then . "$HOME/anaconda3/etc/profile.d/conda.sh";',
            '  elif [ -f "$HOME/miniconda3/etc/profile.d/conda.sh" ]; then . "$HOME/miniconda3/etc/profile.d/conda.sh";',
            '  elif [ -f "/opt/conda/etc/profile.d/conda.sh" ]; then . "/opt/conda/etc/profile.d/conda.sh";',
            '  else printf "conda_env_status=missing_conda\\n"; exit 0; fi',
            '  if conda activate "$conda_env" >/dev/null 2>&1; then',
            '    printf "conda_env_status=ok\\n"',
            '    printf "conda_env_name=%s\\n" "$conda_env"',
            '    printf "conda_env_python=%s\\n" "$(command -v python || true)"',
            '  else',
            '    printf "conda_env_status=activate_failed\\n"',
            '    printf "conda_env_name=%s\\n" "$conda_env"',
            '  fi',
            'else',
            '  printf "conda_env_status=not_configured\\n"',
            'fi',
        ]
    )
    remote = await gateway.run_remote_bash(script, name="remote_check", timeout_seconds=30)
    warnings: list[str] = []
    remote_probe = _parse_key_values(remote.stdout)
    data = {
        **_gateway_evidence(remote),
        "profile": profile.to_dict(),
        "remote_probe": remote_probe,
    }
    env_status = remote_probe.get("conda_env_status", "not_configured")
    if profile.conda_env and env_status != "ok":
        warnings.append(f"remote conda env check failed: {profile.conda_env} ({env_status})")
    ok = remote.ok and (not profile.conda_env or env_status == "ok")
    summary = "remote check passed" if ok else "remote check failed"
    return WorkflowResult(
        ok=ok,
        command="check",
        summary=summary,
        stdout=remote.stdout,
        stderr=remote.stderr,
        returncode=0 if ok else remote.returncode or 1,
        data=data,
        warnings=tuple(warnings),
    )


async def resources(profile: RemoteProfile, *, gateway: RemoteWorkflowGateway) -> WorkflowResult:
    return await resource_backend.resources(profile, gateway=gateway)


async def deploy(
    profile: RemoteProfile,
    *,
    gateway: RemoteWorkflowGateway,
    paths: list[str],
    all_paths: bool = False,
    delete: bool = False,
    dry_run: bool = False,
) -> WorkflowResult:
    selected = ["."] if all_paths else paths
    if not selected:
        return WorkflowResult(ok=False, command="deploy", summary="deploy requires --path or --all", returncode=2)
    if not profile.local_root.exists():
        return WorkflowResult(
            ok=False,
            command="deploy",
            summary=f"local root missing: {profile.local_root}",
            returncode=2,
        )
    outputs: list[str] = []
    errors: list[str] = []
    for item in selected:
        normalized = deploy_relative_path(profile, item)
        source_path = deploy_source_path(profile, item)
        require_file_access(gateway.effective_permissions, source_path, FileSystemAccessMode.READ)
        if normalized == ".":
            source = f"{profile.local_root}/"
            destination = remote_arg(profile, f"{profile.remote_root}/")
            mkdir_result = await _remote_mkdir(gateway, profile.remote_root)
        else:
            if not _is_relative_to(source_path, profile.local_root):
                return WorkflowResult(ok=False, command="deploy", summary=f"path escapes local root: {item}", returncode=2)
            if not source_path.exists():
                return WorkflowResult(ok=False, command="deploy", summary=f"local path missing: {source_path}", returncode=2)
            remote_parent = f"{profile.remote_root}/{Path(normalized).parent}"
            if str(Path(normalized).parent) == ".":
                remote_parent = profile.remote_root
            source = str(source_path)
            destination = remote_arg(profile, f"{remote_parent}/")
            mkdir_result = await _remote_mkdir(gateway, remote_parent)
        if not mkdir_result.ok:
            return mkdir_result
        rsync_result = await _run_rsync(
            gateway,
            profile,
            source,
            destination,
            command_name="deploy",
            delete=delete,
            dry_run=dry_run,
        )
        outputs.append(rsync_result.stdout)
        errors.append(rsync_result.stderr)
        if not rsync_result.ok:
            return rsync_result
    return WorkflowResult(
        ok=True,
        command="deploy",
        summary=f"deployed {len(selected)} path(s)",
        stdout="\n".join(part for part in outputs if part),
        stderr="\n".join(part for part in errors if part),
        data={
            **_gateway_evidence(rsync_result),
            "profile": profile.name,
            "paths": selected,
            "dry_run": dry_run,
            "delete": delete,
        },
    )


async def pull(
    profile: RemoteProfile,
    *,
    gateway: RemoteWorkflowGateway,
    paths: list[str],
    all_paths: bool = False,
    delete: bool = False,
    dry_run: bool = False,
) -> WorkflowResult:
    selected = ["."] if all_paths else paths
    if not selected:
        return WorkflowResult(ok=False, command="pull", summary="pull requires --path or --all", returncode=2)
    transfers = [_pull_source_destination(profile, item) for item in selected]
    for _, _, destination in transfers:
        require_file_access(gateway.effective_permissions, destination, FileSystemAccessMode.WRITE)
    outputs: list[str] = []
    errors: list[str] = []
    local_destinations: list[str] = []
    for source, destination, local_destination in transfers:
        directory = local_destination if local_destination == profile.local_root else local_destination.parent
        mkdir_with_permissions(directory, gateway.effective_permissions)
        rsync_result = await _run_rsync(
            gateway,
            profile,
            source,
            destination,
            command_name="pull",
            delete=delete,
            dry_run=dry_run,
        )
        outputs.append(rsync_result.stdout)
        errors.append(rsync_result.stderr)
        local_destinations.append(str(local_destination))
        if not rsync_result.ok:
            return rsync_result
    return WorkflowResult(
        ok=True,
        command="pull",
        summary=f"pulled {len(selected)} path(s)",
        stdout="\n".join(part for part in outputs if part),
        stderr="\n".join(part for part in errors if part),
        data={
            **_gateway_evidence(rsync_result),
            "profile": profile.name,
            "paths": selected,
            "local_destinations": local_destinations,
            "dry_run": dry_run,
            "delete": delete,
        },
    )


async def run(
    profile: RemoteProfile,
    *,
    gateway: RemoteWorkflowGateway,
    command: str,
    tmux_session: str,
    process_owner_manager: Any,
    owner_id: str,
    env: str | None = None,
    artifact_layout: ExecutionArtifactLayout | None = None,
    job_handle: dict[str, object] | None = None,
    run_id: str = '',
    actor_id: str = '',
) -> WorkflowResult:
    if not command.strip():
        return WorkflowResult(ok=False, command="run", summary="run requires command", returncode=2)
    if not tmux_session.strip():
        return WorkflowResult(ok=False, command="run", summary="run requires tmux_session", returncode=2)
    if not owner_id.strip():
        return WorkflowResult(ok=False, command="run", summary="run requires process owner", returncode=2)
    del gateway
    remote_command = _with_conda(
        _with_artifact_contract(command, artifact_layout),
        env if env is not None else profile.conda_env,
    )
    script = "\n".join(
        [
            "set -euo pipefail",
            f"mkdir -p {shlex.quote(profile.remote_root)}",
            f"cd {shlex.quote(profile.remote_root)}",
            f"exec bash -lc {shlex.quote(remote_command)}",
        ]
    )
    try:
        view = await process_owner_manager.start_session_async(
            owner_id=owner_id,
            run_id=run_id,
            actor_id=actor_id,
            argv=['bash', '-lc', script],
            cwd='/',
            env={},
            tty=False,
            pipe_stdin=False,
            command_display=command,
        )
        process_id = view.process_id
    except Exception as exc:
        return WorkflowResult(
            ok=False,
            command='run',
            summary='remote process owner failed to start execution',
            stderr=str(exc),
            returncode=1,
            data={
                'profile': profile.name,
                'owner_id': owner_id,
                'failure_class': 'remote_process_owner_start_failed',
            },
        )
    handle = dict(job_handle or {})
    handle.update({'owner_id': owner_id, 'process_id': process_id})
    return WorkflowResult(
        ok=True,
        command="run",
        summary=f"started remote execution {tmux_session} in {profile.remote_root}",
        returncode=0,
        data={
            "profile": profile.name,
            "tmux_session": tmux_session,
            "remote_root": profile.remote_root,
            "owner_id": owner_id,
            "process_id": process_id,
            "command_display": f"remote:{profile.name} run {tmux_session}: {command}",
            **({"artifact_layout": artifact_layout.to_dict()} if artifact_layout is not None else {}),
            "job_handle": handle,
        },
    )


async def monitor(
    profile: RemoteProfile,
    *,
    gateway: RemoteWorkflowGateway,
    process_owner_manager: Any,
    owner_id: str,
    process_id: str,
    tmux_session: str = "",
    artifact_layout: ExecutionArtifactLayout | None = None,
    artifact_root: str = "",
    tail: int = 200,
) -> WorkflowResult:
    layout = artifact_layout or (
        ExecutionArtifactLayout.from_artifact_root(artifact_root)
        if str(artifact_root or "").strip()
        else None
    )
    if layout is None:
        return WorkflowResult(
            ok=False,
            command="monitor",
            summary="monitor requires artifact_root or task artifact layout",
            returncode=2,
        )
    try:
        owner_response = await process_owner_manager.read_process_async(
            owner_id=owner_id,
            process_id=process_id,
            after_seq=0,
            wait_ms=0,
            max_bytes=64 * 1024,
        )
    except RemoteOwnerLostError as exc:
        monitor_result = ExecutionMonitorResult(
            job_state=ExecutionJobState.UNKNOWN,
            backend='remote_workflow',
            failure_type=ExecutionFailureType.UNKNOWN,
            evidence=('process_owner=lost',),
            recommended_next_action='inspect_owner_loss',
        )
        return WorkflowResult(
            ok=False,
            command='monitor',
            summary='remote process owner session is no longer resumable',
            stderr=str(exc),
            returncode=1,
            data={
                'profile': profile.name,
                'tmux_session': tmux_session,
                'owner_id': owner_id,
                'process_id': process_id,
                'artifact_layout': layout.to_dict(),
                'monitor': monitor_result.to_dict(),
                'failure_class': 'process_owner_lost',
            },
        )
    except RemoteTransportError as exc:
        monitor_result = ExecutionMonitorResult(
            job_state=ExecutionJobState.UNKNOWN,
            backend='remote_workflow',
            evidence=('process_owner_transport=temporarily_unavailable',),
            recommended_next_action='continue_polling',
        )
        return WorkflowResult(
            ok=False,
            command='monitor',
            summary='remote process owner transport is temporarily unavailable',
            stderr=str(exc),
            returncode=1,
            data={
                'profile': profile.name,
                'tmux_session': tmux_session,
                'owner_id': owner_id,
                'process_id': process_id,
                'artifact_layout': layout.to_dict(),
                'monitor': monitor_result.to_dict(),
                'failure_class': 'process_owner_transport_unavailable',
            },
        )
    except Exception as exc:
        monitor_result = ExecutionMonitorResult(
            job_state=ExecutionJobState.UNKNOWN,
            backend='remote_workflow',
            failure_type=ExecutionFailureType.UNKNOWN,
            evidence=('process_owner=unavailable',),
            recommended_next_action='inspect_owner_loss',
        )
        return WorkflowResult(
            ok=False,
            command='monitor',
            summary='remote process owner state is unavailable',
            stderr=str(exc),
            returncode=1,
            data={
                'profile': profile.name,
                'tmux_session': tmux_session,
                'owner_id': owner_id,
                'process_id': process_id,
                'artifact_layout': layout.to_dict(),
                'monitor': monitor_result.to_dict(),
                'failure_class': 'process_owner_lost',
            },
        )
    script = "\n".join(
        [
            "set -euo pipefail",
            f"light_dir={shlex.quote(layout.light_artifact_dir)}",
            f"manifest_path={shlex.quote(layout.manifest_path)}",
            f"status_path={shlex.quote(layout.status_path)}",
            f"run_log_path={shlex.quote(layout.run_log_path)}",
            'printf "light_dir_exists=%s\\n" "$(test -d "$light_dir" && echo yes || echo no)"',
            'printf "manifest_exists=%s\\n" "$(test -f "$manifest_path" && echo yes || echo no)"',
            'printf "status_exists=%s\\n" "$(test -f "$status_path" && echo yes || echo no)"',
            'printf "run_log_exists=%s\\n" "$(test -f "$run_log_path" && echo yes || echo no)"',
            'printf "__REMOTE_MONITOR_SECTION__ light_artifacts\\n"',
            'if [ -d "$light_dir" ]; then find "$light_dir" -maxdepth 2 -type f -printf "%p\\t%s\\n" | sort; fi',
            'printf "__REMOTE_MONITOR_SECTION__ status_preview\\n"',
            'if [ -f "$status_path" ]; then head -c 6000 "$status_path"; printf "\\n"; fi',
            'printf "__REMOTE_MONITOR_SECTION__ manifest_preview\\n"',
            'if [ -f "$manifest_path" ]; then head -c 6000 "$manifest_path"; printf "\\n"; fi',
            'printf "__REMOTE_MONITOR_SECTION__ log_tail\\n"',
            f'if [ -f "$run_log_path" ]; then tail -n {int(tail)} "$run_log_path"; fi',
        ]
    )
    result = await gateway.run_remote_bash(script, name="remote_monitor", timeout_seconds=30)
    parsed = _parse_monitor_stdout(result.stdout)
    values = parsed.get('values')
    if isinstance(values, dict):
        values['owner_closed'] = 'yes' if bool(owner_response.get('closed')) else 'no'
        exit_code = owner_response.get('exit_code')
        values['owner_exit_code'] = '' if exit_code is None else str(exit_code)
    owner_output = ''.join(
        str(item.get('chunk', '') or '')
        for item in owner_response.get('chunks', [])
        if isinstance(item, dict)
    )
    sections = parsed.get('sections')
    if isinstance(sections, dict) and owner_output:
        sections['owner_output'] = owner_output
    monitor_result = _classify_monitor_result(
        backend="remote_workflow",
        parsed=parsed,
        command_ok=result.ok,
    )
    return WorkflowResult(
        ok=result.ok,
        command="monitor",
        summary=f"remote monitor state={monitor_result.job_state.value}",
        stdout=result.stdout,
        stderr=result.stderr,
        returncode=result.returncode,
        data={
            **_gateway_evidence(result),
            "profile": profile.name,
            "tmux_session": tmux_session,
            "owner_id": owner_id,
            "process_id": process_id,
            "final_seq": owner_response.get('final_seq'),
            "artifact_layout": layout.to_dict(),
            "monitor": monitor_result.to_dict(),
            "remote_probe": parsed,
        },
    )


async def logs(
    profile: RemoteProfile,
    *,
    gateway: RemoteWorkflowGateway,
    file: str,
    tail: int = 100,
) -> WorkflowResult:
    remote_file = _normalize_remote_read_file(profile, file)
    if remote_file == "":
        return WorkflowResult(ok=False, command="logs", summary="logs requires a file path", returncode=2)
    script = f"set -euo pipefail\ntest -f {shlex.quote(remote_file)}\ntail -n {int(tail)} {shlex.quote(remote_file)}"
    result = await gateway.run_remote_bash(script, name="remote_logs", timeout_seconds=30)
    data: dict[str, object] = {
        **_gateway_evidence(result),
        "profile": profile.name,
        "file": remote_file,
        "tail": tail,
    }
    warnings: tuple[str, ...] = ()
    if not result.ok:
        recovery_hint = (
            f"remote_logs could not read `{remote_file}`. If you need process/job state or the standard "
            "remote workflow artifact status, call remote_monitor with the logical job label from remote_run "
            "instead of using local shell process-control commands. If you need a specific file preview, provide "
            "an existing remote file path."
        )
        data.update(
            {
                "failure_class": "remote_observation_error",
                "recommended_next_action": "use_remote_monitor_or_existing_remote_file",
                "recovery_hint": recovery_hint,
                "context_effects": {
                    "failure_class": "remote_observation_error",
                    "recommended_next_action": "use_remote_monitor_or_existing_remote_file",
                    "recovery_hint": recovery_hint,
                    "suggested_tool_names": ["remote_monitor", "remote_logs"],
                    "missing_remote_file": remote_file,
                },
            }
        )
        warnings = (recovery_hint,)
    return WorkflowResult(
        ok=result.ok,
        command="logs",
        summary=f"read remote log {remote_file}" if result.ok else result.summary,
        stdout=result.stdout,
        stderr=result.stderr,
        returncode=result.returncode,
        data=data,
        warnings=warnings,
    )


async def _remote_mkdir(gateway: RemoteWorkflowGateway, remote_path: str) -> WorkflowResult:
    return await gateway.run_remote_bash(
        f"set -euo pipefail\nmkdir -p {shlex.quote(remote_path)}",
        name="remote_mkdir",
        timeout_seconds=30,
    )


async def _run_rsync(
    gateway: RemoteWorkflowGateway,
    profile: RemoteProfile,
    source: str,
    destination: str,
    *,
    command_name: str,
    delete: bool,
    dry_run: bool,
) -> WorkflowResult:
    command = ["rsync", "-az", "--partial", "--human-readable", "-e", rsync_rsh(profile)]
    if delete:
        command.append("--delete")
    if dry_run:
        command.append("-n")
    command.extend([source, destination])
    result = await gateway.run_transfer(command, name=command_name, timeout_seconds=None)
    return WorkflowResult(
        ok=result.ok,
        command=command_name,
        summary=result.summary,
        stdout=result.stdout,
        stderr=result.stderr,
        returncode=result.returncode,
        data={
            **_gateway_evidence(result),
            "source": source,
            "destination": destination,
            "dry_run": dry_run,
            "delete": delete,
        },
    )


def _gateway_evidence(result: WorkflowResult) -> dict[str, object]:
    return {
        key: value
        for key, value in result.data.items()
        if key
        in {
            'canonical_gateway',
            'environment_receipt_id',
            'permission_profile_id',
            'permission_policy_hash',
        }
    }


def _normalize_remote_read_file(profile: RemoteProfile, value: str) -> str:
    raw = str(value or "").strip()
    if raw in {"", "."}:
        return ""
    path = Path(raw)
    if ".." in path.parts:
        raise ValueError(f"logs path must not contain '..': {value}")
    if path.is_absolute():
        return path.as_posix()
    normalized = _normalize_relative_path(raw, command_name="logs")
    return f"{str(profile.remote_root).rstrip('/')}/{normalized}"


def _with_conda(command: str, env: str) -> str:
    if not env.strip():
        return command
    quoted_env = shlex.quote(env)
    return (
        "if command -v conda >/dev/null 2>&1; then :; "
        "elif [ -f \"$HOME/anaconda3/etc/profile.d/conda.sh\" ]; then . \"$HOME/anaconda3/etc/profile.d/conda.sh\"; "
        "elif [ -f \"$HOME/miniconda3/etc/profile.d/conda.sh\" ]; then . \"$HOME/miniconda3/etc/profile.d/conda.sh\"; "
        "elif [ -f \"/opt/conda/etc/profile.d/conda.sh\" ]; then . \"/opt/conda/etc/profile.d/conda.sh\"; "
        "else echo 'remote-workflow: unable to initialize conda' >&2; exit 127; fi; "
        f"conda activate {quoted_env} && {command}"
    )


def _with_artifact_contract(command: str, artifact_layout: ExecutionArtifactLayout | None) -> str:
    if artifact_layout is None:
        return command
    return artifact_layout_shell_exports(artifact_layout) + "\n" + command


def _parse_monitor_stdout(stdout: str) -> dict[str, object]:
    lines = stdout.splitlines()
    values: dict[str, str] = {}
    sections: dict[str, list[str]] = {}
    current_section = ""
    for line in lines:
        if line.startswith("__REMOTE_MONITOR_SECTION__ "):
            current_section = line.split(" ", 1)[1].strip()
            sections.setdefault(current_section, [])
            continue
        if current_section:
            sections.setdefault(current_section, []).append(line)
            continue
        if "=" in line:
            key, value = line.split("=", 1)
            values[key.strip()] = value.strip()
    light_artifacts = []
    for line in sections.get("light_artifacts", []):
        if not line.strip():
            continue
        path, _, size = line.partition("\t")
        light_artifacts.append({"path": path.strip(), "size_bytes": _int_or_zero(size)})
    return {
        "values": values,
        "sections": {key: "\n".join(value).strip() for key, value in sections.items()},
        "light_artifacts": light_artifacts,
    }


def _classify_monitor_result(
    *,
    backend: str,
    parsed: dict[str, object],
    command_ok: bool,
) -> ExecutionMonitorResult:
    values = parsed.get("values") if isinstance(parsed.get("values"), dict) else {}
    light_artifacts = parsed.get("light_artifacts") if isinstance(parsed.get("light_artifacts"), list) else []
    sections = parsed.get("sections") if isinstance(parsed.get("sections"), dict) else {}
    combined_text = "\n".join(str(value) for value in sections.values())
    failure_type = _detect_failure_type(combined_text)
    artifact_paths = tuple(
        str(item.get("path", ""))
        for item in light_artifacts
        if isinstance(item, dict) and str(item.get("path", "")).strip()
    )
    missing = _missing_contract_artifacts(values)
    has_result_artifact = _has_result_artifact_path(artifact_paths)
    evidence = _monitor_evidence(values=values, artifact_paths=artifact_paths, failure_type=failure_type)
    if not command_ok:
        state = ExecutionJobState.UNKNOWN
        action = "inspect_failure"
    elif failure_type is not ExecutionFailureType.NONE:
        state = ExecutionJobState.FAILED
        action = "inspect_failure"
    elif values.get('owner_closed') != 'yes':
        state = ExecutionJobState.RUNNING
        action = "continue_polling"
    elif values.get('owner_exit_code') not in {'', '0'}:
        state = ExecutionJobState.FAILED
        action = 'inspect_failure'
    elif missing:
        state = ExecutionJobState.CONTRACT_VIOLATION
        action = "fix_artifact_layout"
    elif artifact_paths and has_result_artifact:
        state = ExecutionJobState.COMPLETED
        action = "pull_light_artifacts"
    elif artifact_paths:
        state = ExecutionJobState.COMPLETED
        action = "pull_light_artifacts"
    else:
        state = ExecutionJobState.UNKNOWN
        action = "continue_polling"
    return ExecutionMonitorResult(
        job_state=state,
        backend=backend,
        artifacts_ready=bool(artifact_paths and not missing),
        light_artifacts=artifact_paths,
        missing_required_artifacts=missing,
        failure_type=failure_type,
        evidence=evidence,
        recommended_next_action=action,
    )


def _missing_contract_artifacts(values: dict[str, str]) -> tuple[str, ...]:
    missing: list[str] = []
    if values.get("light_dir_exists") != "yes":
        missing.append("light/")
    if values.get("manifest_exists") != "yes":
        missing.append("light/manifest.json")
    if values.get("status_exists") != "yes":
        missing.append("light/status.json")
    return tuple(missing)


def _has_result_artifact_path(paths: tuple[str, ...]) -> bool:
    result_markers = ("summary", "report", "result", "metric", "metrics", "qc")
    control_names = {"manifest.json", "status.json"}
    for path in paths:
        name = Path(str(path or "")).name.casefold()
        if not name or name in control_names:
            continue
        if any(marker in name for marker in result_markers):
            return True
    return False


def _monitor_evidence(
    *,
    values: dict[str, str],
    artifact_paths: tuple[str, ...],
    failure_type: ExecutionFailureType,
) -> tuple[str, ...]:
    evidence = [
        f"owner_closed={values.get('owner_closed', 'unknown')}",
        f"owner_exit_code={values.get('owner_exit_code', 'unknown')}",
        f"light_dir_exists={values.get('light_dir_exists', 'unknown')}",
        f"manifest_exists={values.get('manifest_exists', 'unknown')}",
        f"status_exists={values.get('status_exists', 'unknown')}",
        f"light_artifact_count={len(artifact_paths)}",
    ]
    if failure_type is not ExecutionFailureType.NONE:
        evidence.append(f"failure_type={failure_type.value}")
    return tuple(evidence)


def _detect_failure_type(text: str) -> ExecutionFailureType:
    normalized = text.casefold()
    if "cuda out of memory" in normalized or "outofmemoryerror" in normalized:
        return ExecutionFailureType.CUDA_OOM
    if "no space left on device" in normalized:
        return ExecutionFailureType.DISK_FULL
    if "traceback (most recent call last)" in normalized:
        return ExecutionFailureType.PYTHON_EXCEPTION
    if "killed" in normalized or "sigkill" in normalized:
        return ExecutionFailureType.PROCESS_KILLED
    return ExecutionFailureType.NONE


def _int_or_zero(value: str) -> int:
    try:
        return int(str(value).strip())
    except ValueError:
        return 0


def _parse_key_values(text: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in text.splitlines():
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip()
    return values
