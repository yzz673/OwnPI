from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from typing import Any

from ownpi.runtime.tools.definitions import ToolDefinition, ToolExposure
from ownpi.runtime.tools.effects import resolve_remote_effects


def bind_remote_tool_environment(
    definition: ToolDefinition, *, active_profile: str,
) -> ToolDefinition:
    if definition.name not in {
        'remote_check', 'remote_resources', 'remote_deploy', 'remote_run',
        'remote_monitor', 'remote_logs', 'remote_pull',
    }:
        return definition
    schema = deepcopy(definition.tool_abi().input_schema)
    properties = schema.get('properties', {})
    if active_profile:
        properties.pop('profile', None)
    if definition.name == 'remote_run':
        properties.pop('tmux_session', None)
    schema['required'] = [key for key in schema.get('required', []) if key in properties]
    exposure = definition.exposure
    if active_profile and definition.name in {'remote_monitor', 'remote_logs', 'remote_pull'}:
        exposure = ToolExposure.DIRECT
    return replace(definition, exposure=exposure, input_schema_builder=lambda: deepcopy(schema))


def _remote_handler(handler_name: str):
    async def _handler(context: Any, arguments: dict[str, Any]):
        from ownpi.tools import remote_workflow
        from ownpi.runtime.execution.result_sink import publish_execution_tool_result

        result = await getattr(remote_workflow, handler_name)(context, arguments)
        return publish_execution_tool_result(context, result)

    return _handler


def build_remote_tool_definitions() -> tuple[ToolDefinition, ...]:
    return (
        _remote_profile_list_definition(),
        _remote_profile_show_definition(),
        _remote_check_definition(),
        _remote_resources_definition(),
        _remote_deploy_definition(),
        _remote_run_definition(),
        _remote_monitor_definition(),
        _remote_logs_definition(),
        _remote_pull_definition(),
    )


def _remote_summary(
    *,
    name: str,
    kind: str,
    description: str,
    detail: str,
    search_keywords: tuple[str, ...],
    permission_class: str,
    read_only: bool,
    requires_network: bool = True,
    writes_workspace: bool = False,
    destructive_capable: bool = False,
    action_primitives: tuple[str, ...] = (),
    primary_intent: str = '',
    input_contract_family: str = '',
    selection_disambiguators: tuple[str, ...] = (),
    preferred_predecessors: tuple[str, ...] = (),
    preferred_successors: tuple[str, ...] = (),
) -> dict[str, Any]:
    return {
        'name': name,
        'kind': kind,
        'description': description,
        'detail': detail,
        'search_hint': detail,
        'search_keywords': search_keywords,
        'read_only': read_only,
        'concurrency_safe': False,
        'permission_class': permission_class,
        'invocation_mode': 'agent_requested',
        'result_family': 'remote_workflow',
        'requires_network': requires_network,
        'writes_workspace': writes_workspace,
        'destructive_capable': destructive_capable,
        'toolset_family': 'remote_workflow',
        'backend_kind': 'ssh',
        'environment_affinity': 'remote_required' if requires_network else 'host_required',
        'execution_class': 'remote_command' if not read_only else 'read_only',
        'side_effect_level': 'remote_execution' if not read_only else 'network_read',
        'isolation_requirement': 'host_network',
        'credential_mode': 'ssh_config_or_agent',
        'resume_strategy': 'stateless',
        'action_primitives': action_primitives,
        'primary_intent': primary_intent,
        'grounding_lane': 'grounded' if action_primitives else 'open',
        'input_contract_family': input_contract_family,
        'selection_disambiguators': selection_disambiguators,
        'preferred_predecessors': preferred_predecessors,
        'preferred_successors': preferred_successors,
        'effect_builder': lambda context, arguments: resolve_remote_effects(
            context, arguments, operation=name.removeprefix('remote_'),
        ),
    }


def _common_profile_properties(*, include_config_dir: bool = True) -> dict[str, dict[str, str]]:
    properties: dict[str, dict[str, str]] = {
        'profile': {'type': 'string'},
    }
    if include_config_dir:
        properties['config_dir'] = {'type': 'string'}
    return properties


def _execution_profile_properties() -> dict[str, dict[str, str]]:
    return {'profile': {'type': 'string'}}


