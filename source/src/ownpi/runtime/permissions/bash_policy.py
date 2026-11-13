"""Claude Code-style Bash permission analysis with stricter fail-closed edges.

The analyzer intentionally implements a semantic subset: parser-backed simple
commands, lists, and pipelines are inspectable, while shell constructs that need
runtime expansion or output-path reasoning are rejected before permission rules
can allow them.
"""

from __future__ import annotations

from dataclasses import dataclass
import re
import shlex
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from ownpi.runtime.limits.resources import MAX_BASH_COMMAND_CHARS

try:
    import bashlex
except ModuleNotFoundError:  # pragma: no cover - source checkout may be inspected without dev deps.
    bashlex = None  # type: ignore[assignment]

BASH_ANALYSIS_CONTRACT = 'claude_code_semantic_subset_fail_closed'
BASH_STRICT_FAIL_CLOSED_CONSTRUCTS = (
    'command_substitution',
    'command_substitution_backtick',
    'control_character',
    'escaped_newline',
    'escaped_whitespace',
    'glob_expansion',
    'heredoc_redirect',
    'nested_command_execution',
    'parameter_expansion',
    'process_substitution',
    'redirection',
    'subshell_or_grouping',
    'unicode_whitespace',
    'zsh_equals_expansion',
    'zsh_tilde_bracket_expansion',
)

_CONTROL_CHARS_RE = re.compile(r'[\x00-\x08\x0b-\x1f\x7f]')
_UNICODE_WHITESPACE_RE = re.compile(r'[\u00a0\u1680\u2000-\u200b\u2028\u2029\u202f\u205f\u3000\ufeff]')
_ZSH_EQUALS_EXPANSION_RE = re.compile(r'(^|[\s;&|])=[A-Za-z_./-]')
_ASSIGNMENT_NAME_RE = re.compile(r'^[A-Za-z_][A-Za-z0-9_]*=')
_ASSIGNMENT_RE = re.compile(r'^([A-Za-z_][A-Za-z0-9_]*)=(.*)$')
_TIMEOUT_VALUE_RE = re.compile(r'^[A-Za-z0-9_.+-]+$')
_TIMEOUT_DURATION_RE = re.compile(r'^\d+(?:\.\d+)?[smhd]?$')
_NUMERIC_RE = re.compile(r'^-?\d+$')
_NETWORK_COMMAND_NAMES = {
    'curl',
    'dig',
    'ftp',
    'host',
    'nc',
    'netcat',
    'nslookup',
    'ping',
    'ping6',
    'rsync',
    'scp',
    'sftp',
    'ssh',
    'telnet',
    'traceroute',
    'traceroute6',
    'wget',
    'huggingface-cli',
    'hf',
}
_NETWORK_SUBCOMMANDS: dict[str, set[str]] = {
    'apt': {'install', 'update', 'upgrade'},
    'apt-get': {'install', 'update', 'upgrade'},
    'brew': {'install', 'update', 'upgrade'},
    'bun': {'add', 'install', 'update'},
    'cargo': {'install', 'publish', 'search'},
    'git': {'clone', 'fetch', 'ls-remote', 'pull', 'push', 'submodule'},
    'go': {'get', 'install'},
    'npm': {'add', 'install', 'publish', 'update'},
    'pip': {'download', 'install'},
    'pip3': {'download', 'install'},
    'pnpm': {'add', 'install', 'publish', 'update'},
    'uv': {'add', 'pip', 'sync'},
    'yarn': {'add', 'install', 'publish', 'upgrade'},
    'huggingface_hub': {'download'},
}
_UNSAFE_NESTING_COMMANDS = {'find', 'xargs'}


@dataclass(frozen=True)
class BashRedirect:
    operator: str
    target: str


@dataclass(frozen=True)
class BashCommandSegment:
    text: str
    argv: tuple[str, ...]
    effective_argv: tuple[str, ...] = ()
    env_assignments: tuple[str, ...] = ()
    env_vars: tuple[str, ...] = ()
    redirects: tuple[BashRedirect, ...] = ()


@dataclass(frozen=True)
class BashAnalysis:
    kind: str
    commands: tuple[BashCommandSegment, ...] = ()
    reason: str = ''
    unknown_constructs: tuple[str, ...] = ()
    control_operators: tuple[str, ...] = ()
    parser: str = 'bashlex'


@dataclass(frozen=True)
class BashNetworkIntent:
    requires_network: bool
    hosts: tuple[str, ...] = ()
    command_names: tuple[str, ...] = ()


