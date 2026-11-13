from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
import hashlib
from pathlib import Path
from pathlib import PurePosixPath
import re
from typing import TYPE_CHECKING, Any, Awaitable, Callable

from ownpi.runtime.contracts import ToolResult
from ownpi.runtime.execution.job_contract import (
    ExecutionArtifactLayout,
    build_execution_job_handle,
)
from ownpi.runtime.execution.bindings import remote_run_scoped_tmux_session
from ownpi.runtime.process_owner.manager import ProcessOwnerManager
from ownpi.runtime.process_owner.remote import (
    RemoteEndpointRef,
    endpoint_ref_for,
    run_scoped_paths,
)
from ownpi.runtime.process_owner.remote_bootstrap import bootstrap_remote_owner_async
from ownpi.tools.backends.remote_workflow import workflow
from ownpi.tools.backends.remote_workflow.effects import effective_permissions, revalidate_remote_operation
from ownpi.runtime.permissions.policy import lexical_absolute_path
from ownpi.tools.backends.remote_workflow.previews import _pulled_artifact_preview_data
from ownpi.tools.backends.remote_workflow.profiles import (
    RemoteProfile,
    list_profiles,
    load_profile,
    profile_from_dict,
    remove_profile,
    resolve_config_dir,
    write_profile,
)
from ownpi.tools.backends.remote_workflow.results import WorkflowResult
from ownpi.tools.backends.remote_workflow.shell import (
    RemoteSandboxProtocolUnavailableError,
    RemoteWorkflowGateway,
)
from ownpi.tools.common import clamp_int, metadata_for_context

if TYPE_CHECKING:
    from ownpi.runtime.tools.definitions import ToolExecutionContext

async def run_remote_profile_list(context: ToolExecutionContext, arguments: dict[str, Any]) -> ToolResult:
    config_dir = _config_dir(arguments)
    try:
        profiles = list_profiles(config_dir, effective_permissions=effective_permissions(context))
    except (OSError, ValueError) as exc:
        return ToolResult(ok=False, tool_name='remote_profile_list', error=str(exc))
    data = {
        "config_dir": str(config_dir),
        "profiles": [profile.to_dict() for profile in profiles],
    }
    return ToolResult(
        ok=True,
        tool_name="remote_profile_list",
        data=data,
        stdout="\n".join(profile.name for profile in profiles),
        metadata=metadata_for_context(context, config_dir=str(config_dir)),
    )


async def run_remote_profile_show(context: ToolExecutionContext, arguments: dict[str, Any]) -> ToolResult:
    async def invoke(profile: RemoteProfile, config_dir: Path) -> ToolResult:
        return ToolResult(
            ok=True,
            tool_name="remote_profile_show",
            data={"config_dir": str(config_dir), "profile": profile.to_dict()},
            stdout="\n".join(f"{key}={value}" for key, value in profile.to_dict().items()),
            metadata=metadata_for_context(context, config_dir=str(config_dir), profile=profile.name),
        )

    return await _with_profile_error(
        context,
        tool_name="remote_profile_show",
        arguments=arguments,
        callback=invoke,
    )


def run_remote_profile_write(context: ToolExecutionContext, arguments: dict[str, Any]) -> ToolResult:
    config_dir = _config_dir(arguments)
    payload = arguments.get("profile")
    if not isinstance(payload, dict):
        return ToolResult(ok=False, tool_name="remote_profile_write", error="profile object is required")
    try:
        profile = profile_from_dict(payload)
        path = write_profile(config_dir, profile, overwrite=bool(arguments.get("overwrite", False)))
    except Exception as exc:
        return ToolResult(
            ok=False,
            tool_name="remote_profile_write",
            error=str(exc),
            metadata=metadata_for_context(context, config_dir=str(config_dir)),
        )
    return ToolResult(
        ok=True,
        tool_name="remote_profile_write",
        data={"config_dir": str(config_dir), "path": str(path), "profile": profile.to_dict()},
        stdout=f"profile={profile.name}\npath={path}",
        metadata=metadata_for_context(context, config_dir=str(config_dir), profile=profile.name),
    )


