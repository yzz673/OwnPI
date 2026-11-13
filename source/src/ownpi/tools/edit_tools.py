from __future__ import annotations

import asyncio
import difflib
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ownpi.runtime.contracts import ToolResult
from ownpi.runtime.limits.projection import MAX_DIFF_PREVIEW_CHARS
from ownpi.runtime.permissions.models import FileSystemAccessMode
from ownpi.runtime.permissions.policy import FileSystemPolicyMatcher, lexical_absolute_path
from ownpi.runtime.read_file_state import (
    build_read_file_state_entry_from_content,
    remember_read_file_state,
)
from ownpi.tools.codex_patch import (
    PatchChange,
    PatchError,
    apply_patch_report,
    parse_patch,
    patch_environment_id,
)
from ownpi.tools.patching.filesystem import (
    LocalPatchFileSystem,
    PatchExecutionAuthority,
    PatchPolicyError,
)
from ownpi.tools.common import metadata_for_context, truncate_text

if TYPE_CHECKING:
    from ownpi.runtime.tools.definitions import ToolExecutionContext


_PATCH_FILESYSTEM_KIND = 'local'
_PATCH_PARSER_MODE = 'begin_patch_v1'


def _resolve_edit_path(
    context: ToolExecutionContext,
    raw_path: Any,
    *,
    default: str = '.',
    tool_name: str | None = None,
) -> Path:
    del tool_name
    path = lexical_absolute_path(raw_path or default, cwd=context.project_root)
    permissions = context.effective_permissions
    if permissions is None:
        raise PermissionError("canonical effective permissions are required for file mutation")
    decision = FileSystemPolicyMatcher(permissions.profile.file_system).decide(
        path, requested_access=FileSystemAccessMode.WRITE, cwd=context.project_root,
    )
    if not decision.allowed:
        raise PatchPolicyError(f"filesystem policy denied write: {path}")
    return path


async def _notify_lsp_file_saved(context: ToolExecutionContext, path: Path) -> int:
    try:
        from ownpi.runtime.tools.lsp import notify_lsp_file_saved

        return await notify_lsp_file_saved(root=context.project_root, path=path)
    except Exception:
        return 0
