def _remote_profile_list_definition() -> ToolDefinition:
    return ToolDefinition(
        exposure=ToolExposure.DEFERRED,
        **_remote_summary(
            name='remote_profile_list',
            kind='remote_profile',
            description='List configured remote workflow profiles.',
            detail='List local remote workflow profiles without contacting the remote server.',
            search_keywords=('remote', 'profile', 'ssh', 'server', 'list'),
            permission_class='inspection',
            read_only=True,
            requires_network=False,
        ),
        detail_builder=lambda: {
            'default_input_examples': [{}, {'config_dir': '~/.config/ownpi/remote-workflow'}],
            'result_highlights': ['profiles', 'config_dir'],
        },
        input_schema_builder=lambda: {
            'type': 'object',
            'properties': {'config_dir': {'type': 'string'}},
            'additionalProperties': False,
        },
        handler=_remote_handler('run_remote_profile_list'),
        observation_family='remote_profile',
    )


def _remote_profile_show_definition() -> ToolDefinition:
    return ToolDefinition(
        exposure=ToolExposure.DEFERRED,
        **_remote_summary(
            name='remote_profile_show',
            kind='remote_profile',
            description='Show one configured remote workflow profile.',
            detail='Inspect one local remote workflow profile, including default remote execution environment.',
            search_keywords=('remote', 'profile', 'ssh', 'server', 'show', 'environment'),
            permission_class='inspection',
            read_only=True,
            requires_network=False,
        ),
        detail_builder=lambda: {
            'default_input_examples': [{'profile': 'server_project'}],
            'result_highlights': ['profile', 'host', 'remote_root', 'conda_env'],
        },
        input_schema_builder=lambda: {
            'type': 'object',
            'properties': _common_profile_properties(),
            'required': ['profile'],
            'additionalProperties': False,
        },
        handler=_remote_handler('run_remote_profile_show'),
        observation_family='remote_profile',
    )


def _remote_check_definition() -> ToolDefinition:
    return ToolDefinition(
        exposure=ToolExposure.DEFERRED,
        **_remote_summary(
            name='remote_check',
            kind='remote_check',
            description='Check SSH reachability and configured remote execution environment.',
            detail=(
                'Check SSH, remote root, python, tmux, rsync, and profile conda_env activation. '
                'This does not validate or create the task-local local_workdir; prepare it with exec_command.'
            ),
            search_keywords=('remote', 'ssh', 'check', 'server', 'conda', 'environment'),
            permission_class='network',
            read_only=True,
        ),
        detail_builder=lambda: {
            'default_input_examples': [{'profile': 'server_project'}],
            'result_highlights': ['remote_probe', 'conda_env_status', 'conda_env_python'],
        },
        input_schema_builder=lambda: {
            'type': 'object',
            'properties': _execution_profile_properties(),
            'required': ['profile'],
            'additionalProperties': False,
        },
        handler=_remote_handler('run_remote_check'),
        observation_family='remote_check',
    )


def _remote_resources_definition() -> ToolDefinition:
    return ToolDefinition(
        exposure=ToolExposure.DEFERRED,
        **_remote_summary(
            name='remote_resources',
            kind='remote_resources',
            description='Inspect remote GPU, CPU, memory, disk, and tmux resource state.',
            detail=(
                'Read remote resource status before launching experiments. Reports nvidia-smi GPU memory, '
                'utilization, compute processes, system memory, disk, tmux sessions, and a recommended GPU.'
            ),
            search_keywords=('remote', 'gpu', 'nvidia-smi', 'resource', 'memory', 'oom', 'cuda'),
            permission_class='network',
            read_only=True,
        ),
        detail_builder=lambda: {
            'default_input_examples': [{'profile': 'server_project'}],
            'result_highlights': ['gpus', 'recommended_gpu', 'memory', 'disk', 'tmux_sessions'],
        },
        input_schema_builder=lambda: {
            'type': 'object',
            'properties': _execution_profile_properties(),
            'required': ['profile'],
            'additionalProperties': False,
        },
        handler=_remote_handler('run_remote_resources'),
        observation_family='remote_resources',
    )