def run_remote_profile_remove(context: ToolExecutionContext, arguments: dict[str, Any]) -> ToolResult:
    config_dir = _config_dir(arguments)
    profile_name = _profile_name(arguments)
    if not profile_name:
        return ToolResult(ok=False, tool_name="remote_profile_remove", error="profile is required")
    try:
        path = remove_profile(config_dir, profile_name)
    except Exception as exc:
        return ToolResult(
            ok=False,
            tool_name="remote_profile_remove",
            error=str(exc),
            metadata=metadata_for_context(context, config_dir=str(config_dir), profile=profile_name),
        )
    return ToolResult(
        ok=True,
        tool_name="remote_profile_remove",
        data={"config_dir": str(config_dir), "profile": profile_name, "path": str(path)},
        stdout=f"removed={path}",
        metadata=metadata_for_context(context, config_dir=str(config_dir), profile=profile_name),
    )


async def run_remote_check(context: ToolExecutionContext, arguments: dict[str, Any]) -> ToolResult:
    async def invoke(profile: RemoteProfile, _config_dir: Path) -> ToolResult:
        return _to_tool_result(
            context,
            tool_name="remote_check",
            result=await workflow.check(profile, gateway=_gateway(context, profile)),
            profile=profile,
        )

    return await _with_profile_error(
        context,
        tool_name="remote_check",
        arguments=arguments,
        callback=invoke,
    )


async def run_remote_resources(context: ToolExecutionContext, arguments: dict[str, Any]) -> ToolResult:
    async def invoke(profile: RemoteProfile, _config_dir: Path) -> ToolResult:
        return _to_tool_result(
            context,
            tool_name="remote_resources",
            result=await workflow.resources(profile, gateway=_gateway(context, profile)),
            profile=profile,
        )

    return await _with_profile_error(
        context,
        tool_name="remote_resources",
        arguments=arguments,
        callback=invoke,
    )


async def run_remote_deploy(context: ToolExecutionContext, arguments: dict[str, Any]) -> ToolResult:
    async def invoke(profile: RemoteProfile, _config_dir: Path) -> ToolResult:
        return _to_tool_result(
            context,
            tool_name="remote_deploy",
            result=await workflow.deploy(
                profile,
                gateway=_gateway(context, profile),
                paths=_deploy_paths(context, profile, arguments),
                all_paths=bool(arguments.get("all", False)),
                delete=bool(arguments.get("delete", False)),
                dry_run=bool(arguments.get("dry_run", False)),
            ),
            profile=profile,
        )

    return await _with_profile_error(
        context,
        tool_name="remote_deploy",
        arguments=arguments,
        callback=invoke,
    )


async def run_remote_pull(context: ToolExecutionContext, arguments: dict[str, Any]) -> ToolResult:
    async def invoke(profile: RemoteProfile, _config_dir: Path) -> ToolResult:
        return _to_tool_result(
            context,
            tool_name="remote_pull",
            result=_with_owner_refs(
                await workflow.pull(
                    profile,
                    gateway=_gateway(context, profile),
                    paths=_pull_paths(context, arguments),
                    all_paths=bool(arguments.get("all", False)),
                    delete=bool(arguments.get("delete", False)),
                    dry_run=bool(arguments.get("dry_run", False)),
                ),
                context,
            ),
            profile=profile,
        )

    return await _with_profile_error(
        context,
        tool_name="remote_pull",
        arguments=arguments,
        callback=invoke,
    )


async def run_remote_run(context: ToolExecutionContext, arguments: dict[str, Any]) -> ToolResult:
    command = str(arguments.get("command", arguments.get("cmd", "")) or "").strip()
    session = _remote_tmux_session(context, arguments)
    artifact_root = str(arguments.get("artifact_root", "") or "").strip()
    env = arguments.get("env")
    env_override = str(env) if env is not None else None
    async def invoke(profile: RemoteProfile, _config_dir: Path) -> ToolResult:
        return _to_tool_result(
            context,
            tool_name="remote_run",
            result=await _run_remote_execution(
                context=context,
                profile=profile,
                command=command,
                session=session,
                env_override=env_override,
                artifact_root=artifact_root,
            ),
            profile=profile,
        )

    return await _with_profile_error(
        context,
        tool_name="remote_run",
        arguments=arguments,
        callback=invoke,
    )