async def run_patch_apply(context: ToolExecutionContext, arguments: dict[str, Any]) -> ToolResult:
    patch_value = arguments.get('patch')
    if not isinstance(patch_value, str):
        return _patch_input_failure_result(
            context,
            error='patch must be a string',
            failure_kind='invalid_patch',
            recommended_next_action=(
                'Submit the complete transaction as one string bounded by Begin Patch and End Patch, then '
                'retry patch_apply.'
            ),
        )
    patch_text = patch_value
    if not patch_text.strip():
        return _patch_input_failure_result(
            context,
            error='patch is required',
            failure_kind='invalid_patch',
            recommended_next_action=(
                'Provide a non-empty Begin Patch payload containing the complete transaction, then retry '
                'patch_apply.'
            ),
        )
    if context.environment_provider_kind != 'host_local':
        error = (
            'patch_apply supports only the host_local environment; '
            f'selected {context.environment_provider_kind or "unknown"}'
        )
        return ToolResult(
            ok=False,
            tool_name='patch_apply',
            error=error,
            stderr=error,
            data={
                'denial_kind': 'unsupported_environment',
                'failure_class': 'patch_environment_error',
                'failure_kind': 'unsupported_environment',
                'mutation_applied': False,
                'mutation_state': 'none',
                'retry_same_tool': False,
                'preserve_transaction': False,
                'recommended_next_action': (
                    'Use patch_apply only in a host_local environment. Do not retry the same patch unchanged '
                    'in the selected environment.'
                ),
            },
            metadata=metadata_for_context(
                context,
                denial_kind='unsupported_environment',
                environment_provider_kind=context.environment_provider_kind,
            ),
        )
    cwd = context.project_root.resolve()
    try:
        operations = parse_patch(patch_text)
    except PatchError as exc:
        return _patch_error_result(context, exc)
    environment_id = patch_environment_id(patch_text)
    if environment_id and environment_id not in _patch_environment_identities(context):
        error = f'patch Environment ID does not match the active environment: {environment_id}'
        return _patch_input_failure_result(
            context,
            error=error,
            failure_kind='environment_mismatch',
            recommended_next_action=(
                'Correct or remove the Environment ID while preserving the complete transaction, then '
                'retry patch_apply.'
            ),
        )
    try:
        patch_paths: list[Path] = []
        for operation in operations:
            patch_paths.append(
                _resolve_edit_path(context, operation.path, tool_name='patch_apply')
            )
            if operation.move_path is not None:
                patch_paths.append(
                    _resolve_edit_path(
                        context,
                        operation.move_path,
                        tool_name='patch_apply',
                    )
                )
    except (OSError, ValueError) as exc:
        if isinstance(exc, PatchPolicyError):
            return _patch_permission_failure_result(
                context,
                error=str(exc),
                touched_paths=patch_paths,
            )
        return _patch_input_failure_result(
            context,
            error=str(exc),
            failure_kind='invalid_path',
            recommended_next_action=(
                'Correct the rejected path while preserving the complete transaction, then retry '
                'patch_apply.'
            ),
        )
    try:
        permissions = context.effective_permissions
        if permissions is None:
            raise PermissionError('canonical effective permissions are required for patch execution')
        await asyncio.sleep(0)
        if context.cancel_checker is not None and context.cancel_checker():
            raise asyncio.CancelledError('patch cancelled before commit')
        # Commit and rollback form one synchronous boundary; do not abandon
        # a partially applied transaction in response to task cancellation.
        report = apply_patch_report(
            patch_text,
            cwd=cwd,
            resolve_path=lambda path: _resolve_edit_path(
                context,
                path,
                tool_name='patch_apply',
            ),
            authority=PatchExecutionAuthority(permissions=permissions, cwd=cwd),
        )
    except (PatchError, OSError, ValueError) as exc:
        if isinstance(exc, PatchError):
            return _patch_error_result(context, exc, operation_count=len(operations), touched_paths=patch_paths)
        if isinstance(exc, PermissionError):
            return ToolResult(
                ok=False,
                tool_name='patch_apply',
                error=str(exc),
                stderr=str(exc),
                data={
                    'failure_class': 'filesystem_permission_error',
                    'failure_kind': 'permission_denied',
                    'operation_count': len(operations),
                    'touched_paths': [str(path) for path in patch_paths],
                    'mutation_applied': False,
                    'mutation_state': 'none',
                    'retry_same_tool': True,
                    'preserve_transaction': True,
                    'recommended_next_action': (
                        'Inspect the denied filesystem path and retry the complete patch only after the '
                        'effective permission or target path has been corrected.'
                    ),
                },
                metadata=metadata_for_context(
                    context,
                    failure_class='filesystem_permission_error',
                    failure_kind='permission_denied',
                ),
            )
        return _patch_input_failure_result(
            context,
            error=str(exc),
            failure_kind='filesystem_error',
            recommended_next_action=(
                'Inspect the filesystem failure and all affected paths before deciding whether the full '
                'transaction can be retried with patch_apply.'
            ),
            retry_same_tool=False,
        )
    delta = report.changes
    excerpt, truncated = truncate_text(patch_text, limit=MAX_DIFF_PREVIEW_CHARS)
    lsp_notified = 0
    affected_paths = _patch_affected_paths(delta)
    patch_filesystem = LocalPatchFileSystem(
        authority=PatchExecutionAuthority(permissions=permissions, cwd=cwd),
    )
    for change in delta:
        if change.kind in {'delete', 'move'}:
            _invalidate_patch_read_file_state(context, change.path)
    for patch_path in affected_paths:
        try:
            identity = patch_filesystem.identity(patch_path)
        except (PatchPolicyError, PermissionError) as exc:
            failure_class = (
                'filesystem_permission_error'
                if isinstance(exc, PatchPolicyError)
                else 'filesystem_os_error'
            )
            return ToolResult(
                ok=False,
                tool_name='patch_apply',
                error=str(exc),
                stderr=str(exc),
                data={
                    'failure_class': failure_class,
                    'failure_kind': 'post_commit_read_denied',
                    'mutation_applied': True,
                    'mutation_state': 'committed',
                    'retry_same_tool': False,
                    'preserve_transaction': False,
                    'recommended_next_action': (
                        'The patch was committed, but read-state refresh was denied. Inspect the committed '
                        'paths before continuing; do not blindly replay the patch.'
                    ),
                },
                metadata=metadata_for_context(
                    context,
                    failure_class=failure_class,
                    failure_kind='post_commit_read_denied',
                ),
            )
        if not identity.exists or identity.kind != 'file':
            continue
        content = patch_filesystem.read_text(patch_path)
        if context.state_store is not None:
            remember_read_file_state(
                state_store=context.state_store,
                entry=build_read_file_state_entry_from_content(
                    path=patch_path,
                    content=content,
                    stage=context.stage.value,
                    attempt=context.attempt,
                    continuation_index=context.continuation_index,
                    query_chain_id=str(getattr(context, 'query_chain_id', '') or '').strip(),
                    query_chain_depth=int(getattr(context, 'query_chain_depth', 0) or 0),
                    source='patch_apply',
                ),
            )
    # Finish every read-state update before cancellable ancillary notifications.
    # A cancelled notification cannot undo the committed patch or its receipts.
    for patch_path in affected_paths:
        try:
            lsp_notified += await _notify_lsp_file_saved(context, patch_path)
        except asyncio.CancelledError:
            break
    delta_payload = [_patch_change_payload(change) for change in delta]
    return ToolResult(
        ok=True,
        tool_name='patch_apply',
        data={
            'patch_excerpt': excerpt,
            'patch_truncated': truncated,
            'touched_paths': [str(path) for path in affected_paths],
            'changes': delta_payload,
            'operation_count': report.operation_count,
            'parser_mode': _PATCH_PARSER_MODE,
            'filesystem_kind': _PATCH_FILESYSTEM_KIND,
            'delta_exact': report.delta_exact,
            'rollback_status': report.rollback_status,
            'rollback_errors': list(report.rollback_errors),
            'lsp_notified_session_count': lsp_notified,
        },
        stdout=f'patch_apply changes={len(delta_payload)}\n{delta_payload}\n{excerpt}',
        exit_code=0,
        metadata=metadata_for_context(context, cwd=str(cwd), touched_paths=[str(path) for path in affected_paths]),
    )


