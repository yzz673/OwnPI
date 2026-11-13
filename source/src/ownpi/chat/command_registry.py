from __future__ import annotations

from dataclasses import dataclass, field, replace
from pathlib import Path
import shlex
from typing import Any, Callable, Literal

from ownpi.chat.command_attachments import (
    append_command_attachments,
    build_command_attachments,
    command_attachment_messages,
)
from ownpi.chat.workflow_commands import WorkflowCommandResult, handle_workflow_command
from ownpi.schemas.runtime import UserConfig


CommandKind = Literal['local', 'workflow', 'prompt', 'skill', 'mcp']
CommandHandler = Callable[['CommandExecutionContext', str], 'CommandResult']
CommandAvailable = Callable[['CommandExecutionContext'], bool]
WorkflowCommandHandler = Callable[..., WorkflowCommandResult | None]


@dataclass(frozen=True)
class CommandDefinition:
    name: str
    description: str
    source: str = 'builtin'
    kind: CommandKind = 'local'
    aliases: tuple[str, ...] = ()
    allowed_tools: tuple[str, ...] = ()
    model: str | None = None
    reasoning_effort: str | None = None
    argument_hint: str | None = None
    user_invocable: bool = True
    model_invocation_enabled: bool = True
    trust_level: str = 'unknown'
    plugin_name: str | None = None
    hook_names: tuple[str, ...] = ()
    context_resources: tuple[str, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)
    available: CommandAvailable | None = None
    execute: CommandHandler | None = None

    def matches(self, name: str) -> bool:
        normalized = str(name or '').strip()
        return normalized == self.name or normalized in self.aliases

    def with_execute(self, execute: CommandHandler | None) -> 'CommandDefinition':
        return replace(self, execute=execute)


@dataclass
class CommandExecutionContext:
    project_root: Path
    user_config: UserConfig | None = None
    history: list[Any] = field(default_factory=list)
    mcp_elicitation_handler: Callable[[Any], Any] | None = None
    hook_engine: Any | None = None
    state_store: Any | None = None
    app_paths: Any | None = None
    mcp_resource_cache: dict[str, dict[str, Any]] = field(default_factory=dict)
    state: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class CommandResult:
    handled: bool
    message: str = ''
    prompt: str | None = None
    exit_code: int | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


class CommandRegistry:
    def __init__(self, definitions: list[CommandDefinition] | tuple[CommandDefinition, ...] = ()) -> None:
        self._definitions: dict[str, CommandDefinition] = {}
        for definition in definitions:
            self.register(definition)

    def register(self, definition: CommandDefinition, *, replace: bool = False) -> None:
        keys = self._definition_keys(definition)
        duplicate_keys = sorted({key for key in keys if keys.count(key) > 1})
        if duplicate_keys:
            raise ValueError(f'Command definition contains duplicate names or aliases: {", ".join(duplicate_keys)}')
        collisions = [(key, existing) for key in keys if (existing := self._definitions.get(key)) is not None]
        if collisions and not replace:
            details = ', '.join(f'{key} -> {existing.name}' for key, existing in collisions)
            raise ValueError(f'Command registration collision: {details}')
        if replace:
            existing = self._definitions.get(definition.name)
            if existing is not None:
                for key in self._definition_keys(existing):
                    if self._definitions.get(key) is existing:
                        del self._definitions[key]
        self._definitions[definition.name] = definition
        for alias in definition.aliases:
            self._definitions[alias] = definition

    def attach_handler(self, name: str, execute: CommandHandler, *, replace: bool = True) -> None:
        definition = self.get(name)
        if definition is None:
            raise KeyError(f'Unknown command: {name}')
        self.register(definition.with_execute(execute), replace=replace)

    def unregister(self, name: str) -> CommandDefinition | None:
        definition = self.get(name)
        if definition is None:
            return None
        for key in self._definition_keys(definition):
            if self._definitions.get(key) is definition:
                del self._definitions[key]
        return definition

    def unregister_by_source_prefix(self, source_prefix: str) -> tuple[CommandDefinition, ...]:
        removed: list[CommandDefinition] = []
        for definition in self.definitions():
            if not definition.source.startswith(source_prefix):
                continue
            if self.unregister(definition.name) is not None:
                removed.append(definition)
        return tuple(removed)

    def get(self, name: str) -> CommandDefinition | None:
        return self._definitions.get(str(name or '').strip())

    def definitions(self) -> tuple[CommandDefinition, ...]:
        seen: set[str] = set()
        items: list[CommandDefinition] = []
        for definition in self._definitions.values():
            if definition.name in seen:
                continue
            seen.add(definition.name)
            items.append(definition)
        return tuple(items)

    def visible_definitions(self, context: CommandExecutionContext | None = None) -> tuple[CommandDefinition, ...]:
        items: list[CommandDefinition] = []
        for definition in self.definitions():
            if not definition.user_invocable:
                continue
            if context is not None and definition.available is not None and not definition.available(context):
                continue
            items.append(definition)
        return tuple(items)

    def execute(self, message: str, context: CommandExecutionContext) -> CommandResult:
        command_name = parse_command_name(message)
        if not command_name:
            return CommandResult(handled=False)
        definition = self.get(command_name)
        if definition is None or definition.execute is None:
            return CommandResult(handled=False)
        if not definition.user_invocable:
            return CommandResult(
                handled=True,
                message=f'Command is not user-invocable: {command_name}',
                metadata={
                    'source': 'command_registry',
                    'command_name': command_name,
                    'user_invocable': False,
                },
            )
        if definition.available is not None and not definition.available(context):
            return CommandResult(handled=True, message=f'Command is not available: {command_name}')
        result = definition.execute(context, message)
        return _command_result_with_attachments(result, context=context, message=message)

    @staticmethod
    def _definition_keys(definition: CommandDefinition) -> list[str]:
        return [definition.name, *definition.aliases]


