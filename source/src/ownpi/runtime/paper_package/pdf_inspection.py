from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import shutil
from typing import Any, Mapping, Sequence

from ownpi.runtime.environment.subprocess_env import build_subprocess_env
from ownpi.runtime.paper_package.latex_build import LatexBuildReceipt
from ownpi.runtime.paper_package.paths import (
    PaperPathError,
    resolve_contained_path,
)
from ownpi.runtime.permissions.action_compiler import OperationEffects
from ownpi.runtime.permissions.models import EffectivePermissions, FileSystemAccessMode
from ownpi.runtime.permissions.orchestrator import FileSystemAccessRequest
from ownpi.runtime.permissions.runtime_config import default_runtime_permissions
from ownpi.runtime.sandbox import SandboxCommandRequest, SandboxManager
from ownpi.runtime.tools.errors import ToolPreflightValidationError


PDF_INSPECTION_RECEIPT_SCHEMA_VERSION = 1
_UNRESOLVED_RE = re.compile(r'\?\?|\b(?:TODO|TBD)\b|undefined citation|undefined reference', re.IGNORECASE)


@dataclass(frozen=True)
class PdfInspectionReceipt:
    pdf_ref: dict[str, Any]
    build_receipt_id: str
    page_count: int
    pages: tuple[dict[str, Any], ...]
    rendered_page_refs: tuple[dict[str, Any], ...]
    diagnostics: tuple[dict[str, Any], ...]
    expected_text_checks: tuple[dict[str, Any], ...]
    visual_status: str
    status: str
    created_at: str
    schema_version: int = PDF_INSPECTION_RECEIPT_SCHEMA_VERSION
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
            'pdf_ref': dict(self.pdf_ref),
            'build_receipt_id': self.build_receipt_id,
            'page_count': self.page_count,
            'pages': [dict(item) for item in self.pages],
            'rendered_page_refs': [dict(item) for item in self.rendered_page_refs],
            'diagnostics': [dict(item) for item in self.diagnostics],
            'expected_text_checks': [dict(item) for item in self.expected_text_checks],
            'visual_status': self.visual_status,
            'status': self.status,
            'created_at': self.created_at,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any], *, verify_hash: bool = True) -> 'PdfInspectionReceipt':
        receipt = cls(
            schema_version=int(data.get('schema_version', 0) or 0),
            pdf_ref=dict(data.get('pdf_ref', {}) or {}),
            build_receipt_id=str(data.get('build_receipt_id', '') or ''),
            page_count=int(data.get('page_count', 0) or 0),
            pages=tuple(dict(item) for item in data.get('pages', []) or []),
            rendered_page_refs=tuple(dict(item) for item in data.get('rendered_page_refs', []) or []),
            diagnostics=tuple(dict(item) for item in data.get('diagnostics', []) or []),
            expected_text_checks=tuple(dict(item) for item in data.get('expected_text_checks', []) or []),
            visual_status=str(data.get('visual_status', '') or ''),
            status=str(data.get('status', '') or ''),
            created_at=str(data.get('created_at', '') or ''),
            receipt_id=str(data.get('receipt_id', '') or ''),
            content_hash=str(data.get('content_hash', '') or ''),
        )
        if receipt.schema_version != PDF_INSPECTION_RECEIPT_SCHEMA_VERSION:
            raise ValueError(f'unsupported PDF inspection schema_version: {receipt.schema_version}')
        expected = _hash_payload(receipt._payload() | {'receipt_id': receipt.receipt_id})
        if verify_hash and (not receipt.content_hash or receipt.content_hash != expected):
            raise ValueError('PDF inspection receipt content hash mismatch')
        return receipt