def analyze_bash_command(command: str) -> BashAnalysis:
    command_text = str(command or '').strip()
    if not command_text:
        return BashAnalysis(kind='simple', commands=(), parser=_parser_name())
    preflight_unknowns = _preflight_unknown_constructs(command_text)
    if preflight_unknowns:
        return BashAnalysis(
            kind='too_complex',
            reason=preflight_unknowns[0],
            unknown_constructs=tuple(preflight_unknowns),
            control_operators=tuple(_control_operators_from_text(command_text)),
            parser=_parser_name(),
        )
    if bashlex is None:
        return BashAnalysis(
            kind='too_complex',
            reason='bash_parser_unavailable',
            unknown_constructs=('bash_parser_unavailable',),
            control_operators=tuple(_control_operators_from_text(command_text)),
            parser='unavailable',
        )
    try:
        parts = bashlex.parse(command_text)
    except Exception as exc:
        return BashAnalysis(
            kind='too_complex',
            reason='bash_parse_error',
            unknown_constructs=(f'bash_parse_error:{type(exc).__name__}',),
            control_operators=tuple(_control_operators_from_text(command_text)),
            parser='bashlex',
        )
    collector = _AnalyzerCollector(command_text)
    for part in parts:
        collector.visit(part)
    unknowns = tuple(dict.fromkeys(item for item in collector.unknown_constructs if item))
    if unknowns:
        return BashAnalysis(
            kind='too_complex',
            commands=tuple(collector.commands),
            reason=unknowns[0],
            unknown_constructs=unknowns,
            control_operators=tuple(dict.fromkeys(collector.control_operators)),
            parser='bashlex',
        )
    return BashAnalysis(
        kind='simple',
        commands=tuple(collector.commands),
        control_operators=tuple(dict.fromkeys(collector.control_operators)),
        parser='bashlex',
    )


def extract_network_intent(analysis: BashAnalysis) -> BashNetworkIntent:
    hosts: set[str] = set()
    command_names: set[str] = set()
    requires_network = False
    if analysis.kind != 'simple':
        return BashNetworkIntent(requires_network=False)
    for command in analysis.commands:
        argv = _strip_exec_wrappers_for_intent(command.argv)
        if not argv:
            continue
        command_name = Path(argv[0]).name.lower()
        subcommand = _first_non_option(argv[1:]).lower()
        command_requires_network = command_name in _NETWORK_COMMAND_NAMES or subcommand in _NETWORK_SUBCOMMANDS.get(
            command_name, set()
        )
        token_hosts = _url_hosts_from_tokens(argv)
        if command_requires_network:
            token_hosts.update(_candidate_hosts_from_tokens(argv))
        if command_requires_network or token_hosts:
            requires_network = True
            command_names.add(command_name)
            hosts.update(token_hosts)
    return BashNetworkIntent(
        requires_network=requires_network,
        hosts=tuple(sorted(hosts)),
        command_names=tuple(sorted(command_names)),
    )


def analyze_argv_network_intent(argv: tuple[str, ...] | list[str]) -> BashNetworkIntent:
    raw_argv = tuple(str(item) for item in argv)
    command = BashCommandSegment(
        text=' '.join(raw_argv),
        argv=raw_argv,
        effective_argv=_strip_safe_permission_wrappers(raw_argv),
    )
    return extract_network_intent(BashAnalysis(kind='simple', commands=(command,)))


def bash_analysis_metadata(command: str | None) -> dict[str, object]:
    if command is None:
        return {}
    analysis = analyze_bash_command(command)
    metadata: dict[str, object] = {
        'shell_analysis_contract': BASH_ANALYSIS_CONTRACT,
        'shell_analysis_strict_fail_closed': True,
        'shell_analysis_strict_constructs': list(BASH_STRICT_FAIL_CLOSED_CONSTRUCTS),
        'shell_analysis_kind': analysis.kind,
        'shell_analysis_parser': analysis.parser,
        'shell_analysis_reason': analysis.reason,
        'shell_analysis_unknown_constructs': list(analysis.unknown_constructs),
        'shell_control_operators': list(analysis.control_operators),
        'shell_analysis_command_count': len(analysis.commands),
    }
    intent = extract_network_intent(analysis)
    metadata.update(
        {
            'shell_requires_network': intent.requires_network,
            'shell_network_hosts': list(intent.hosts),
            'shell_network_commands': list(intent.command_names),
        }
    )
    return metadata