def _remote_deploy_definition() -> ToolDefinition:
    return ToolDefinition(
        exposure=ToolExposure.DEFERRED,
        **_remote_summary(
            name='remote_deploy',
            kind='remote_sync',
            description='Rsync already-created local files/directories to the configured remote project root.',
            detail=(
                'Use rsync over SSH to deploy selected local project paths or the full project to remote. '
                'This tool does not create local files; prepare them with exec_command or patch_apply, optionally validate with exec_command '
                'and wait for terminal success (a yielded session is not completion), '
                'then deploy the resulting local script/config before remote_run.'
            ),
            search_keywords=('remote', 'deploy', 'rsync', 'sync', 'server', 'script', 'config'),
            permission_class='execution',
            read_only=False,
            writes_workspace=False,
            destructive_capable=True,
            action_primitives=('execute_remote',),
            primary_intent='execute_remote',
            input_contract_family='remote_profile_paths',
            selection_disambiguators=(
                'Use after local files/scripts/configs already exist; this only syncs them to the remote root.',
                'Use remote_run after deploying runnable code/config.',
            ),
            preferred_predecessors=('patch_apply', 'exec_command'),
            preferred_successors=('remote_run',),
        ),
        detail_builder=lambda: {
            'default_input_examples': [
                {'profile': 'server_project', 'paths': ['src', 'pyproject.toml']},
                {'profile': 'server_project', 'paths': ['scripts/qc_intake.py', 'configs/qc.yaml']},
                {'profile': 'server_project', 'all': True, 'dry_run': True},
            ],
            'result_highlights': ['paths', 'dry_run', 'delete'],
        },
        input_schema_builder=lambda: {
            'type': 'object',
            'properties': {
                **_execution_profile_properties(),
                'paths': {'type': 'array', 'items': {'type': 'string'}},
                'all': {'type': 'boolean'},
                'delete': {'type': 'boolean'},
                'dry_run': {'type': 'boolean'},
            },
            'required': ['profile'],
            'additionalProperties': False,
        },
        handler=_remote_handler('run_remote_deploy'),
        observation_family='remote_sync',
        destructive_kinds=('remote_delete_with_rsync',),
    )


def _remote_run_definition() -> ToolDefinition:
    return ToolDefinition(
        exposure=ToolExposure.DEFERRED,
        **_remote_summary(
            name='remote_run',
            kind='remote_execution',
            description='Start a detached owner-managed remote command after code/configs are ready remotely.',
            detail=(
                'Start an owner-managed remote process under the profile remote root using the profile conda_env by default. '
                'The tmux_session input is retained as a stable logical job label; it does not identify or control a tmux process. '
                'Default usage is local-first: run a script/config that was already written locally and deployed with remote_deploy. '
                'Keep inline commands short for probes/status checks; avoid embedding long source code directly in remote_run. '
                'For execution jobs with an artifact_root, the deployed script must write '
                '$OWNPI_MANIFEST_PATH and $OWNPI_STATUS_PATH, plus downstream-readable outputs under '
                '$OWNPI_LIGHT_ARTIFACT_DIR. '
                'After remote_run, prefer remote_monitor with the same logical job label and artifact_root to classify '
                'job state before final synthesis. remote_pull may still be used for intermediate artifact snapshots.'
            ),
            search_keywords=('remote', 'run', 'ssh', 'experiment', 'training', 'artifact', 'deployed'),
            permission_class='execution',
            read_only=False,
            destructive_capable=True,
            action_primitives=('execute_remote',),
            primary_intent='execute_remote',
            input_contract_family='remote_profile_command_session',
            selection_disambiguators=(
                'Use to start a detached remote job after local-first code has been deployed.',
                'Use remote_monitor to poll job state after this returns.',
            ),
            preferred_predecessors=('remote_deploy',),
            preferred_successors=('remote_monitor',),
        ),
        detail_builder=lambda: {
            'default_input_examples': [
                {
                    'profile': 'server_project',
                    'tmux_session': 'train_exp_001',
                    'command': 'python train.py',
                    'artifact_root': '/remote/artifacts/train_exp_001',
                },
                {
                    'profile': 'server_project',
                    'tmux_session': 'qc_intake',
                    'command': 'python scripts/qc_intake.py --config configs/qc.yaml',
                    'artifact_root': '/remote/artifacts/qc_intake',
                },
                {
                    'profile': 'server_project',
                    'tmux_session': 'probe',
                    'command': 'python3 -V',
                    'artifact_root': '/remote/artifacts/probe',
                    'env': '',
                },
            ],
            'result_highlights': ['tmux_session', 'remote_root'],
        },
        input_schema_builder=lambda: {
            'type': 'object',
            'properties': {
                **_execution_profile_properties(),
                'command': {'type': 'string'},
                'tmux_session': {'type': 'string'},
                'artifact_root': {'type': 'string'},
                'env': {'type': 'string'},
            },
            'required': ['profile', 'command', 'tmux_session', 'artifact_root'],
            'additionalProperties': False,
        },
        handler=_remote_handler('run_remote_run'),
        observation_family='remote_execution',
        destructive_kinds=('remote_session_replace',),
    )


