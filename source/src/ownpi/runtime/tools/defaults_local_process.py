from __future__ import annotations

from pathlib import Path
from typing import Any

from ownpi.runtime.permissions.action_compiler import OperationEffects
from ownpi.runtime.permissions.bash_policy import (
    analyze_argv_network_intent,
    analyze_bash_command,
    extract_network_intent,
)
from ownpi.runtime.permissions.models import FileSystemAccessMode
from ownpi.runtime.permissions.orchestrator import FileSystemAccessRequest
from ownpi.runtime.tools.definitions import ToolDefinition, ToolExposure


def _interactive_handler(handler_name: str):
    async def _run(context: Any, arguments: dict[str, Any]):
        from ownpi.tools import interactive_process
        from ownpi.runtime.execution.result_sink import publish_execution_tool_result

        result = await getattr(interactive_process, handler_name)(context, arguments)
        return publish_execution_tool_result(context, result)

    return _run


def _managed_execution_handler(handler_name: str):
    async def _run(context: Any, arguments: dict[str, Any]):
        from ownpi.tools import managed_execution
        from ownpi.runtime.execution.result_sink import publish_execution_tool_result

        result = await getattr(managed_execution, handler_name)(context, arguments)
        return publish_execution_tool_result(context, result)

    return _run


def build_local_process_tool_definitions() -> tuple[ToolDefinition, ...]:
    return (
        _exec_command_definition(),
        _write_stdin_definition(),
        _execution_monitor_definition(),
        _execution_logs_definition(),
        _execution_kill_definition(),
    )


def _validate_exec_command_input(
    _context: object,
    arguments: dict[str, object],
) -> dict[str, str] | None:
    command = str(arguments.get('cmd', '') or '').strip()
    program = str(arguments.get('program', '') or '').strip()
    if bool(command) == bool(program):
        return {
            'error': 'exec_command requires exactly one of `cmd` or `program`.',
            'compact_hint': 'Provide a shell command, or provide `program` with optional `args`.',
        }
    if program:
        args = arguments.get('args')
        if args is not None and (
            not isinstance(args, list)
            or any(not isinstance(item, str) for item in args)
        ):
            return {
                'error': 'exec_command `args` must be an array of strings.',
                'compact_hint': 'Pass command arguments as `args: ["..."]`.',
            }
    elif 'args' in arguments:
        return {
            'error': '`args` requires structured `program` mode; `cmd` already contains the shell command.',
            'compact_hint': 'Use `cmd` alone, or use `program` with `args`.',
        }
    if arguments.get('workdir') and arguments.get('cwd'):
        return {
            'error': 'exec_command accepts only one working-directory field: `workdir` or `cwd`.',
            'compact_hint': 'Use `workdir` (or the structured-mode alias `cwd`) once.',
        }
    if arguments.get('tty') is not None and arguments.get('use_pty') is not None:
        if bool(arguments.get('tty')) != bool(arguments.get('use_pty')):
            return {
                'error': '`tty` and `use_pty` must agree when both are provided.',
                'compact_hint': 'Set only one PTY switch, or give both the same value.',
            }
    mode = str(arguments.get('sandbox_permissions', 'use_default') or 'use_default')
    if mode != 'require_escalated':
        if arguments.get('prefix_rule') not in (None, []):
            return {
                'error': 'prefix_rule requires sandbox_permissions=require_escalated.',
                'compact_hint': 'Request escalation before proposing a reusable prefix.',
            }
        return None
    if not str(arguments.get('justification', '') or '').strip():
        return {
            'error': 'require_escalated requires a user-facing justification.',
            'compact_hint': 'Explain why execution outside the default sandbox is required.',
        }
    prefix = arguments.get('prefix_rule')
    if prefix is None:
        return None
    if not isinstance(prefix, list) or not prefix or any(
        not isinstance(item, str) or not item.strip() for item in prefix
    ):
        return {
            'error': 'prefix_rule must be a non-empty array of command tokens.',
            'compact_hint': 'Use a bounded token prefix such as ["git", "pull"].',
        }
    if program:
        raw_args = arguments.get('args', [])
        command_tokens = [program]
        if isinstance(raw_args, list):
            command_tokens.extend(str(item) for item in raw_args)
        commands = [command_tokens]
    else:
        analysis = analyze_bash_command(command)
        allowed_shell_constructs = {'redirection'}
        if (
            not analysis.commands
            or analysis.kind != 'simple'
            and not set(analysis.unknown_constructs).issubset(allowed_shell_constructs)
        ):
            return {
                'error': 'Cannot bind a reusable prefix to shell expansions or unparsed commands.',
                'compact_hint': 'Omit prefix_rule to request approval for this exact command.',
            }
        commands = [list(segment.argv) for segment in analysis.commands]
    normalized_prefix = [item.strip() for item in prefix]
    if any(tokens[: len(normalized_prefix)] != normalized_prefix for tokens in commands):
        return {
            'error': 'prefix_rule does not match every requested command prefix.',
            'compact_hint': 'Bind every command to the prefix, or omit prefix_rule for exact-command approval.',
        }
    return None