def _patch_error_result(
    context: ToolExecutionContext,
    error: PatchError,
    *,
    operation_count: int = 0,
    touched_paths: list[Path] | None = None,
) -> ToolResult:
    recovery = _patch_error_recovery(error)
    data: dict[str, Any] = {
        'operation_count': operation_count,
        'touched_paths': [str(path) for path in (touched_paths or [])],
        'parser_mode': _PATCH_PARSER_MODE,
        'filesystem_kind': _PATCH_FILESYSTEM_KIND,
        'delta_exact': error.delta_exact,
        'rollback_status': error.rollback_status,
        'rollback_errors': list(error.rollback_errors),
        **recovery,
    }
    if error.line_number is not None:
        data['line_number'] = error.line_number
    if error.operation_index is not None:
        data['operation_index'] = error.operation_index
    if error.path is not None:
        data['path'] = str(error.path)
    return ToolResult(
        ok=False,
        tool_name='patch_apply',
        error=str(error),
        stderr=str(error),
        data=data,
        metadata=metadata_for_context(
            context,
            operation_count=operation_count,
            delta_exact=error.delta_exact,
            rollback_status=error.rollback_status,
        ),
    )


def _patch_input_failure_result(
    context: ToolExecutionContext,
    *,
    error: str,
    failure_kind: str,
    recommended_next_action: str,
    retry_same_tool: bool = True,
) -> ToolResult:
    return ToolResult(
        ok=False,
        tool_name='patch_apply',
        error=error,
        stderr=error,
        data={
            'failure_class': 'patch_input_error',
            'failure_kind': failure_kind,
            'mutation_applied': False,
            'mutation_state': 'none',
            'retry_same_tool': retry_same_tool,
            'preserve_transaction': retry_same_tool,
            'recommended_next_action': recommended_next_action,
        },
        metadata=metadata_for_context(context),
    )


def _patch_permission_failure_result(
    context: ToolExecutionContext,
    *,
    error: str,
    touched_paths: list[Path] | None = None,
) -> ToolResult:
    return ToolResult(
        ok=False,
        tool_name='patch_apply',
        error=error,
        stderr=error,
        data={
            'failure_class': 'filesystem_permission_error',
            'failure_kind': 'permission_denied',
            'operation_count': 0,
            'touched_paths': [str(path) for path in (touched_paths or [])],
            'mutation_applied': False,
            'mutation_state': 'none',
            'retry_same_tool': True,
            'preserve_transaction': True,
            'recommended_next_action': (
                'Inspect the denied filesystem path and retry the complete patch only after the '
                'effective permission or target path has been corrected.'
            ),
        },
        metadata=metadata_for_context(
            context,
            failure_class='filesystem_permission_error',
            failure_kind='permission_denied',
        ),
    )


