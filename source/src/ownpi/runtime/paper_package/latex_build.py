from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
import time
from typing import Any, Callable, Mapping

from ownpi.runtime.environment.subprocess_env import build_subprocess_env
from ownpi.runtime.paper_package.paths import (
    PaperPathError,
    relative_path,
    resolve_contained_path,
)
from ownpi.runtime.permissions.action_compiler import OperationEffects
from ownpi.runtime.permissions.models import EffectivePermissions, FileSystemAccessMode
from ownpi.runtime.permissions.orchestrator import FileSystemAccessRequest
from ownpi.runtime.permissions.policy import lexical_absolute_path, require_file_access
from ownpi.runtime.permissions.runtime_config import default_runtime_permissions
from ownpi.runtime.sandbox import SandboxCommandRequest, SandboxManager
from ownpi.runtime.sandbox.denial import DenialOutputEvidence, is_sandbox_denied
from ownpi.runtime.tools.errors import ToolPreflightValidationError


LATEX_BUILD_RECEIPT_SCHEMA_VERSION = 1
_SOURCE_SUFFIXES = {'.tex', '.bib', '.cls', '.sty', '.bst', '.png', '.jpg', '.jpeg', '.pdf', '.svg'}
_CITE_RE = re.compile(r'\\cite[a-zA-Z*]*\s*(?:\[[^\]]*\]\s*){0,2}\{(?P<keys>[^}]+)\}')
_BIB_ENTRY_RE = re.compile(r'@\w+\s*\{\s*([^,\s]+)\s*,', re.IGNORECASE)
_INPUT_RE = re.compile(r'\\(?:input|include)\{(?P<path>[^}]+)\}')


@dataclass(frozen=True)
class LatexBuildReceipt:
    entry_point: str
    command: tuple[str, ...]
    cwd: str
    exit_code: int | None
    duration_seconds: float
    dependencies: dict[str, str]
    input_refs: tuple[dict[str, Any], ...]
    pdf_ref: dict[str, Any]
    log_ref: dict[str, Any]
    receipt_path: str
    diagnostics: tuple[dict[str, Any], ...]
    bibliography: dict[str, Any]
    status: str
    failure_class: str
    created_at: str
    schema_version: int = LATEX_BUILD_RECEIPT_SCHEMA_VERSION
    receipt_id: str = ''
    content_hash: str = ''

    def to_dict(self) -> dict[str, Any]:
        payload = self._payload()
        payload['receipt_id'] = self.receipt_id or _receipt_id(payload)
        payload['content_hash'] = self.content_hash or _hash_payload(payload)
        return payload

    def _payload(self) -> dict[str, Any]:
        return {
            'schema_version': self.schema_version,
            'entry_point': self.entry_point,
            'command': list(self.command),
            'cwd': self.cwd,
            'exit_code': self.exit_code,
            'duration_seconds': self.duration_seconds,
            'dependencies': dict(self.dependencies),
            'input_refs': [dict(item) for item in self.input_refs],
            'pdf_ref': dict(self.pdf_ref),
            'log_ref': dict(self.log_ref),
            'receipt_path': self.receipt_path,
            'diagnostics': [dict(item) for item in self.diagnostics],
            'bibliography': dict(self.bibliography),
            'status': self.status,
            'failure_class': self.failure_class,
            'created_at': self.created_at,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any], *, verify_hash: bool = True) -> 'LatexBuildReceipt':
        receipt = cls(
            schema_version=int(data.get('schema_version', 0) or 0),
            entry_point=str(data.get('entry_point', '') or ''),
            command=tuple(str(item) for item in data.get('command', []) or []),
            cwd=str(data.get('cwd', '') or ''),
            exit_code=int(data['exit_code']) if data.get('exit_code') is not None else None,
            duration_seconds=float(data.get('duration_seconds', 0) or 0),
            dependencies={str(key): str(value) for key, value in dict(data.get('dependencies', {}) or {}).items()},
            input_refs=tuple(dict(item) for item in data.get('input_refs', []) or []),
            pdf_ref=dict(data.get('pdf_ref', {}) or {}),
            log_ref=dict(data.get('log_ref', {}) or {}),
            receipt_path=str(data.get('receipt_path', '') or ''),
            diagnostics=tuple(dict(item) for item in data.get('diagnostics', []) or []),
            bibliography=dict(data.get('bibliography', {}) or {}),
            status=str(data.get('status', '') or ''),
            failure_class=str(data.get('failure_class', '') or ''),
            created_at=str(data.get('created_at', '') or ''),
            receipt_id=str(data.get('receipt_id', '') or ''),
            content_hash=str(data.get('content_hash', '') or ''),
        )
        if receipt.schema_version != LATEX_BUILD_RECEIPT_SCHEMA_VERSION:
            raise ValueError(f'unsupported LaTeX build schema_version: {receipt.schema_version}')
        expected = _hash_payload(receipt._payload() | {'receipt_id': receipt.receipt_id})
        if verify_hash and (not receipt.content_hash or receipt.content_hash != expected):
            raise ValueError('LaTeX build receipt content hash mismatch')
        return receipt