def _exec_command_definition() -> ToolDefinition:
    return ToolDefinition(
        exposure=ToolExposure.DIRECT,
        **_summary(
            name='exec_command',
            kind='interactive_process_exec',
            description=(
                'Purpose: Run local commands for inspection, implementation, testing, analysis, and other research work. '
                'Use this tool when the task requires executing a command in the local environment. '
                'Usage: provide either a shell command in `cmd`, or an executable in `program` with optional `args`. '
                'Use `workdir` to select the working directory. Use `yield_time_ms` to control the initial wait. '
                'If the command remains active, continue the returned session instead of starting it again. '
                'Do not provide both `cmd` and `program`, and do not provide `args` with `cmd`. '
                'Example: `{"cmd":"python -V","workdir":"."}`. '
                'Special rules: bare `python`, `python3`, `pip`, and `pytest` commands use the current managed Python environment. '
                'To use another environment, provide its exact interpreter path, for example '
                '`/path/to/conda/env/bin/python script.py`; explicit interpreter paths are preserved. '
                'Write cross-tool diagnostics under `$OWNPI_TEMP_ARTIFACT_ROOT`.'
            ),
            detail=(
                'Use the same entry for quick diagnostics and duration-uncertain experiments. '
                'A command that exceeds the initial wait returns a live session and durable execution identity; '
                'observe or continue that session without rerunning the command.'
            ),
            read_only=False,
            concurrency_safe=True,
            selection_disambiguators=(
                'Use for both quick local commands and duration-uncertain local executions.',
                'Let the yield result determine whether follow-up observation is needed.',
            ),
        ),
        input_schema_builder=lambda: {
            'type': 'object',
            'properties': {
                'cmd': {'type': 'string'},
                'program': {'type': 'string'},
                'args': {'type': 'array', 'items': {'type': 'string'}},
                'justification': {'type': 'string'},
                'login': {'type': 'boolean'},
                'max_output_tokens': {'type': 'integer'},
                'prefix_rule': {'type': 'array', 'items': {'type': 'string'}},
                'sandbox_permissions': {
                    'type': 'string',
                    'enum': ['use_default', 'require_escalated'],
                },
                'shell': {'type': 'string'},
                'tty': {'type': 'boolean'},
                'use_pty': {'type': 'boolean'},
                'pty_columns': {'type': 'integer'},
                'pty_rows': {'type': 'integer'},
                'cwd': {'type': 'string'},
                'workdir': {'type': 'string'},
                'yield_time_ms': {'type': 'integer'},
                'env_overrides': {'type': 'object'},
            },
            'required': [],
            'additionalProperties': False,
        },
        input_validator=_validate_exec_command_input,
        effect_builder=_resolve_exec_command_effects,
        handler=_interactive_handler('run_exec_command'),
        classifier_input_builder=lambda arguments: {
            'tool_name': 'exec_command',
            'command': _display_exec_command(arguments),
            'cwd': str(arguments.get('workdir') or arguments.get('cwd') or ''),
        },
        observation_family='interactive_process',
    )