def parse_command_name(message: str) -> str | None:
    if not str(message or '').startswith('/'):
        return None
    try:
        tokens = shlex.split(message)
    except ValueError:
        tokens = str(message or '').split()
    if not tokens:
        return None
    return tokens[0]


def _command_result_with_attachments(
    result: CommandResult,
    *,
    context: CommandExecutionContext,
    message: str,
) -> CommandResult:
    if not result.handled or result.prompt is None:
        return result
    attachment_text = '\n'.join([message, result.prompt or ''])
    attachments = build_command_attachments(
        attachment_text,
        mcp_resource_cache=context.mcp_resource_cache,
    )
    if not attachments:
        return result
    metadata = dict(result.metadata)
    metadata['command_attachments'] = [dict(item) for item in attachments]
    metadata['reserved_command_attachment_messages'] = command_attachment_messages(attachments)
    metadata['command_attachment_message_effect'] = 'reserved_metadata'
    return CommandResult(
        handled=result.handled,
        message=result.message,
        prompt=append_command_attachments(result.prompt, attachments),
        exit_code=result.exit_code,
        metadata=metadata,
    )


def build_default_command_registry(
    *,
    workflow_command_handler: WorkflowCommandHandler = handle_workflow_command,
) -> CommandRegistry:
    definitions = [
        CommandDefinition(name='/help', description='Show this help message'),
        CommandDefinition(name='/exit', description='Exit the chat'),
        CommandDefinition(name='/last', description='Show the last interpreted task'),
        CommandDefinition(name='/status', description='Show current provider/model/profile status'),
        CommandDefinition(
            name='/runtime-profile',
            description='Show or select the Runtime profile for subsequent new runs',
        ),
        CommandDefinition(name='/runs', description='List recent workflow runs from the runtime output directory'),
        CommandDefinition(name='/memory', description='Show, reload, or edit OwnPI instruction memory'),
        CommandDefinition(name='/resume-run', description='Resume a workflow run from its latest durable recovery point'),
        CommandDefinition(name='/clarify-run', description='Resume a run with pending human clarification'),
        CommandDefinition(name='/cancel-clarify', description='Cancel local clarify capture'),
        CommandDefinition(name='/permission-run', description='Resume a run with pending tool permission'),
        CommandDefinition(name='/cancel-permission', description='Cancel local permission capture'),
        CommandDefinition(name='/permissions', description='Explain active permission rules and sandbox status'),
        _workflow_definition('/run-task', 'Run a task spec YAML directly', workflow_command_handler),
        _workflow_definition('/summarize-pdf', 'Create and run a paper_summarization task from a PDF', workflow_command_handler),
        _workflow_definition('/review-task', 'Create and run a literature-review task from a command', workflow_command_handler),
        CommandDefinition(name='/skills', description='List skills or enable/disable installed skills'),
        CommandDefinition(name='/commands', description='List available slash commands'),
        CommandDefinition(name='/extensions', description='Show or reload discovered skills, commands, hooks, and plugins'),
        CommandDefinition(name='/hooks', description='Show runtime hook status grouped by source and event'),
        CommandDefinition(name='/model', description='Pick model first, then reasoning, or switch model/profile'),
        CommandDefinition(name='/models', description='List configured LLM profiles and candidate models'),
        CommandDefinition(name='/reasoning', description='Open reasoning picker or switch reasoning effort'),
        CommandDefinition(name='/reasonings', description='List candidate reasoning efforts'),
        CommandDefinition(name='/mode', description='Switch UI mode: user or developer'),
    ]
    return CommandRegistry(definitions)


def slash_command_summaries(registry: CommandRegistry | None = None) -> tuple[tuple[str, str], ...]:
    active_registry = registry or build_default_command_registry()
    return tuple((definition.name, definition.description) for definition in active_registry.visible_definitions())


def _workflow_definition(
    name: str,
    description: str,
    workflow_command_handler: WorkflowCommandHandler,
) -> CommandDefinition:
    return CommandDefinition(
        name=name,
        description=description,
        source='builtin.workflow',
        kind='workflow',
        execute=lambda context, message: _execute_workflow_command(
            context,
            message,
            workflow_command_handler=workflow_command_handler,
        ),
    )


def _execute_workflow_command(
    context: CommandExecutionContext,
    message: str,
    *,
    workflow_command_handler: WorkflowCommandHandler,
) -> CommandResult:
    result = workflow_command_handler(
        message,
        project_root=context.project_root,
    )
    if result is None:
        return CommandResult(handled=False)
    return CommandResult(
        handled=True,
        message=result.message or '',
        prompt=result.prompt,
        metadata={'source': 'workflow_command'},
    )