async def run_latex_build(
    *,
    workspace: Path,
    project_dir: str,
    entry_point: str,
    engine: str = 'pdflatex',
    timeout_seconds: int = 120,
    runner: Callable[..., subprocess.CompletedProcess[str]] | None = None,
    effective_permissions: EffectivePermissions | None = None,
) -> LatexBuildReceipt:
    root = workspace.resolve()
    permissions = effective_permissions or default_runtime_permissions(project_root=root)
    lexical_project = lexical_absolute_path(project_dir or '.', cwd=workspace)
    lexical_entry = lexical_absolute_path(entry_point, cwd=lexical_project)
    project = resolve_contained_path(
        root,
        project_dir,
        field_name='project_dir',
    )
    entry = resolve_contained_path(
        project,
        entry_point,
        field_name='entry_point',
        allow_absolute=False,
    )
    clean_engine = engine.strip().lower()
    if clean_engine not in {'pdflatex', 'xelatex', 'lualatex'}:
        raise ValueError('engine must be pdflatex, xelatex, or lualatex')
    require_file_access(permissions, project, FileSystemAccessMode.READ)
    require_file_access(permissions, lexical_project, FileSystemAccessMode.READ)
    require_file_access(permissions, lexical_entry, FileSystemAccessMode.READ)
    require_file_access(permissions, entry, FileSystemAccessMode.READ)
    if not entry.exists() or not entry.is_file() or entry.suffix.lower() != '.tex':
        raise ValueError(f'LaTeX entry point does not exist: {entry_point}')
    created_at = datetime.now(timezone.utc).isoformat()
    token = re.sub(r'[^0-9]', '', created_at)[:20]
    receipt_dir = project / '.authoring' / 'receipts' / 'latex'
    log_path = receipt_dir / f'latex-build-{token}.log'
    receipt_path = receipt_dir / f'latex-build-{token}.json'
    source_text = _source_graph_text(project, lexical_entry, permissions)
    for directory in (receipt_dir.parent.parent, receipt_dir.parent, receipt_dir):
        require_file_access(permissions, directory, FileSystemAccessMode.WRITE)
    require_file_access(permissions, log_path, FileSystemAccessMode.WRITE)
    require_file_access(permissions, receipt_path, FileSystemAccessMode.WRITE)
    receipt_dir.mkdir(parents=True, exist_ok=True)
    backend = _bibliography_backend(source_text)
    entry_argument = relative_path(project, entry)
    dependencies = {
        'latexmk': shutil.which('latexmk') or '',
        clean_engine: shutil.which(clean_engine) or '',
    }
    if backend:
        dependencies[backend] = shutil.which(backend) or ''
    missing = [name for name, path in dependencies.items() if not path]
    pdf = project / f'{entry.stem}.pdf'
    input_paths = tuple(
        path
        for path in _input_paths(project, permissions)
        if path.is_file()
        and path.suffix.lower() in _SOURCE_SUFFIXES
        and path != pdf
        and '.authoring' not in path.parts
    )[:500]
    command = (
        'latexmk',
        f'-{clean_engine.removesuffix("latex")}latex' if clean_engine != 'pdflatex' else '-pdf',
        '-interaction=nonstopmode',
        '-halt-on-error',
        entry_argument,
    )
    completed: subprocess.CompletedProcess[str] | None = None
    environment_error = ''
    started = time.monotonic()
    if missing:
        environment_error = f'missing required commands: {", ".join(missing)}'
    else:
        try:
            if runner is not None:
                completed = runner(
                    list(command),
                    cwd=project,
                    text=True,
                    capture_output=True,
                    timeout=max(1, min(int(timeout_seconds), 600)),
                    check=False,
                )
            else:
                sandbox_result = await SandboxManager().execute_async(
                    SandboxCommandRequest(
                        argv=command,
                        cwd=project,
                        env=build_subprocess_env(),
                        effective_permissions=permissions,
                        timeout_seconds=max(1, min(int(timeout_seconds), 600)),
                    )
                )
                evidence = DenialOutputEvidence()
                evidence.observe('stdout', sandbox_result.stdout)
                evidence.observe('stderr', sandbox_result.stderr)
                if is_sandbox_denied(
                    sandbox_backend=sandbox_result.backend_kind, closed=True,
                    exit_code=sandbox_result.exit_code, timed_out=sandbox_result.timed_out,
                    matched_marker=evidence.matched_marker,
                ):
                    raise PermissionError(evidence.output_excerpt or 'LaTeX sandbox denied')
                completed = subprocess.CompletedProcess(
                    args=list(command),
                    returncode=sandbox_result.exit_code,
                    stdout=sandbox_result.stdout,
                    stderr=sandbox_result.stderr,
                )
                if sandbox_result.timed_out:
                    environment_error = (
                        f'LaTeX build timed out after '
                        f'{max(1, min(int(timeout_seconds), 600))} seconds'
                    )
        except subprocess.TimeoutExpired as exc:
            environment_error = f'LaTeX build timed out after {exc.timeout} seconds'
        except PermissionError:
            raise
        except OSError as exc:
            environment_error = f'{type(exc).__name__}: {str(exc)[:280]}'
    duration = round(time.monotonic() - started, 6)
    log = environment_error
    if completed is not None:
        log = f'{completed.stdout or ""}{completed.stderr or ""}'
    require_file_access(permissions, log_path, FileSystemAccessMode.WRITE)
    log_path.write_text(log, encoding='utf-8')
    final_tex_log = project / f'{entry.stem}.log'
    diagnostic_log = _read_text(final_tex_log, permissions) if final_tex_log.exists() else log
    diagnostics = _parse_latex_diagnostics(
        diagnostic_log,
        entry_point=entry_argument,
    )
    bibliography = _bibliography_status(
        project=project,
        entry=entry,
        source_text=source_text,
        backend=backend,
        log=diagnostic_log,
        permissions=permissions,
    )
    if bibliography['required'] and not bibliography['resolved']:
        diagnostics = (*diagnostics, {'category': 'bibliography_unresolved', 'file': entry.name, 'line': None, 'message': 'Bibliography keys or generated references are unresolved.'})
    manuscript_errors = [item for item in diagnostics if item.get('severity') != 'warning']
    passed = completed is not None and completed.returncode == 0 and pdf.exists() and not manuscript_errors
    status = 'passed' if passed else 'failed'
    failure_class = 'none' if passed else ('environment' if environment_error else 'manuscript')
    input_refs = tuple(_file_ref(root, path, permissions) for path in input_paths)
    draft = LatexBuildReceipt(
        entry_point=_relative(root, entry),
        command=command,
        cwd=_relative(root, project),
        exit_code=completed.returncode if completed is not None else None,
        duration_seconds=duration,
        dependencies=dependencies,
        input_refs=input_refs,
        pdf_ref=_file_ref(root, pdf, permissions) if pdf.exists() else {},
        log_ref=_file_ref(root, log_path, permissions),
        receipt_path=_relative(root, receipt_path),
        diagnostics=tuple(diagnostics[:100]),
        bibliography=bibliography,
        status=status,
        failure_class=failure_class,
        created_at=created_at,
    )
    payload = draft._payload()
    receipt_id = _receipt_id(payload)
    receipt = LatexBuildReceipt(
        **{**draft.__dict__, 'receipt_id': receipt_id, 'content_hash': _hash_payload(payload | {'receipt_id': receipt_id})}
    )
    require_file_access(permissions, receipt_path, FileSystemAccessMode.WRITE)
    receipt_path.write_text(json.dumps(receipt.to_dict(), indent=2, sort_keys=True) + '\n', encoding='utf-8')
    return receipt