async def run_remote_monitor(context: ToolExecutionContext, arguments: dict[str, Any]) -> ToolResult:
    session = _remote_tmux_session(context, arguments)
    artifact_root = str(arguments.get("artifact_root", "") or "").strip()
    tail = clamp_int(arguments.get("tail"), default=200, minimum=1, maximum=5000)
    async def invoke(profile: RemoteProfile, _config_dir: Path) -> ToolResult:
        return _to_tool_result(
            context,
            tool_name="remote_monitor",
            result=await _monitor_remote_execution(
                context=context,
                profile=profile,
                arguments=arguments,
                session=session,
                artifact_root=artifact_root,
                tail=tail,
            ),
            profile=profile,
        )

    return await _with_profile_error(
        context,
        tool_name="remote_monitor",
        arguments=arguments,
        callback=invoke,
    )


async def run_remote_logs(context: ToolExecutionContext, arguments: dict[str, Any]) -> ToolResult:
    file_path = str(arguments.get("file", arguments.get("file_path", "")) or "").strip()
    tail = clamp_int(arguments.get("tail"), default=100, minimum=1, maximum=5000)
    async def invoke(profile: RemoteProfile, _config_dir: Path) -> ToolResult:
        return _to_tool_result(
            context,
            tool_name="remote_logs",
            result=await workflow.logs(
                profile,
                gateway=_gateway(context, profile),
                file=file_path,
                tail=tail,
            ),
            profile=profile,
        )

    return await _with_profile_error(
        context,
        tool_name="remote_logs",
        arguments=arguments,
        callback=invoke,
    )


def _config_dir(arguments: dict[str, Any]) -> Path:
    raw = arguments.get("config_dir")
    return resolve_config_dir(str(raw)) if raw is not None and str(raw).strip() else resolve_config_dir()


def _profile_name(arguments: dict[str, Any], context: Any | None = None) -> str:
    explicit = str(arguments.get("profile", arguments.get("profile_name", "")) or "").strip()
    return explicit or str(getattr(context, 'active_remote_profile', '') or '').strip()


def _load_profile(context: ToolExecutionContext, arguments: dict[str, Any]) -> tuple[RemoteProfile, Path]:
    config_dir = _config_dir(arguments)
    profile_name = _profile_name(arguments, context)
    if not profile_name:
        raise ValueError("profile is required")
    return load_profile(config_dir, profile_name, effective_permissions=effective_permissions(context)), config_dir


def _load_effective_profile(context: ToolExecutionContext, arguments: dict[str, Any]) -> tuple[RemoteProfile, Path]:
    profile, config_dir = _load_profile(context, arguments)
    return _apply_runtime_binding_overrides(profile=profile, context=context), config_dir


def _apply_runtime_binding_overrides(*, profile: RemoteProfile, context: ToolExecutionContext) -> RemoteProfile:
    del context
    return profile


def _paths(arguments: dict[str, Any]) -> list[str]:
    raw_paths = arguments.get("paths", arguments.get("path", []))
    if isinstance(raw_paths, str):
        return [raw_paths]
    if not isinstance(raw_paths, list):
        return []
    return [str(item) for item in raw_paths if str(item).strip()]


def _remote_tmux_session(context: ToolExecutionContext, arguments: dict[str, Any]) -> str:
    scoped = remote_run_scoped_tmux_session(
        context,
        artifact_root=str(arguments.get('artifact_root', '') or ''),
    )
    if scoped:
        return scoped
    return str(arguments.get("tmux_session", arguments.get("session", "")) or "").strip()


def _deploy_paths(context: ToolExecutionContext, profile: RemoteProfile, arguments: dict[str, Any]) -> list[str]:
    paths = _paths(arguments)
    if not paths:
        return paths
    project_root_raw = str(getattr(context, "project_root", "") or "").strip()
    if not project_root_raw:
        return paths
    project_root = lexical_absolute_path(project_root_raw)
    local_root = lexical_absolute_path(profile.local_root)
    normalized: list[str] = []
    for raw_path in paths:
        item = str(raw_path).strip()
        if not item or item == ".":
            normalized.append(item)
            continue
        candidate = Path(item).expanduser()
        resolved_candidate = lexical_absolute_path(candidate, cwd=project_root)
        if _path_is_relative_to(resolved_candidate, local_root):
            normalized.append(str(resolved_candidate.relative_to(local_root)))
        else:
            normalized.append(item)
    return normalized


