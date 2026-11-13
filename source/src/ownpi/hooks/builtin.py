from __future__ import annotations

import os
from pathlib import Path
import re
import subprocess
from typing import Any

from ownpi.runtime.hooks.registry import HookRegistration, managed_callback_registration
from ownpi.schemas.runtime import BuiltinHooksConfig


_BUILTIN_HOOK_SOURCE = 'builtin'


def build_builtin_hook_registrations(config: BuiltinHooksConfig | None = None) -> tuple[HookRegistration, ...]:
    resolved = config or BuiltinHooksConfig()
    if not resolved.enabled:
        return ()
    return (
        managed_callback_registration(
            'SessionStart',
            matcher='*',
            callback=_session_start_callback,
            source=_BUILTIN_HOOK_SOURCE,
            name='session-start',
        ),
        managed_callback_registration(
            'Stop',
            matcher='*',
            callback=_stop_summary_callback,
            source=_BUILTIN_HOOK_SOURCE,
            name='stop-summary',
        ),
        managed_callback_registration(
            'SessionEnd',
            matcher='*',
            callback=_session_summary_callback,
            source=_BUILTIN_HOOK_SOURCE,
            name='session-summary',
        ),
    )


def _session_start_callback(payload: dict[str, Any]) -> dict[str, Any]:
    cwd = _payload_cwd(payload)
    git_info = _git_info(cwd)
    todo_info = _todo_info(cwd)
    lines = [
        f'Session started: {cwd.name}',
        f'Directory: {cwd}',
    ]
    if git_info['is_repo']:
        lines.append(f"Git branch: {git_info['branch']}")
        if git_info['changes_count']:
            lines.append(f"Uncommitted changes: {git_info['changes_count']} files")
            lines.extend(f"  - {item}" for item in git_info['changes'][:5])
            if git_info['changes_count'] > 5:
                lines.append(f"  - ... {git_info['changes_count'] - 5} more")
        else:
            lines.append('Working directory: clean')
    else:
        lines.append('Git: not a repository')
    if todo_info['found']:
        lines.append(f"Todos: {todo_info['pending']} pending / {todo_info['done']} done in {todo_info['file']}")
    else:
        lines.append('Todos: no todo file found')
    return {'continue': True, 'systemMessage': '\n'.join(lines)}


def _stop_summary_callback(payload: dict[str, Any]) -> dict[str, Any]:
    cwd = _payload_cwd(payload)
    reason = str(payload.get('reason') or 'task_complete')
    git_info = _git_info(cwd)
    temp_info = _temp_file_info(cwd)
    lines = [
        f'Session stop requested: {reason}',
        f'Directory: {cwd}',
    ]
    if git_info['is_repo']:
        lines.append(f"Git branch: {git_info['branch']}")
        lines.append(f"Uncommitted changes: {git_info['changes_count']} files")
    else:
        lines.append('Git: not a repository')
    lines.append(f"Temp files: {temp_info['count']}")
    for bucket, count in list(temp_info['buckets'].items())[:5]:
        lines.append(f'  - {bucket}: {count}')
    return {'continue': True, 'systemMessage': '\n'.join(lines)}


def _session_summary_callback(payload: dict[str, Any]) -> dict[str, Any]:
    cwd = _payload_cwd(payload)
    session_id = str(payload.get('session_id') or payload.get('run_id') or 'unknown')
    git_info = _git_info(cwd)
    todo_info = _todo_info(cwd)
    lines = [
        f'Session ended: {cwd.name}',
        f'Session id: {session_id}',
        f'Directory: {cwd}',
    ]
    if git_info['is_repo']:
        lines.append(f"Git branch: {git_info['branch']}")
        lines.append(f"Uncommitted changes: {git_info['changes_count']} files")
        if git_info['changes_count']:
            lines.append('Suggested next action: review, test, then commit the tracked changes.')
    else:
        lines.append('Git: not a repository')
    if todo_info['found'] and todo_info['pending']:
        lines.append(f"Open todos remain: {todo_info['pending']} in {todo_info['file']}")
    return {'continue': True, 'systemMessage': '\n'.join(lines)}


def _payload_cwd(payload: dict[str, Any]) -> Path:
    raw = str(payload.get('cwd') or payload.get('project_root') or os.getcwd())
    try:
        return Path(raw).expanduser().resolve()
    except OSError:
        return Path.cwd()


def _run_git(cwd: Path, args: list[str]) -> str:
    try:
        completed = subprocess.run(
            ['git', *args],
            cwd=str(cwd),
            check=True,
            capture_output=True,
            text=True,
            timeout=2,
        )
    except (OSError, subprocess.SubprocessError):
        return ''
    return completed.stdout.strip()


def _git_info(cwd: Path) -> dict[str, Any]:
    if not _run_git(cwd, ['rev-parse', '--git-dir']):
        return {'is_repo': False, 'branch': 'unknown', 'changes_count': 0, 'changes': ()}
    branch = _run_git(cwd, ['branch', '--show-current']) or 'unknown'
    status = _run_git(cwd, ['status', '--porcelain'])
    changes = tuple(line for line in status.splitlines() if line.strip())
    return {
        'is_repo': True,
        'branch': branch,
        'changes_count': len(changes),
        'changes': changes,
    }


def _todo_info(cwd: Path) -> dict[str, Any]:
    candidates = (
        cwd / 'docs' / 'todo.md',
        cwd / 'TODO.md',
        cwd / 'TODO',
        cwd / 'notes' / 'todo.md',
        cwd / '.claude' / 'todos.md',
    )
    for path in candidates:
        if not path.exists() or not path.is_file():
            continue
        try:
            text = path.read_text(encoding='utf-8', errors='ignore')
        except OSError:
            continue
        total = len(re.findall(r'^[\-\*]\s+\[[ xX]\]', text, flags=re.MULTILINE))
        done = len(re.findall(r'^[\-\*]\s+\[[xX]\]', text, flags=re.MULTILINE))
        return {'found': True, 'file': path.name, 'total': total, 'done': done, 'pending': max(0, total - done)}
    return {'found': False, 'file': '', 'total': 0, 'done': 0, 'pending': 0}


def _temp_file_info(cwd: Path) -> dict[str, Any]:
    roots = ('plan', 'docs/plans', '.claude/temp', 'tmp', 'temp')
    files: set[str] = set()
    git_info = _git_info(cwd)
    for change in git_info.get('changes', ()):
        if isinstance(change, str) and change.startswith('??'):
            candidate = change[3:].strip()
            if re.search(r'plan|draft|tmp|temp|scratch', candidate, flags=re.IGNORECASE):
                files.add(candidate)
    for root in roots:
        path = cwd / root
        if not path.exists() or not path.is_dir():
            continue
        try:
            for item in path.rglob('*'):
                if item.is_file():
                    files.add(str(item.relative_to(cwd)))
        except OSError:
            continue
    buckets: dict[str, int] = {}
    for file in sorted(files):
        normalized = file.replace('\\', '/')
        bucket = next((root for root in roots if normalized == root or normalized.startswith(f'{root}/')), '')
        if not bucket:
            bucket = normalized.split('/', 1)[0] if '/' in normalized else './'
        buckets[bucket] = buckets.get(bucket, 0) + 1
    return {'count': len(files), 'buckets': buckets}


def _is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return True