def resolve_latex_build_effects(
    context: Any,
    arguments: dict[str, Any],
) -> OperationEffects:
    context_root = Path(context.project_root).resolve()
    try:
        root = resolve_contained_path(
            context_root,
            str(arguments.get('workspace_path', '') or ''),
            field_name='workspace_path',
        )
        project = resolve_contained_path(
            root,
            str(arguments.get('project_dir', '') or ''),
            field_name='project_dir',
        )
        entry = resolve_contained_path(
            project,
            str(arguments.get('entry_point', '') or ''),
            field_name='entry_point',
            allow_absolute=False,
        )
    except PaperPathError as exc:
        raise ToolPreflightValidationError(
            str(exc),
            validation_code=f'latex_build_{exc.field_name}_invalid',
            compact_hint=(
                'Use a path inside the active workspace. workspace_path is based on the '
                'runtime workspace, project_dir is based on workspace_path, and entry_point '
                'must be relative to project_dir.'
            ),
            field_name=exc.field_name,
        ) from exc
    engine = str(arguments.get('engine', 'pdflatex') or 'pdflatex').strip().lower()
    command = (
        shutil.which('latexmk') or 'latexmk',
        f'-{engine.removesuffix("latex")}latex' if engine != 'pdflatex' else '-pdf',
        '-interaction=nonstopmode',
        '-halt-on-error',
        relative_path(project, entry),
    )
    lexical_workspace = lexical_absolute_path(arguments.get('workspace_path') or '.', cwd=context_root)
    lexical_project = lexical_absolute_path(arguments.get('project_dir') or '.', cwd=lexical_workspace)
    lexical_entry = lexical_absolute_path(arguments.get('entry_point') or '.', cwd=lexical_project)
    accesses = [
        FileSystemAccessRequest(lexical_project, FileSystemAccessMode.READ),
        FileSystemAccessRequest(lexical_entry, FileSystemAccessMode.READ),
        FileSystemAccessRequest(lexical_project, FileSystemAccessMode.WRITE),
    ]
    executable = Path(command[0]).expanduser()
    if executable.is_absolute():
        accesses.append(
            FileSystemAccessRequest(executable.resolve(), FileSystemAccessMode.READ)
        )
    return OperationEffects.declared(
        file_system_accesses=tuple(accesses),
        process_required=True,
        process_argv=command,
        source='latex_build_process',
    )