class _AnalyzerCollector:
    def __init__(self, command_text: str) -> None:
        self.command_text = command_text
        self.commands: list[BashCommandSegment] = []
        self.unknown_constructs: list[str] = []
        self.control_operators: list[str] = []

    def visit(self, node: Any) -> None:
        kind = str(getattr(node, 'kind', '') or '')
        if kind == 'command':
            self._visit_command(node)
            return
        if kind in {'list', 'pipeline'}:
            for child in _node_children(node):
                self.visit(child)
            return
        if kind in {'operator', 'pipe'}:
            operator = str(getattr(node, 'op', getattr(node, 'pipe', '')) or '').strip()
            if operator:
                self.control_operators.append(operator)
            return
        if kind in {'compound', 'subshell', 'bracegroup', 'function'}:
            self.unknown_constructs.append('subshell_or_grouping')
            return
        if kind == 'reservedword':
            word = str(getattr(node, 'word', '') or '')
            if word.strip() not in {'', '&&', '||', '|', ';'}:
                self.unknown_constructs.append('reserved_word')
            return
        self.unknown_constructs.append(f'unsupported_node:{kind or "unknown"}')

    def _visit_command(self, node: Any) -> None:
        parts = getattr(node, 'parts', None)
        if not isinstance(parts, list):
            self.unknown_constructs.append('malformed_command')
            return
        words: list[str] = []
        env_assignments: list[str] = []
        env_vars: list[str] = []
        redirects: list[BashRedirect] = []
        for part in parts:
            kind = str(getattr(part, 'kind', '') or '')
            if kind == 'assignment':
                assignment = str(getattr(part, 'word', '') or '')
                if not _ASSIGNMENT_NAME_RE.match(assignment) or not _assignment_is_static(assignment):
                    self.unknown_constructs.append('unsupported_assignment')
                    continue
                env_assignments.append(assignment)
                env_vars.append(assignment.split('=', 1)[0])
                continue
            if kind == 'word':
                word_unknowns = _word_unknown_constructs(part)
                if word_unknowns:
                    self.unknown_constructs.extend(word_unknowns)
                    continue
                word = str(getattr(part, 'word', '') or '')
                if _contains_glob_pattern(word):
                    self.unknown_constructs.append('glob_expansion')
                    continue
                words.append(word)
                continue
            if kind in {'redirect', 'redir'}:
                redirects.append(
                    BashRedirect(
                        operator=str(getattr(part, 'type', '') or ''),
                        target=str(getattr(getattr(part, 'output', None), 'word', '') or ''),
                    )
                )
                self.unknown_constructs.append('redirection')
                if getattr(part, 'heredoc', None) is not None:
                    self.unknown_constructs.append('heredoc_redirect')
                continue
            self.unknown_constructs.append(f'unsupported_command_part:{kind or "unknown"}')
        argv = tuple(words)
        if argv:
            stripped = _strip_exec_wrappers_for_intent(argv)
            if stripped and Path(stripped[0]).name.lower() in _UNSAFE_NESTING_COMMANDS:
                self.unknown_constructs.append(f'nested_command_execution:{Path(stripped[0]).name.lower()}')
            text = _slice_node_text(self.command_text, node) or ' '.join((*env_vars, *argv))
            self.commands.append(
                BashCommandSegment(
                    text=_normalize_shell_text(text),
                    argv=argv,
                    effective_argv=_strip_safe_permission_wrappers(argv),
                    env_assignments=tuple(env_assignments),
                    env_vars=tuple(env_vars),
                    redirects=tuple(redirects),
                )
            )


def _preflight_unknown_constructs(command: str) -> tuple[str, ...]:
    unknowns: list[str] = []
    if len(command) > MAX_BASH_COMMAND_CHARS:
        unknowns.append('command_too_long')
    if _CONTROL_CHARS_RE.search(command):
        unknowns.append('control_character')
    if _UNICODE_WHITESPACE_RE.search(command):
        unknowns.append('unicode_whitespace')
    if _ZSH_EQUALS_EXPANSION_RE.search(command):
        unknowns.append('zsh_equals_expansion')
    if '~[' in command:
        unknowns.append('zsh_tilde_bracket_expansion')
    if '\\\n' in command:
        unknowns.append('escaped_newline')
    if re.search(r'\\[ \t]', command):
        unknowns.append('escaped_whitespace')
    if _brace_with_quote_obfuscation(command):
        unknowns.append('brace_with_quote_obfuscation')
    return tuple(dict.fromkeys(unknowns))