def _display_exec_command(arguments: dict[str, Any]) -> str:
    command = str(arguments.get('cmd', '') or '').strip()
    if command:
        return command
    program = str(arguments.get('program', '') or '').strip()
    args = arguments.get('args', [])
    if not isinstance(args, list):
        return program
    return ' '.join([program, *(str(item) for item in args)]).strip()


def _write_stdin_definition() -> ToolDefinition:
    return ToolDefinition(
        exposure=ToolExposure.DIRECT,
        **_summary(
            name='write_stdin',
            kind='interactive_process_write',
            description='Write characters to an existing unified exec session and return recent output.',
            detail='Write to or poll a session_id, or use execution_id from the Execution Board after resume.',
            read_only=False,
            resume_strategy='session_identity_only',
        ),
        input_schema_builder=lambda: {
            'type': 'object',
            'properties': {
                'chars': {'type': 'string'},
                'max_output_tokens': {'type': 'integer'},
                'session_id': {'type': 'integer'},
                'execution_id': {'type': 'string'},
                'yield_time_ms': {'type': 'integer'},
            },
            'required': [],
            'additionalProperties': False,
        },
        handler=_interactive_handler('run_write_stdin'),
        observation_family='interactive_process',
    )


def _execution_monitor_definition() -> ToolDefinition:
    return ToolDefinition(
        exposure=ToolExposure.DEFERRED,
        **_summary(
            name='execution_monitor',
            kind='managed_execution_monitor',
            description='Get a status overview and recent output for a managed local execution.',
            detail='Use first with an execution_id from the Execution Board. Use execution_logs only for a longer or more detailed stdout/stderr tail.',
            read_only=True,
            selection_disambiguators=(
                'Use to inspect an existing execution_id from the Execution Board.',
            ),
        ),
        input_schema_builder=_execution_control_schema,
        effect_builder=_resolve_execution_monitor_effects,
        handler=_managed_execution_handler('run_execution_monitor'),
        observation_family='local_process_job',
    )


def _execution_logs_definition() -> ToolDefinition:
    return ToolDefinition(
        exposure=ToolExposure.DEFERRED,
        **_summary(
            name='execution_logs',
            kind='managed_execution_logs',
            description='Read a longer or more detailed stdout/stderr tail from a managed local execution.',
            detail='Use after execution_monitor when the overview output is insufficient or detailed log diagnosis is needed.',
            read_only=True,
            selection_disambiguators=(
                'Use to inspect durable logs for an existing execution_id.',
            ),
        ),
        input_schema_builder=lambda: {
            **_execution_control_schema(),
            'properties': {
                **_execution_control_schema()['properties'],
                'tail_chars': {'type': 'integer'},
            },
        },
        effect_builder=_resolve_execution_logs_effects,
        handler=_managed_execution_handler('run_execution_logs'),
        observation_family='local_process_logs',
    )


def _execution_kill_definition() -> ToolDefinition:
    return ToolDefinition(
        exposure=ToolExposure.DEFERRED,
        **_summary(
            name='execution_kill',
            kind='managed_execution_kill',
            description='Terminate one managed local execution by execution_id.',
            detail='Terminate the exact Process Owner process recorded on the Execution Board.',
            read_only=False,
            destructive_capable=True,
            selection_disambiguators=(
                'Use only to terminate an existing execution_id.',
            ),
        ),
        input_schema_builder=_execution_control_schema,
        effect_builder=_resolve_execution_kill_effects,
        handler=_managed_execution_handler('run_execution_kill'),
        observation_family='local_process_job',
        destructive_kinds=('process_termination',),
    )


def _execution_control_schema() -> dict[str, Any]:
    return {
        'type': 'object',
        'properties': {
            'execution_id': {'type': 'string'},
            'wait_ms': {'type': 'integer'},
        },
        'required': ['execution_id'],
        'additionalProperties': False,
    }