def _path_is_relative_to(candidate: Path, root: Path) -> bool:
    try:
        candidate.relative_to(root)
        return True
    except ValueError:
        return False


def _pull_paths(context: ToolExecutionContext, arguments: dict[str, Any]) -> list[str]:
    scope = str(arguments.get("artifact_scope", "") or "").strip().casefold()
    if scope == "light":
        layout = _artifact_layout_from_context(context=context)
        if layout is None:
            artifact_root = str(arguments.get("artifact_root", "") or "").strip()
            if not artifact_root:
                raise ValueError("remote_pull artifact_scope=light requires task artifact_root or input artifact_root")
            layout = ExecutionArtifactLayout.from_artifact_root(artifact_root)
        return [layout.light_artifact_dir]
    if scope:
        raise ValueError(f"unsupported remote_pull artifact_scope: {scope}")
    return _paths(arguments)


async def _with_profile_error(
    context: ToolExecutionContext,
    *,
    tool_name: str,
    arguments: dict[str, Any],
    callback: Callable[[RemoteProfile, Path], Awaitable[ToolResult]],
) -> ToolResult:
    try:
        profile, config_dir = _load_effective_profile(context, arguments)
        revalidate_remote_operation(
            context, arguments, operation=tool_name.removeprefix('remote_'), profile=profile,
        )
        return await callback(profile, config_dir)
    except RemoteSandboxProtocolUnavailableError as exc:
        effective_permissions = getattr(context, 'effective_permissions', None)
        permission_evidence = (
            effective_permissions.to_dict()
            if effective_permissions is not None and hasattr(effective_permissions, 'to_dict')
            else None
        )
        return ToolResult(
            ok=False,
            tool_name=tool_name,
            error=str(exc),
            data={
                'failure_class': 'remote_sandbox_protocol_unavailable',
                'recommended_next_action': 'select_full_access_or_configure_remote_sandbox',
                'canonical_gateway': False,
                'remote_sandbox_capability': {
                    'provider_kind': 'ssh_remote',
                    'status': 'unavailable',
                    'enforcement_status': 'blocked',
                    'reason': 'compatible_remote_sandbox_protocol_unavailable',
                },
                **(
                    {'effective_permissions': permission_evidence}
                    if isinstance(permission_evidence, dict)
                    else {}
                ),
            },
            metadata=metadata_for_context(
                context,
                profile=_profile_name(arguments, context),
                config_dir=str(_config_dir(arguments)),
                failure_class='remote_sandbox_protocol_unavailable',
            ),
        )
    except Exception as exc:
        return ToolResult(
            ok=False,
            tool_name=tool_name,
            error=str(exc),
            metadata=metadata_for_context(context, profile=_profile_name(arguments, context), config_dir=str(_config_dir(arguments))),
        )


def _gateway(
    context: ToolExecutionContext,
    profile: RemoteProfile,
) -> RemoteWorkflowGateway:
    return RemoteWorkflowGateway(context=context, profile=profile)


async def _run_remote_execution(
    *,
    context: ToolExecutionContext,
    profile: RemoteProfile,
    command: str,
    session: str,
    env_override: str | None,
    artifact_root: str,
) -> WorkflowResult:
    gateway = _gateway(context, profile)
    manager, owner_id = await _ensure_remote_owner(
        context=context,
        profile=profile,
    )
    return await workflow.run(
        profile,
        gateway=gateway,
        command=command,
        tmux_session=session,
        process_owner_manager=manager,
        owner_id=owner_id,
        env=env_override,
        run_id=context.run_id,
        actor_id=context.process_owner_actor_id or context.actor_id or '',
        artifact_layout=_artifact_layout_from_context(
            context=context,
            artifact_root=artifact_root,
        ),
        job_handle=_remote_job_handle_payload(
            context=context,
            profile=profile,
            session=session,
            artifact_root=artifact_root,
        ),
    )