def _control_operators_from_text(command: str) -> list[str]:
    operators: list[str] = []
    in_single_quote = False
    in_double_quote = False
    escaped = False
    for index, char in enumerate(command):
        if escaped:
            escaped = False
            continue
        if char == '\\':
            escaped = True
            continue
        if char == "'" and not in_double_quote:
            in_single_quote = not in_single_quote
            continue
        if char == '"' and not in_single_quote:
            in_double_quote = not in_double_quote
            continue
        if in_single_quote or in_double_quote:
            continue
        next_char = command[index + 1] if index + 1 < len(command) else ''
        if char == '&' and next_char == '&':
            operators.append('&&')
        elif char == '|' and next_char == '|':
            operators.append('||')
        elif char == '|':
            operators.append('|')
        elif char == ';':
            operators.append(';')
        elif char == '\n':
            operators.append('newline')
    return list(dict.fromkeys(operators))


def _word_unknown_constructs(node: Any) -> list[str]:
    unknowns: list[str] = []
    parts = getattr(node, 'parts', None)
    if isinstance(parts, list) and parts:
        for child in parts:
            kind = str(getattr(child, 'kind', '') or '')
            if kind == 'commandsubstitution':
                unknowns.append('command_substitution')
            elif kind == 'processsubstitution':
                unknowns.append('process_substitution')
            elif kind == 'parameter':
                unknowns.append('parameter_expansion')
            else:
                unknowns.append(f'unsupported_word_part:{kind or "unknown"}')
    word = str(getattr(node, 'word', '') or '')
    if '`' in word:
        unknowns.append('command_substitution_backtick')
    if '$(' in word:
        unknowns.append('command_substitution')
    if '<(' in word or '>(' in word:
        unknowns.append('process_substitution')
    if '${' in word:
        unknowns.append('parameter_expansion')
    return unknowns


def _contains_glob_pattern(word: str) -> bool:
    escaped = False
    for char in word:
        if escaped:
            escaped = False
            continue
        if char == '\\':
            escaped = True
            continue
        if char in {'*', '?', '['}:
            return True
    return False


def _assignment_is_static(assignment: str) -> bool:
    match = _ASSIGNMENT_RE.match(assignment)
    if not match:
        return False
    value = match.group(2)
    return not any(marker in value for marker in ('$', '`', '$(', '${', '<(', '>('))


def _brace_with_quote_obfuscation(command: str) -> bool:
    masked = _mask_braces_in_quoted_contexts(command)
    return re.search(r'\{[^}]*[\'"]', masked) is not None


def _mask_braces_in_quoted_contexts(command: str) -> str:
    if '{' not in command:
        return command
    output: list[str] = []
    in_single = False
    in_double = False
    index = 0
    while index < len(command):
        char = command[index]
        if in_single:
            if char == "'":
                in_single = False
            output.append(' ' if char == '{' else char)
            index += 1
            continue
        if in_double:
            if char == '\\' and index + 1 < len(command) and command[index + 1] in {'"', '\\'}:
                output.extend([char, command[index + 1]])
                index += 2
                continue
            if char == '"':
                in_double = False
            output.append(' ' if char == '{' else char)
            index += 1
            continue
        if char == '\\' and index + 1 < len(command):
            output.extend([char, command[index + 1]])
            index += 2
            continue
        if char == "'":
            in_single = True
        elif char == '"':
            in_double = True
        output.append(char)
        index += 1
    return ''.join(output)


def _node_children(node: Any) -> list[Any]:
    children: list[Any] = []
    for attr in ('parts', 'list'):
        value = getattr(node, attr, None)
        if isinstance(value, list):
            children.extend(value)
    for attr in ('command', 'output', 'input'):
        value = getattr(node, attr, None)
        if value is not None:
            children.append(value)
    return children


def _slice_node_text(command_text: str, node: Any) -> str:
    pos = getattr(node, 'pos', None)
    if not isinstance(pos, tuple) or len(pos) != 2:
        return ''
    start, end = pos
    if not isinstance(start, int) or not isinstance(end, int):
        return ''
    return command_text[start:end].strip()


def _normalize_shell_text(text: str) -> str:
    try:
        return ' '.join(shlex.split(text, posix=True))
    except ValueError:
        return ' '.join(text.split())


def _strip_safe_permission_wrappers(argv: tuple[str, ...]) -> tuple[str, ...]:
    tokens = list(argv)
    while tokens:
        command_name = Path(tokens[0]).name.lower()
        if command_name in {'time', 'nohup'}:
            tokens.pop(0)
            if tokens and tokens[0] == '--':
                tokens.pop(0)
            continue
        if command_name == 'timeout':
            stripped = _strip_timeout_wrapper(tokens)
            if stripped is None:
                return tuple(tokens)
            tokens = list(stripped)
            continue
        if command_name == 'nice':
            stripped = _strip_nice_wrapper(tokens)
            if stripped is None:
                return tuple(tokens)
            tokens = list(stripped)
            continue
        if command_name == 'stdbuf':
            stripped = _strip_stdbuf_wrapper(tokens)
            if stripped is None:
                return tuple(tokens)
            tokens = list(stripped)
            continue
        break
    return tuple(tokens)


