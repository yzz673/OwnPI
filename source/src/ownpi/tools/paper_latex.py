from __future__ import annotations

from typing import TYPE_CHECKING, Any, Mapping

from ownpi.runtime.contracts import ArtifactRef, ToolResult
from ownpi.runtime.paper_package.paths import resolve_contained_path
from ownpi.runtime.permissions.models import EffectivePermissions
from ownpi.runtime.permissions.runtime_config import default_runtime_permissions
from ownpi.runtime.paper_package.latex_build import run_latex_build
from ownpi.runtime.paper_package.pdf_inspection import inspect_pdf
from ownpi.tools.common import clamp_int, metadata_for_context

if TYPE_CHECKING:
    from ownpi.runtime.tools.definitions import ToolExecutionContext


async def run_latex_build_tool(context: ToolExecutionContext, arguments: dict[str, Any]) -> ToolResult:
    try:
        workspace = resolve_contained_path(
            context.project_root,
            str(arguments.get('workspace_path', '') or ''),
            field_name='workspace_path',
        )
        receipt = await run_latex_build(
            workspace=workspace,
            project_dir=str(arguments.get('project_dir', '') or ''),
            entry_point=str(arguments.get('entry_point', '') or ''),
            engine=str(arguments.get('engine', 'pdflatex') or 'pdflatex'),
            timeout_seconds=clamp_int(arguments.get('timeout_seconds'), default=120, minimum=1, maximum=600),
            effective_permissions=_canonical_permissions(context),
        )
    except PermissionError as exc:
        return ToolResult(
            ok=False, tool_name='latex_build', data={'outcome': 'permission_denied'},
            error=str(exc), metadata=metadata_for_context(context),
        )
    except (OSError, RuntimeError, TypeError, ValueError) as exc:
        return _error(context, 'latex_build', str(exc))
    artifacts = []
    if receipt.pdf_ref:
        artifacts.append(
            ArtifactRef(
                artifact_id=f'latex-pdf:{receipt.receipt_id}',
                artifact_type='latex_pdf',
                file_path=str(workspace / str(receipt.pdf_ref['path'])),
            )
        )
    data = {'outcome': receipt.status, 'build_receipt': receipt.to_dict()}
    return ToolResult(
        ok=True,
        tool_name='latex_build',
        data=data,
        stdout=(
            f'outcome={receipt.status}\nfailure_class={receipt.failure_class}\n'
            f'diagnostics={len(receipt.diagnostics)}\n'
        ),
        artifacts=artifacts,
        metadata=metadata_for_context(
            context,
            latex_build_status=receipt.status,
            latex_failure_class=receipt.failure_class,
            latex_build_receipt_id=receipt.receipt_id,
        ),
        warnings=[str(item.get('category', 'latex_diagnostic')) for item in receipt.diagnostics],
    )


async def run_pdf_inspect_tool(context: ToolExecutionContext, arguments: dict[str, Any]) -> ToolResult:
    build_receipt = arguments.get('build_receipt')
    visual = arguments.get('visual_assessment')
    if build_receipt is not None and not isinstance(build_receipt, Mapping):
        return _error(context, 'pdf_inspect', 'build_receipt must be an object')
    if visual is not None and not isinstance(visual, Mapping):
        return _error(context, 'pdf_inspect', 'visual_assessment must be an object')
    try:
        workspace = resolve_contained_path(
            context.project_root,
            str(arguments.get('workspace_path', '') or ''),
            field_name='workspace_path',
        )
        receipt = await inspect_pdf(
            workspace=workspace,
            pdf_path=str(arguments.get('pdf_path', '') or ''),
            build_receipt=build_receipt,
            expected_texts=tuple(str(item) for item in arguments.get('expected_texts', []) or []),
            visual_assessment=visual,
            effective_permissions=_canonical_permissions(context),
        )
    except (OSError, RuntimeError, TypeError, ValueError) as exc:
        return _error(context, 'pdf_inspect', str(exc))
    artifacts = [
        ArtifactRef(
            artifact_id=f"pdf-render:{receipt.receipt_id}:{index}",
            artifact_type='pdf_render_page',
            file_path=str(workspace / str(item['path'])),
        )
        for index, item in enumerate(receipt.rendered_page_refs, start=1)
    ]
    data = {'outcome': receipt.status, 'inspection_receipt': receipt.to_dict()}
    return ToolResult(
        ok=True,
        tool_name='pdf_inspect',
        data=data,
        stdout=f'outcome={receipt.status}\npages={receipt.page_count}\ndiagnostics={len(receipt.diagnostics)}\n',
        artifacts=artifacts,
        metadata=metadata_for_context(
            context,
            pdf_inspection_status=receipt.status,
            pdf_inspection_receipt_id=receipt.receipt_id,
            pdf_page_count=receipt.page_count,
        ),
        warnings=[str(item.get('code', 'pdf_diagnostic')) for item in receipt.diagnostics],
    )


def _error(context: ToolExecutionContext, tool_name: str, message: str) -> ToolResult:
    return ToolResult(
        ok=False,
        tool_name=tool_name,
        data={'outcome': 'invalid_input'},
        error=' '.join(str(message).split())[:320],
        metadata=metadata_for_context(context),
    )


def _canonical_permissions(context: ToolExecutionContext) -> EffectivePermissions:
    value = getattr(context, 'effective_permissions', None)
    if isinstance(value, EffectivePermissions):
        return value
    state = getattr(getattr(context, 'state_store', None), 'state', None)
    value = getattr(getattr(state, 'tool_state', None), 'effective_permissions', None)
    if isinstance(value, EffectivePermissions):
        return value
    return default_runtime_permissions(project_root=context.project_root)