def _remote_logs_definition() -> ToolDefinition:
    return ToolDefinition(
        exposure=ToolExposure.DEFERRED,
        **_remote_summary(
            name='remote_logs',
            kind='remote_read',
            description='Read a bounded tail of a remote log file.',
            detail=(
                'Read a bounded tail from a remote file. Relative paths are resolved under profile remote_root; '
                'absolute paths are allowed for task-provided external artifact roots. '
                'Use this for remote previews only. If the file is a small report/result needed by later stages, '
                'call remote_pull next and read the local mirror instead of using local file_reader on the remote path.'
            ),
            search_keywords=('remote', 'logs', 'tail', 'tmux', 'debug', 'artifact'),
            permission_class='network',
            read_only=True,
        ),
        detail_builder=lambda: {
            'default_input_examples': [
                {'profile': 'server_project', 'file': 'logs/train.log', 'tail': 100},
                {
                    'profile': 'server_project',
                    'file': '/remote/artifact/root/qc_summary.md',
                    'tail': 200,
                },
            ],
            'result_highlights': ['file', 'tail'],
        },
        input_schema_builder=lambda: {
            'type': 'object',
            'properties': {
                **_execution_profile_properties(),
                'file': {'type': 'string'},
                'tail': {'type': 'integer'},
            },
            'required': ['profile', 'file'],
            'additionalProperties': False,
        },
        handler=_remote_handler('run_remote_logs'),
        observation_family='remote_log',
    )


def _remote_monitor_definition() -> ToolDefinition:
    return ToolDefinition(
        exposure=ToolExposure.DEFERRED,
        **_remote_summary(
            name='remote_monitor',
            kind='remote_read',
            description='Poll a remote execution job and its standard artifact contract.',
            detail=(
                'Poll one bounded remote job state without assuming completion from remote_run. Checks process-owner state, '
                'standard artifact_root/light manifest/status files, logs/run.log tail, light artifact files, and common '
                'failure patterns such as Traceback, CUDA OOM, Killed, and disk full. Prefer this after remote_run when '
                'you need job-state evidence before final synthesis. If recommended_next_action is fix_artifact_layout, update the deployed script so completed '
                'jobs write light/manifest.json and light/status.json before finalizing. If recommended_next_action is '
                'pull_light_artifacts, pull artifact_root/light.'
            ),
            search_keywords=('remote', 'monitor', 'poll', 'artifact', 'status', 'manifest', 'oom'),
            permission_class='network',
            read_only=True,
            action_primitives=('monitor_job',),
            primary_intent='monitor_job',
            input_contract_family='remote_profile_session_artifact_root',
            selection_disambiguators=(
                'Use after remote_run to poll status; do not use it to pull completed artifacts.',
                'When terminal completed evidence recommends pull_light_artifacts, use remote_pull next.',
            ),
            preferred_predecessors=('remote_run',),
            preferred_successors=('remote_pull',),
        ),
        detail_builder=lambda: {
            'default_input_examples': [
                {'profile': 'server_project', 'tmux_session': 'train_exp_001'},
                {'profile': 'server_project', 'tmux_session': 'qc_intake', 'tail': 200},
                {
                    'profile': 'server_project',
                    'tmux_session': 'qc_intake',
                    'artifact_root': '/remote/artifact/root',
                },
            ],
            'result_highlights': ['monitor.job_state', 'monitor.recommended_next_action', 'light_artifacts'],
        },
        input_schema_builder=lambda: {
            'type': 'object',
            'properties': {
                **_execution_profile_properties(),
                'tmux_session': {'type': 'string'},
                'session': {'type': 'string'},
                'artifact_root': {'type': 'string'},
                'tail': {'type': 'integer'},
            },
            'required': ['profile'],
            'additionalProperties': False,
        },
        handler=_remote_handler('run_remote_monitor'),
        input_validator=_validate_remote_monitor_arguments,
        observation_family='remote_job_monitor',
    )