def _parse_latex_diagnostics(log: str, *, entry_point: str) -> tuple[dict[str, Any], ...]:
    diagnostics: list[dict[str, Any]] = []
    pending_line: int | None = None
    lines = log.splitlines()
    for line in lines:
        line_match = re.match(r'l\.(\d+)', line.strip())
        if line_match:
            pending_line = int(line_match.group(1))
            if diagnostics and diagnostics[-1].get('line') is None:
                diagnostics[-1]['line'] = pending_line
        category = ''
        severity = 'error'
        lowered = line.casefold()
        if 'citation' in lowered and ('undefined' in lowered or 'not found' in lowered):
            category = 'missing_citation'
        elif 'reference' in lowered and 'undefined' in lowered:
            category = 'missing_reference'
        elif 'file ' in lowered and 'not found' in lowered:
            match = re.search(r"File [`']([^`']+)[`'] not found", line, re.IGNORECASE)
            missing = match.group(1) if match else ''
            category = 'missing_asset' if Path(missing).suffix.lower() in {'.png', '.jpg', '.pdf', '.svg'} else 'missing_package'
        elif 'font' in lowered and ('not found' in lowered or 'not loadable' in lowered):
            category = 'missing_font'
        elif 'undefined control sequence' in lowered:
            category = 'undefined_control_sequence'
        elif 'emergency stop' in lowered or 'fatal error occurred' in lowered:
            category = 'latex_fatal_error'
        elif 'overfull \\hbox' in lowered or 'overfull \\vbox' in lowered:
            category, severity = 'layout_overflow', 'warning'
        if category:
            diagnostic = {
                'category': category,
                'severity': severity,
                'file': entry_point,
                'line': pending_line,
                'message': ' '.join(line.split())[:400],
            }
            if diagnostic not in diagnostics:
                diagnostics.append(diagnostic)
    return tuple(diagnostics)