async def inspect_pdf(
    *,
    workspace: Path,
    pdf_path: str,
    build_receipt: Mapping[str, Any] | None = None,
    expected_texts: Sequence[str] = (),
    visual_assessment: Mapping[str, Any] | None = None,
    effective_permissions: EffectivePermissions | None = None,
) -> PdfInspectionReceipt:
    root = workspace.resolve()
    pdf = resolve_contained_path(root, pdf_path, field_name='pdf_path')
    if not pdf.exists() or not pdf.is_file() or not pdf.read_bytes().startswith(b'%PDF'):
        raise ValueError(f'valid PDF does not exist: {pdf_path}')
    diagnostics: list[dict[str, Any]] = []
    build_id = ''
    if build_receipt:
        build = LatexBuildReceipt.from_dict(build_receipt)
        build_id = build.receipt_id
        if build.pdf_ref.get('sha256') != _sha256(pdf):
            diagnostics.append({'code': 'pdf_build_receipt_hash_mismatch'})
        diagnostics.extend(
            {'code': 'pdf_build_layout_overflow', 'message': str(item.get('message', ''))[:300]}
            for item in build.diagnostics
            if item.get('category') == 'layout_overflow'
        )
    pages, combined_text = _inspect_pages(pdf, diagnostics=diagnostics)
    text_checks = tuple(
        {'text': str(value)[:240], 'present': _normalized(value) in _normalized(combined_text)}
        for value in expected_texts
        if str(value).strip()
    )
    diagnostics.extend(
        {'code': 'expected_pdf_text_missing', 'text': item['text']}
        for item in text_checks
        if not item['present']
    )
    render_refs = await _render_pages(
        root=root,
        pdf=pdf,
        diagnostics=diagnostics,
        effective_permissions=(
            effective_permissions or default_runtime_permissions(project_root=root)
        ),
    )
    visual = dict(visual_assessment or {})
    expected_hashes = {str(item['sha256']) for item in render_refs}
    inspected_hashes = {str(item) for item in visual.get('inspected_render_hashes', []) or []}
    visual_status = 'pending'
    if visual:
        visual_status = 'passed' if visual.get('status') == 'passed' else 'failed'
        if expected_hashes != inspected_hashes:
            diagnostics.append({'code': 'pdf_visual_render_hash_mismatch'})
            visual_status = 'failed'
        for finding in visual.get('findings', []) or []:
            diagnostics.append({'code': 'pdf_visual_finding', 'finding': str(finding)[:240]})
            visual_status = 'failed'
    mechanical_errors = bool(diagnostics)
    status = 'passed' if not mechanical_errors and visual_status == 'passed' else (
        'inspection_required' if not mechanical_errors and visual_status == 'pending' else 'failed'
    )
    draft = PdfInspectionReceipt(
        pdf_ref=_file_ref(root, pdf),
        build_receipt_id=build_id,
        page_count=len(pages),
        pages=tuple(pages),
        rendered_page_refs=tuple(render_refs),
        diagnostics=tuple(diagnostics),
        expected_text_checks=text_checks,
        visual_status=visual_status,
        status=status,
        created_at=datetime.now(timezone.utc).isoformat(),
    )
    payload = draft._payload()
    receipt_id = _receipt_id(payload)
    return PdfInspectionReceipt(
        **{**draft.__dict__, 'receipt_id': receipt_id, 'content_hash': _hash_payload(payload | {'receipt_id': receipt_id})}
    )


def _inspect_pages(
    pdf: Path,
    *,
    diagnostics: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], str]:
    import pymupdf

    pages: list[dict[str, Any]] = []
    text_parts: list[str] = []
    with pymupdf.open(str(pdf)) as document:
        for index, page in enumerate(document, start=1):
            text = page.get_text('text') or ''
            text_parts.append(text)
            images = page.get_images(full=True)
            blocks = page.get_text('blocks') or []
            rect = page.rect
            clipped = any(
                float(block[0]) < -1 or float(block[1]) < -1 or float(block[2]) > rect.width + 1 or float(block[3]) > rect.height + 1
                for block in blocks
                if len(block) >= 4
            )
            unresolved = sorted(set(match.group(0) for match in _UNRESOLVED_RE.finditer(text)))
            blank = len(' '.join(text.split())) < 10 and not images
            broken_images = 0
            for image in images:
                try:
                    document.extract_image(int(image[0]))
                except Exception:
                    broken_images += 1
            page_summary = {
                'page': index,
                'width_points': round(rect.width, 2),
                'height_points': round(rect.height, 2),
                'text_chars': len(text),
                'image_count': len(images),
                'blank': blank,
                'clipped': clipped,
                'broken_image_count': broken_images,
                'unresolved_markers': unresolved,
            }
            pages.append(page_summary)
            if blank:
                diagnostics.append({'code': 'pdf_blank_page', 'page': index})
            if clipped:
                diagnostics.append({'code': 'pdf_content_clipped', 'page': index})
            if broken_images:
                diagnostics.append({'code': 'pdf_broken_image', 'page': index, 'count': broken_images})
            if unresolved:
                diagnostics.append(
                    {
                        'code': 'pdf_unresolved_marker',
                        'page': index,
                        'markers': unresolved,
                    }
                )
    return pages, '\n'.join(text_parts)