def _summary(
    *,
    name: str,
    kind: str,
    description: str,
    detail: str,
    read_only: bool,
    concurrency_safe: bool = False,
    destructive_capable: bool = False,
    resume_strategy: str = 'execution_reconciliation',
    selection_disambiguators: tuple[str, ...] | None = None,
) -> dict[str, Any]:
    return {
        'name': name,
        'kind': kind,
        'description': description,
        'detail': detail,
        'search_hint': detail,
        'search_keywords': ('local', 'job', 'long-running', 'training', 'process', 'monitor'),
        'read_only': read_only,
        'concurrency_safe': concurrency_safe,
        'permission_class': 'execution',
        'invocation_mode': 'agent_requested',
        'result_family': 'execution_job',
        'requires_network': False,
        'writes_workspace': not read_only,
        'destructive_capable': destructive_capable,
        'toolset_family': 'local_process',
        'backend_kind': 'local_exec',
        'environment_affinity': 'host_required',
        'execution_class': 'command_exec',
        'side_effect_level': 'local_execution' if not read_only else 'read',
        'isolation_requirement': 'host_local',
        'credential_mode': 'none',
        'resume_strategy': resume_strategy,
        'action_primitives': ('execute_local',) if not read_only else ('inspect_local',),
        'primary_intent': 'execute_local' if not read_only else 'inspect_local',
        'grounding_lane': 'grounded',
        'input_contract_family': 'local_process_job',
        'selection_disambiguators': selection_disambiguators
        or (
            'Use when the task needs a managed detached local process.',
            'Use for long-running training, preprocessing, or full-grid batch runs.',
        ),
    }


def _resolve_exec_command_effects(context: Any, arguments: dict[str, Any]) -> OperationEffects:
    del context
    shell_command = str(arguments.get('cmd', '') or '').strip()
    program = str(arguments.get('program', '') or '').strip()
    if shell_command:
        intent = extract_network_intent(analyze_bash_command(shell_command))
        process_argv = (shell_command,)
    elif program:
        process_argv = (program, *(str(item) for item in arguments.get('args', [])))
        intent = analyze_argv_network_intent(process_argv)
    else:
        intent = analyze_argv_network_intent(())
        process_argv = ()
    return OperationEffects.declared(
        process_required=True,
        process_argv=process_argv,
        network_required=intent.requires_network,
        network_endpoints=tuple(intent.hosts),
        source='interactive_command_process',
    )


def _resolve_execution_monitor_effects(
    _context: Any,
    _arguments: dict[str, Any],
) -> OperationEffects:
    return OperationEffects.declared(
        process_required=True,
        source='managed_execution_monitor',
    )


def _resolve_execution_logs_effects(
    context: Any,
    arguments: dict[str, Any],
) -> OperationEffects:
    record = _execution_record(context, arguments)
    refs = () if record is None else (
        record.handle.stdout_ref,
        record.handle.stderr_ref,
    )
    return OperationEffects.declared(
        file_system_accesses=tuple(
            FileSystemAccessRequest(
                Path(ref).expanduser().resolve(),
                FileSystemAccessMode.READ,
            )
            for ref in refs
            if ref
        ),
        source='managed_execution_logs',
    )


def _resolve_execution_kill_effects(
    _context: Any,
    _arguments: dict[str, Any],
) -> OperationEffects:
    return OperationEffects.declared(
        process_required=True,
        source='managed_execution_kill',
    )


def _execution_record(context: Any, arguments: dict[str, Any]) -> Any | None:
    execution_id = str(arguments.get('execution_id', '') or '').strip()
    registry = getattr(
        getattr(getattr(context, 'state_store', None), 'state', None),
        'execution_registry',
        None,
    )
    service = context.execution_runtime_service
    if service is not None:
        registry = service.registry_snapshot()
    if registry is None or not execution_id:
        return None
    return registry.get(execution_id)


def _resolve_effect_path(context: Any, raw: str) -> Path:
    path = Path(raw).expanduser()
    if not path.is_absolute():
        path = Path(context.project_root) / path
    return path.resolve()