def _bibliography_status(
    *,
    project: Path,
    entry: Path,
    source_text: str,
    backend: str,
    log: str,
    permissions: EffectivePermissions,
) -> dict[str, Any]:
    cite_keys = {
        key.strip()
        for match in _CITE_RE.finditer(source_text)
        for key in match.group('keys').split(',')
        if key.strip()
    }
    bib_keys = {
        key
        for bib_path in project.glob('*.bib')
        for key in _BIB_ENTRY_RE.findall(_read_text(bib_path, permissions))
    }
    bbl_path = project / f'{entry.stem}.bbl'
    unresolved = 'undefined citations' in log.casefold() or any(key not in bib_keys for key in cite_keys)
    return {
        'required': bool(cite_keys),
        'backend': backend,
        'citation_keys': sorted(cite_keys),
        'bibtex_keys': sorted(bib_keys),
        'bbl_path': bbl_path.relative_to(project).as_posix() if bbl_path.exists() else '',
        'bbl_sha256': _sha256(bbl_path, permissions) if bbl_path.exists() else '',
        'resolved': not cite_keys or (bool(bbl_path.exists() and bbl_path.stat().st_size > 0) and not unresolved),
    }


def _bibliography_backend(text: str) -> str:
    if '\\addbibresource' in text or '\\printbibliography' in text:
        return 'biber'
    if '\\bibliography' in text:
        return 'bibtex'
    return ''


def _source_graph_text(project: Path, entry: Path, permissions: EffectivePermissions) -> str:
    pending = [entry]
    visited: set[Path] = set()
    parts: list[str] = []
    while pending and len(visited) < 200:
        path = pending.pop()
        if path in visited or not path.exists() or not path.is_file():
            continue
        visited.add(path)
        text = _read_text(path, permissions)
        parts.append(text)
        for match in _INPUT_RE.finditer(text):
            child = Path(match.group('path'))
            if not child.suffix:
                child = child.with_suffix('.tex')
            candidate = project / child
            try:
                candidate.resolve().relative_to(project)
            except ValueError:
                continue
            pending.append(candidate)
    return '\n'.join(parts)


def _file_ref(root: Path, path: Path, permissions: EffectivePermissions) -> dict[str, Any]:
    return {'path': _relative(root, path), 'sha256': _sha256(path, permissions), 'size_bytes': path.stat().st_size}


def _read_text(path: Path, permissions: EffectivePermissions) -> str:
    require_file_access(permissions, path, FileSystemAccessMode.READ)
    return path.read_text(encoding='utf-8', errors='replace')


def _input_paths(project: Path, permissions: EffectivePermissions) -> list[Path]:
    pending = [project]
    files: list[Path] = []
    while pending:
        directory = pending.pop()
        require_file_access(permissions, directory, FileSystemAccessMode.READ)
        for path in sorted(directory.iterdir()):
            if path.name == '.authoring':
                continue
            if path.is_dir() and not path.is_symlink():
                pending.append(path)
            elif path.is_file():
                files.append(path)
    return sorted(files)


def _relative(root: Path, path: Path) -> str:
    return relative_path(root, path)


def _sha256(path: Path, permissions: EffectivePermissions) -> str:
    require_file_access(permissions, path, FileSystemAccessMode.READ)
    digest = hashlib.sha256()
    with path.open('rb') as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def _hash_payload(payload: Mapping[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode('utf-8')
    ).hexdigest()


def _receipt_id(payload: Mapping[str, Any]) -> str:
    return f'latex-build-{_hash_payload(payload)[:20]}'