async def _render_pages(
    *,
    root: Path,
    pdf: Path,
    diagnostics: list[dict[str, Any]],
    effective_permissions: EffectivePermissions,
) -> list[dict[str, Any]]:
    pdftoppm = shutil.which('pdftoppm')
    if not pdftoppm:
        diagnostics.append({'code': 'pdf_render_dependency_missing', 'dependency': 'pdftoppm'})
        return []
    render_dir = pdf.parent / '.authoring' / 'renders' / _sha256(pdf)[:12]
    render_dir.mkdir(parents=True, exist_ok=True)
    prefix = render_dir / 'page'
    command = (
        pdftoppm,
        '-png',
        '-r',
        '120',
        str(pdf),
        str(prefix),
    )
    completed = await SandboxManager().execute_async(
        SandboxCommandRequest(
            argv=command,
            cwd=pdf.parent.resolve(),
            env=build_subprocess_env(),
            effective_permissions=effective_permissions,
            timeout_seconds=120,
        )
    )
    if completed.exit_code != 0:
        diagnostics.append(
            {
                'code': 'pdf_render_failed',
                'message': ' '.join(completed.stderr.split())[:300],
            }
        )
        return []
    return [_file_ref(root, path) for path in sorted(render_dir.glob('page-*.png'))]


def resolve_pdf_inspection_effects(
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
        pdf = resolve_contained_path(
            root,
            str(arguments.get('pdf_path', '') or ''),
            field_name='pdf_path',
        )
    except PaperPathError as exc:
        raise ToolPreflightValidationError(
            str(exc),
            validation_code=f'pdf_inspect_{exc.field_name}_invalid',
            compact_hint=(
                'Use a path inside the active workspace. workspace_path is based on the '
                'runtime workspace and pdf_path is based on workspace_path.'
            ),
            field_name=exc.field_name,
        ) from exc
    pdftoppm = shutil.which('pdftoppm')
    accesses = [
        FileSystemAccessRequest(pdf, FileSystemAccessMode.READ),
        FileSystemAccessRequest(pdf.parent / '.authoring', FileSystemAccessMode.WRITE),
    ]
    if not pdftoppm:
        return OperationEffects.declared(
            file_system_accesses=tuple(accesses),
            source='pdf_inspection_files',
        )
    executable = Path(pdftoppm).resolve()
    accesses.append(FileSystemAccessRequest(executable, FileSystemAccessMode.READ))
    return OperationEffects.declared(
        file_system_accesses=tuple(accesses),
        process_required=True,
        process_argv=(
            pdftoppm,
            '-png',
            '-r',
            '120',
            str(pdf),
            str(pdf.parent / '.authoring' / 'renders'),
        ),
        source='pdf_render_process',
    )


def _file_ref(root: Path, path: Path) -> dict[str, Any]:
    ref: dict[str, Any] = {
        'path': path.resolve().relative_to(root).as_posix(),
        'sha256': _sha256(path),
        'size_bytes': path.stat().st_size,
    }
    if path.suffix.lower() == '.png':
        from PIL import Image

        with Image.open(path) as image:
            ref.update(width=image.width, height=image.height)
    return ref


def _normalized(value: Any) -> str:
    return ' '.join(str(value or '').casefold().split())


def _sha256(path: Path) -> str:
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
    return f'pdf-inspection-{_hash_payload(payload)[:20]}'