def _patch_error_recovery(error: PatchError) -> dict[str, Any]:
    failure_kind = error.failure_kind
    if failure_kind in {'permission_denied', 'os_permission_denied'} and error.rollback_status != 'partial':
        return {
            'failure_class': (
                'filesystem_permission_error'
                if failure_kind == 'permission_denied'
                else 'filesystem_os_error'
            ),
            'failure_kind': failure_kind,
            'mutation_applied': False,
            'mutation_state': 'none',
            'retry_same_tool': True,
            'preserve_transaction': True,
            'recommended_next_action': (
                'Inspect the denied filesystem path and retry the complete patch only after the '
                'effective permission or target path has been corrected.'
            ),
        }
    if error.rollback_status == 'partial':
        return {
            'failure_class': 'patch_state_uncertain',
            'failure_kind': failure_kind,
            'mutation_applied': None,
            'mutation_state': 'possibly_partial',
            'retry_same_tool': False,
            'preserve_transaction': True,
            'recommended_next_action': (
                'Inspect every touched path and reconcile partial rollback state before rebuilding the full '
                'transaction. Do not retry or decompose the patch until the filesystem state is known.'
            ),
        }
    if error.rollback_status == 'complete':
        return {
            'failure_class': 'patch_commit_error',
            'failure_kind': failure_kind,
            'mutation_applied': False,
            'mutation_state': 'rolled_back',
            'retry_same_tool': True,
            'preserve_transaction': True,
            'recommended_next_action': (
                'The patch was rolled back completely. Inspect the commit failure, re-read all affected '
                'targets, and retry the corrected full patch if the transaction remains valid.'
            ),
        }

    action_by_kind = {
        'invalid_patch': (
            'Correct the Begin Patch syntax using the reported line and operation, then retry patch_apply '
            'with the complete transaction.'
        ),
        'no_changes': (
            'Revise the patch so it contains the intended operations, then retry patch_apply with the '
            'complete transaction.'
        ),
        'invalid_target': (
            'Inspect the rejected target, select the intended writable regular-file path, and retry '
            'patch_apply with the complete transaction.'
        ),
        'path_already_exists': (
            'No mutation occurred. Read the existing target and use Update File with matching context; '
            'do not retry Add File on the same path.'
        ),
        'path_not_found': (
            'No mutation occurred. Re-read the affected targets, correct the invalid path, and resubmit '
            'the complete patch with patch_apply if the operations still form one transaction.'
        ),
        'context_mismatch': (
            'No mutation occurred. Re-read the affected targets, rebuild stale or ambiguous hunks against '
            'current content, and resubmit the complete patch with patch_apply.'
        ),
        'concurrent_modification': (
            'No patch mutation was retained. Re-read every affected target, rebuild the full patch against '
            'current content, and retry patch_apply only after concurrent changes are reconciled.'
        ),
    }
    recommended_next_action = action_by_kind.get(failure_kind)
    retry_same_tool = recommended_next_action is not None
    if failure_kind == 'unsupported_filesystem':
        recommended_next_action = (
            'Use a host-local filesystem with atomic replace support. Do not retry patch_apply unchanged '
            'in the unsupported environment.'
        )
    elif recommended_next_action is None:
        recommended_next_action = (
            'Inspect the patch failure and affected targets before choosing a corrective action. Retry '
            'patch_apply only if the complete transaction remains appropriate.'
        )
    return {
        'failure_class': 'patch_preflight_error',
        'failure_kind': failure_kind,
        'mutation_applied': False,
        'mutation_state': 'none',
        'retry_same_tool': retry_same_tool,
        'preserve_transaction': retry_same_tool,
        'recommended_next_action': recommended_next_action,
    }


def _patch_environment_identities(context: ToolExecutionContext) -> set[str]:
    descriptor = context.environment_descriptor if isinstance(context.environment_descriptor, dict) else {}
    return {
        value
        for value in (
            'local' if context.environment_provider_kind == 'host_local' else '',
            str(context.environment_provider_kind or '').strip(),
            str(descriptor.get('environment_id') or '').strip(),
            str(descriptor.get('env_name') or '').strip(),
            str(descriptor.get('target_env_name') or '').strip(),
        )
        if value
    }





def _unified_diff(before: str, after: str, *, fromfile: str, tofile: str) -> str:
    return ''.join(
        difflib.unified_diff(
            before.splitlines(keepends=True),
            after.splitlines(keepends=True),
            fromfile=fromfile,
            tofile=tofile,
        )
    )


def _patch_affected_paths(changes: tuple[PatchChange, ...]) -> list[Path]:
    paths: list[Path] = []
    for change in changes:
        for path in (change.path, change.destination):
            if path is not None and path not in paths:
                paths.append(path)
    return paths


def _patch_change_payload(change: PatchChange) -> dict[str, Any]:
    return {
        'kind': change.kind,
        'path': str(change.path),
        'destination': str(change.destination) if change.destination is not None else None,
        'before_exists': change.old_content is not None,
        'after_exists': change.new_content is not None,
        'destination_before_exists': change.destination_old_content is not None,
        'before_bytes': len(change.old_content.encode('utf-8')) if change.old_content is not None else 0,
        'after_bytes': len(change.new_content.encode('utf-8')) if change.new_content is not None else 0,
        'destination_before_bytes': (
            len(change.destination_old_content.encode('utf-8'))
            if change.destination_old_content is not None
            else 0
        ),
    }


def _invalidate_patch_read_file_state(context: ToolExecutionContext, path: Path) -> None:
    state_store = context.state_store
    tool_state = getattr(getattr(state_store, 'state', None), 'tool_state', None)
    read_file_state = getattr(tool_state, 'read_file_state', None)
    if not isinstance(read_file_state, dict):
        return
    removed = read_file_state.pop(str(path.resolve()), None)
    if removed is not None and hasattr(state_store, '_touch'):
        state_store._touch()