def _strip_exec_wrappers_for_intent(argv: tuple[str, ...]) -> tuple[str, ...]:
    tokens = list(argv)
    while tokens:
        command_name = Path(tokens[0]).name.lower()
        if command_name == 'env':
            tokens.pop(0)
            while tokens and (tokens[0].startswith('-') or _ASSIGNMENT_NAME_RE.match(tokens[0])):
                tokens.pop(0)
            continue
        if command_name == 'command':
            tokens.pop(0)
            while tokens and tokens[0].startswith('-'):
                tokens.pop(0)
            continue
        stripped = _strip_safe_permission_wrappers(tuple(tokens))
        if list(stripped) == tokens:
            break
        tokens = list(stripped)
    if len(tokens) >= 3 and Path(tokens[0]).name.lower().startswith('python') and tokens[1] == '-m':
        return (tokens[2], *tokens[3:])
    return tuple(tokens)


def _strip_timeout_wrapper(tokens: list[str]) -> tuple[str, ...] | None:
    index = 1
    while index < len(tokens):
        arg = tokens[index]
        next_arg = tokens[index + 1] if index + 1 < len(tokens) else ''
        if arg in {'--foreground', '--preserve-status', '--verbose', '-v'}:
            index += 1
        elif re.fullmatch(r'--(?:kill-after|signal)=[A-Za-z0-9_.+-]+', arg):
            index += 1
        elif arg in {'--kill-after', '--signal', '-k', '-s'} and next_arg and _TIMEOUT_VALUE_RE.fullmatch(next_arg):
            index += 2
        elif re.fullmatch(r'-[ks][A-Za-z0-9_.+-]+', arg):
            index += 1
        elif arg == '--':
            index += 1
            break
        elif arg.startswith('-'):
            return None
        else:
            break
    if index >= len(tokens) or not _TIMEOUT_DURATION_RE.fullmatch(tokens[index]):
        return None
    return tuple(tokens[index + 1:])


def _strip_nice_wrapper(tokens: list[str]) -> tuple[str, ...] | None:
    index = 1
    if index < len(tokens) and tokens[index] == '-n':
        if index + 1 >= len(tokens) or not _NUMERIC_RE.fullmatch(tokens[index + 1]):
            return None
        index += 2
    elif index < len(tokens) and re.fullmatch(r'-\d+', tokens[index]):
        index += 1
    if index < len(tokens) and tokens[index] == '--':
        index += 1
    return tuple(tokens[index:]) if index < len(tokens) else None


def _strip_stdbuf_wrapper(tokens: list[str]) -> tuple[str, ...] | None:
    index = 1
    consumed = False
    while index < len(tokens):
        arg = tokens[index]
        if re.fullmatch(r'-[ioe][LN0-9]+', arg):
            consumed = True
            index += 1
            continue
        break
    if not consumed:
        return None
    if index < len(tokens) and tokens[index] == '--':
        index += 1
    return tuple(tokens[index:]) if index < len(tokens) else None


def _candidate_hosts_from_tokens(tokens: tuple[str, ...]) -> set[str]:
    hosts: set[str] = set()
    for token in tokens[1:]:
        host = _network_host_candidate(token)
        if host:
            hosts.add(host)
    return hosts


def _url_hosts_from_tokens(tokens: tuple[str, ...]) -> set[str]:
    hosts: set[str] = set()
    for token in tokens:
        parsed = urlparse(str(token or '').strip().strip('\'"'))
        if parsed.scheme in {'http', 'https', 'ftp'} and parsed.hostname:
            hosts.add(parsed.hostname.lower())
    return hosts


def _network_host_candidate(token: str) -> str:
    normalized = str(token or '').strip().strip('\'"')
    if not normalized or normalized.startswith('-'):
        return ''
    parsed = urlparse(normalized)
    if parsed.hostname:
        return parsed.hostname.lower()
    if '@' in normalized:
        normalized = normalized.rsplit('@', 1)[-1]
    normalized = normalized.split(':', 1)[0].strip('[]')
    if not normalized or '/' in normalized or normalized in {'.', '..'}:
        return ''
    lowered = normalized.lower()
    if '.' in lowered or lowered == 'localhost':
        return lowered
    return ''


def _first_non_option(tokens: tuple[str, ...]) -> str:
    for token in tokens:
        if token.startswith('-'):
            continue
        return token
    return ''


def _parser_name() -> str:
    return 'bashlex' if bashlex is not None else 'unavailable'