async def _monitor_remote_execution(
    *,
    context: ToolExecutionContext,
    profile: RemoteProfile,
    arguments: dict[str, Any],
    session: str,
    artifact_root: str,
    tail: int,
) -> WorkflowResult:
    gateway = _gateway(context, profile)
    owner_id, process_id = _remote_execution_owner_refs(
        context=context,
        arguments=arguments,
        profile=profile,
        artifact_root=artifact_root,
    )
    if not owner_id or not process_id:
        return WorkflowResult(
            ok=False,
            command='monitor',
            summary='remote execution owner reference is unavailable',
            returncode=2,
            data={
                'profile': profile.name,
                'failure_class': 'missing_process_owner_reference',
            },
        )
    manager, _ = await _ensure_remote_owner(
        context=context,
        profile=profile,
        owner_id=owner_id,
    )
    return await workflow.monitor(
        profile,
        gateway=gateway,
        process_owner_manager=manager,
        owner_id=owner_id,
        process_id=process_id,
        tmux_session=session,
        artifact_layout=_artifact_layout_from_context(
            context=context,
            artifact_root=artifact_root,
        ),
        artifact_root=artifact_root,
        tail=tail,
    )


async def _ensure_remote_owner(
    *,
    context: ToolExecutionContext,
    profile: RemoteProfile,
    owner_id: str = '',
) -> tuple[ProcessOwnerManager, str]:
    manager = context.process_owner_manager
    if not isinstance(manager, ProcessOwnerManager):
        raise RuntimeError('remote execution requires the run-scoped process owner manager')
    resolved_owner_id = owner_id or _remote_owner_id(context=context, profile=profile)
    binding = manager.bindings.get(resolved_owner_id)
    if binding is not None and binding.transport_kind != 'remote_ssh_unix':
        raise RuntimeError('remote execution owner binding has the wrong transport kind')
    endpoint_ref = (
        binding.endpoint_ref
        if binding is not None
        else str(_remote_endpoint_ref(context=context, profile=profile))
    )
    if not manager.has_client(resolved_owner_id):
        await bootstrap_remote_owner_async(
            profile,
            endpoint_ref,
            idle_grace_seconds=manager.owner_daemon_idle_grace_seconds,
        )
    had_client = manager.has_client(resolved_owner_id)
    try:
        await manager.bind_remote_async(
            owner_id=resolved_owner_id,
            profile=profile,
            endpoint_ref=endpoint_ref,
            resume_session_id=(binding.resume_session_id if binding is not None else ''),
        )
        if not had_client:
            state_store = getattr(context, 'state_store', None)
            persist = getattr(state_store, 'poll_runtime_control', None)
            if callable(persist):
                persist('process_owner_binding_attached')
    except Exception:
        if not had_client:
            await manager.close_owner_async(resolved_owner_id, drain=False)
            if binding is None:
                manager.bindings.remove(resolved_owner_id)
            else:
                manager.bindings.upsert(binding)
        raise
    return manager, resolved_owner_id


def _remote_execution_owner_refs(
    *,
    context: ToolExecutionContext,
    arguments: dict[str, Any],
    profile: RemoteProfile,
    artifact_root: str,
) -> tuple[str, str]:
    handle = context.execution_handle
    owner_id = str(getattr(handle, 'owner_id', '') or '').strip()
    process_id = str(getattr(handle, 'process_id', '') or '').strip()
    if owner_id and process_id:
        return owner_id, process_id
    state_store = getattr(context, 'state_store', None)
    state = getattr(state_store, 'state', None)
    registry = getattr(state, 'execution_registry', None)
    records = tuple(getattr(registry, 'records', ()) or ())
    matches = [
        record
        for record in records
        if getattr(record.handle, 'backend', '') == 'remote_workflow'
        and getattr(record.handle, 'profile', '') == profile.name
        and (
            not artifact_root
            or getattr(record.handle, 'artifact_root', '') == artifact_root
        )
    ]
    if len(matches) != 1:
        return '', ''
    handle = matches[0].handle
    return str(handle.owner_id or ''), str(handle.process_id or '')