def _remote_pull_definition() -> ToolDefinition:
    return ToolDefinition(
        exposure=ToolExposure.DEFERRED,
        **_remote_summary(
            name='remote_pull',
            kind='remote_sync',
            description='Pull remote result paths back into the local project root.',
            detail=(
                'Use rsync over SSH to pull selected remote result/report artifacts back to the local root. '
                'Relative paths are resolved under profile remote_root. Absolute task artifact paths are allowed and mirrored under '
                '`local_root/remote_artifacts/...`, which is the preferred way to make small remote reports/results available for local reading. '
                'For execution jobs that follow the standard artifact contract, prefer artifact_scope="light" to pull the whole '
                'artifact_root/light directory incrementally before downstream local file_reader/artifact packaging.'
            ),
            search_keywords=('remote', 'pull', 'rsync', 'outputs', 'results', 'artifact'),
            permission_class='execution',
            read_only=False,
            writes_workspace=True,
            destructive_capable=True,
            action_primitives=('fetch_artifact',),
            primary_intent='fetch_artifact',
            input_contract_family='remote_profile_paths_or_artifact_scope',
            selection_disambiguators=(
                'Use to mirror light artifacts locally for inspection or downstream local reading.',
                'If the producing job may still be running, treat the pull as a snapshot and use remote_monitor/logs before final synthesis.',
            ),
            preferred_predecessors=('remote_monitor',),
        ),
        detail_builder=lambda: {
            'default_input_examples': [
                {'profile': 'server_project', 'paths': ['outputs/exp_001']},
                {'profile': 'server_project', 'paths': ['/remote/artifact/root/qc_summary.md']},
                {'profile': 'server_project', 'artifact_scope': 'light'},
            ],
            'result_highlights': ['paths', 'local_destinations', 'dry_run', 'delete'],
        },
        input_schema_builder=lambda: {
            'type': 'object',
            'properties': {
                **_execution_profile_properties(),
                'paths': {'type': 'array', 'items': {'type': 'string'}},
                'artifact_scope': {'type': 'string'},
                'artifact_root': {'type': 'string'},
                'all': {'type': 'boolean'},
                'delete': {'type': 'boolean'},
                'dry_run': {'type': 'boolean'},
            },
            'required': ['profile'],
            'additionalProperties': False,
        },
        handler=_remote_handler('run_remote_pull'),
        input_validator=_validate_remote_pull_arguments,
        observation_family='remote_sync',
        destructive_kinds=('local_delete_with_rsync',),
    )


def _validate_remote_monitor_arguments(_context: Any, arguments: dict[str, Any]) -> dict[str, Any] | None:
    has_deploy_like_input = bool(arguments.get('paths') or arguments.get('path') or arguments.get('all'))
    if has_deploy_like_input:
        return {
            'error': 'remote_monitor received deploy-like input',
            'compact_hint': (
                'remote_monitor only polls a previously started tmux job. This input looks like a deploy action; '
                'call remote_deploy with profile and paths/all first, then remote_run, then remote_monitor.'
            ),
            'validation_code': 'remote_monitor_deploy_like_input',
            'default_input_examples': [
                {'profile': 'server_project', 'paths': ['runtime_handoff_probe.py']},
                {'profile': 'server_project', 'tmux_session': 'handoff_probe', 'artifact_root': '/remote/artifact/root'},
            ],
            'tool_call_recovery': {
                'policy': 'auto_correct_single_safe_candidate',
                'confidence': 'deterministic',
                'tool_name': 'remote_deploy',
                'inherit_input_keys': ['profile', 'paths', 'path', 'all', 'delete', 'dry_run'],
                'blocked_input_keys': ['tmux_session', 'session', 'artifact_root', 'tail', 'artifact_scope'],
                'reason': (
                    'The current action has deploy-shaped inputs but selected remote_monitor. Use remote_deploy '
                    'to sync local-first code before launching or monitoring a remote job.'
                ),
                'expected_output': 'Deployment confirmation showing selected local paths synced to the remote root.',
            },
        }
    return None


def _validate_remote_pull_arguments(context: Any, arguments: dict[str, Any]) -> dict[str, Any] | None:
    return None