def _remote_owner_id(
    *,
    context: ToolExecutionContext,
    profile: RemoteProfile,
) -> str:
    run_id = str(context.run_id or '').strip()
    if not run_id:
        metadata = getattr(getattr(context.state_store, 'state', None), 'run_metadata', None)
        run_id = str(getattr(metadata, 'run_id', '') or 'anonymous')
    return f'remote-{_safe_owner_token(profile.name)}-{_safe_owner_token(run_id)}'


def _remote_endpoint_ref(
    *,
    context: ToolExecutionContext,
    profile: RemoteProfile,
) -> RemoteEndpointRef:
    owner_id = _remote_owner_id(context=context, profile=profile)
    paths = run_scoped_paths(
        base_dir=(
            PurePosixPath(profile.remote_root)
            / '.ownpi'
            / 'process-owners'
        ),
        run_id=owner_id,
    )
    return endpoint_ref_for(
        profile_name=profile.name,
        manifest_path=paths.manifest_path,
    )


def _safe_owner_token(value: str) -> str:
    normalized = re.sub(r'[^A-Za-z0-9_.-]+', '-', str(value or '').strip())
    normalized = normalized.strip('-.') or 'anonymous'
    if len(normalized) <= 48:
        return normalized
    digest = hashlib.sha256(normalized.encode('utf-8')).hexdigest()[:12]
    return f'{normalized[:35]}-{digest}'


def _to_tool_result(
    context: ToolExecutionContext,
    *,
    tool_name: str,
    result: WorkflowResult,
    profile: RemoteProfile,
) -> ToolResult:
    data = dict(result.data)
    if tool_name == "remote_pull" and result.ok:
        data.update(_pulled_artifact_preview_data(data.get("local_destinations"), effective_permissions(context)))
    metadata = metadata_for_context(
        context,
        profile=profile.name,
        remote_root=profile.remote_root,
        local_root=str(profile.local_root),
        backend_command=result.command,
    )
    result_context_effects = data.get("context_effects")
    if isinstance(result_context_effects, dict):
        metadata["context_effects"] = dict(result_context_effects)
    for key in ("failure_class", "recommended_next_action", "recovery_hint"):
        value = data.get(key)
        if isinstance(value, str) and value.strip():
            metadata[key] = value.strip()
    return ToolResult(
        ok=result.ok,
        tool_name=tool_name,
        data=data,
        stdout=result.stdout or result.summary,
        stderr=result.stderr,
        exit_code=result.returncode,
        warnings=list(result.warnings),
        error=None if result.ok else result.summary,
        metadata=metadata,
    )


def _with_owner_refs(
    result: WorkflowResult,
    context: ToolExecutionContext,
) -> WorkflowResult:
    handle = context.execution_handle
    owner_id = str(getattr(handle, 'owner_id', '') or '').strip()
    process_id = str(getattr(handle, 'process_id', '') or '').strip()
    if not owner_id or not process_id:
        return result
    return replace(
        result,
        data={
            **dict(result.data),
            'owner_id': owner_id,
            'process_id': process_id,
        },
    )


def _remote_job_handle_payload(
    *,
    context: ToolExecutionContext,
    profile: RemoteProfile,
    session: str,
    artifact_root: str,
) -> dict[str, Any] | None:
    layout = _artifact_layout_from_context(
        context=context,
        artifact_root=artifact_root,
    )
    if layout is None:
        return None
    task_id = str(context.state_store.state.run_metadata.task_id or 'remote-job').strip()
    handle = build_execution_job_handle(
        job_id=f"{task_id}:{session or 'tmux'}",
        backend="remote_workflow",
        workdir=profile.remote_root,
        artifact_layout=layout,
        submitted_at=datetime.now(timezone.utc).isoformat(),
        remote_workdir=profile.remote_root,
        tmux_session=session,
    )
    return handle.to_dict()


def _artifact_layout_from_context(
    *,
    context: ToolExecutionContext,
    artifact_root: str = '',
) -> ExecutionArtifactLayout | None:
    root = str(artifact_root or '').strip()
    if not root:
        return None
    return ExecutionArtifactLayout.from_artifact_root(root)
