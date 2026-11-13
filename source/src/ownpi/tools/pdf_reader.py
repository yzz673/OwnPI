from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ownpi.runtime.environment.subprocess_env import build_subprocess_env
from ownpi.runtime.limits.projection import (
    MAX_PDF_PAGE_SNIPPET_CHARS,
    MAX_PDF_PREVIEW_CHARS,
)
from ownpi.runtime.limits.resources import MAX_PDF_ISOLATED_RESULT_BYTES
from ownpi.runtime.permissions.action_compiler import OperationEffects
from ownpi.runtime.permissions.models import EffectivePermissions, FileSystemAccessMode
from ownpi.runtime.permissions.orchestrator import FileSystemAccessRequest
from ownpi.runtime.permissions.runtime_config import default_runtime_permissions
from ownpi.runtime.sandbox import SandboxCommandRequest, SandboxManager
from ownpi.runtime.sandbox.denial import DenialOutputEvidence, is_sandbox_denied
from ownpi.tools.common import metadata_for_context, resolve_workspace_path, truncate_text

if TYPE_CHECKING:
    from ownpi.runtime.contracts import ToolResult
    from ownpi.runtime.tools.definitions import ToolExecutionContext


_TEXT_READABLE_THRESHOLD = 40
_OCR_TIMEOUT_SECONDS = 30
_OCR_LANGUAGE = "eng"
_TOOL_PREVIEW_CHARS = 16000
_REMOTE_PDF_TIMEOUT_SECONDS = 30
_REMOTE_PDF_CPU_SECONDS = 20
_REMOTE_PDF_ADDRESS_SPACE_BYTES = 1_073_741_824


@dataclass(frozen=True)
class PDFPageStatus:
    page_index: int
    char_count: int
    readable: bool
    snippet: str
    text: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "page_index": self.page_index,
            "char_count": self.char_count,
            "readable": self.readable,
            "snippet": self.snippet,
        }


@dataclass(frozen=True)
class PDFExtractionResult:
    source_path: str
    extractor: str
    page_count: int
    readable_pages: int
    pages: list[PDFPageStatus]
    combined_text: str
    notes: list[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_path": self.source_path,
            "extractor": self.extractor,
            "page_count": self.page_count,
            "readable_pages": self.readable_pages,
            "pages": [page.to_dict() for page in self.pages],
            "combined_text": self.combined_text,
            "notes": list(self.notes),
        }


@dataclass(frozen=True)
class PDFToolStatus:
    available_extractors: list[str]
    preferred_extractor: str | None
    tesseract_available: bool
    scanned_pdf_support: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "available_extractors": list(self.available_extractors),
            "preferred_extractor": self.preferred_extractor,
            "tesseract_available": self.tesseract_available,
            "scanned_pdf_support": self.scanned_pdf_support,
        }


def _classify_page_text(text: str) -> tuple[bool, str, int]:
    cleaned = " ".join(text.split())
    snippet = cleaned[:MAX_PDF_PAGE_SNIPPET_CHARS]
    return len(cleaned) >= _TEXT_READABLE_THRESHOLD, snippet, len(cleaned)


def _append_combined_text(
    combined_parts: list[str],
    text: str,
    *,
    page_index: int,
    current_length: int,
) -> int:
    if not text or current_length >= MAX_PDF_PREVIEW_CHARS:
        return current_length
    snippet = f"\n\n[Page {page_index}]\n{text.strip()}"
    combined_parts.append(snippet)
    return current_length + len(snippet)


async def _extract_with_pypdf(
    pdf_path: Path,
    *,
    max_pages: int | None = None,
    start_page: int = 1,
    max_extracted_chars: int | None = None,
    enable_ocr: bool = True,
    effective_permissions: EffectivePermissions | None = None,
) -> PDFExtractionResult:
    del enable_ocr, effective_permissions
    from pypdf import PdfReader

    reader = PdfReader(str(pdf_path))
    page_statuses: list[PDFPageStatus] = []
    combined_parts: list[str] = []
    combined_length = 0
    notes: list[str] = []
    readable_pages = 0

    for page_index, page in enumerate(reader.pages, start=1):
        if page_index < start_page:
            continue
        if max_pages is not None and page_index >= start_page + max_pages:
            notes.append(f'page_limit_applied:{max_pages}')
            break
        if max_extracted_chars is not None and sum(item.char_count for item in page_statuses) >= max_extracted_chars:
            notes.append(f'character_limit_applied:{max_extracted_chars}')
            break
        try:
            text = page.extract_text() or ""
        except Exception as exc:
            text = ""
            notes.append(f"page_{page_index}_extract_error: {type(exc).__name__}")
        if max_extracted_chars is not None:
            remaining = max(0, max_extracted_chars - sum(item.char_count for item in page_statuses))
            if len(text) > remaining:
                notes.append(f'page_{page_index}_text_truncated_at:{remaining}')
            text = text[:remaining]
        readable, snippet, char_count = _classify_page_text(text)
        if readable:
            readable_pages += 1
        page_statuses.append(
            PDFPageStatus(
                page_index=page_index,
                char_count=char_count,
                readable=readable,
                snippet=snippet,
                text=text,
            )
        )
        combined_length = _append_combined_text(
            combined_parts,
            text,
            page_index=page_index,
            current_length=combined_length,
        )

    return PDFExtractionResult(
        source_path=str(pdf_path),
        extractor="pypdf",
        page_count=len(reader.pages),
        readable_pages=readable_pages,
        pages=page_statuses,
        combined_text="".join(combined_parts)[:MAX_PDF_PREVIEW_CHARS],
        notes=notes,
    )


async def _extract_with_pymupdf(
    pdf_path: Path,
    *,
    max_pages: int | None = None,
    start_page: int = 1,
    max_extracted_chars: int | None = None,
    enable_ocr: bool = True,
    effective_permissions: EffectivePermissions | None = None,
) -> PDFExtractionResult:
    import pymupdf

    page_statuses: list[PDFPageStatus] = []
    combined_parts: list[str] = []
    combined_length = 0
    notes: list[str] = []
    readable_pages = 0
    tesseract_available = shutil.which("tesseract") is not None
    ocr_unavailable_recorded = False

    with pymupdf.open(str(pdf_path)) as document:
        for page_index, page in enumerate(document, start=1):
            if page_index < start_page:
                continue
            if max_pages is not None and page_index >= start_page + max_pages:
                notes.append(f'page_limit_applied:{max_pages}')
                break
            if (
                max_extracted_chars is not None
                and sum(item.char_count for item in page_statuses) >= max_extracted_chars
            ):
                notes.append(f'character_limit_applied:{max_extracted_chars}')
                break
            try:
                text = page.get_text("text") or ""
            except Exception as exc:
                text = ""
                notes.append(f"page_{page_index}_extract_error: {type(exc).__name__}")

            initial_readable, _, _ = _classify_page_text(text)
            if enable_ocr and not initial_readable:
                ocr_text = await _ocr_page_with_tesseract(
                    page,
                    effective_permissions=effective_permissions,
                )
                if ocr_text:
                    text = ocr_text
                    notes.append(f"page_{page_index}_ocr_used")
                elif not tesseract_available and not ocr_unavailable_recorded:
                    notes.append("ocr_unavailable: install system tesseract to improve image-only PDF extraction")
                    ocr_unavailable_recorded = True
            if max_extracted_chars is not None:
                remaining = max(0, max_extracted_chars - sum(item.char_count for item in page_statuses))
                if len(text) > remaining:
                    notes.append(f'page_{page_index}_text_truncated_at:{remaining}')
                text = text[:remaining]

            readable, snippet, char_count = _classify_page_text(text)
            if readable:
                readable_pages += 1
            page_statuses.append(
                PDFPageStatus(
                    page_index=page_index,
                    char_count=char_count,
                    readable=readable,
                    snippet=snippet,
                    text=text,
                )
            )
            combined_length = _append_combined_text(
                combined_parts,
                text,
                page_index=page_index,
                current_length=combined_length,
            )

        return PDFExtractionResult(
            source_path=str(pdf_path),
            extractor="pymupdf",
            page_count=document.page_count,
            readable_pages=readable_pages,
            pages=page_statuses,
            combined_text="".join(combined_parts)[:MAX_PDF_PREVIEW_CHARS],
            notes=notes,
        )


async def _ocr_page_with_tesseract(
    page: Any,
    *,
    effective_permissions: EffectivePermissions | None,
) -> str:
    if shutil.which("tesseract") is None:
        return ""

    try:
        import pymupdf
        pixmap = page.get_pixmap(matrix=pymupdf.Matrix(2, 2), alpha=False)
        image_bytes = pixmap.tobytes("png")
    except Exception:
        return ""

    temp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as temp_image:
            temp_path = Path(temp_image.name)
            temp_image.write(image_bytes)
            temp_image.flush()

        command = ('tesseract', str(temp_path), 'stdout', '-l', _OCR_LANGUAGE)
        completed = await SandboxManager().execute_async(
            SandboxCommandRequest(
                argv=command,
                cwd=temp_path.parent.resolve(),
                env=build_subprocess_env(),
                effective_permissions=(
                    effective_permissions
                    or default_runtime_permissions(project_root=temp_path.parent)
                ),
                timeout_seconds=_OCR_TIMEOUT_SECONDS,
            )
        )
    except Exception:
        return ""
    finally:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)

    if completed.exit_code != 0:
        return ""
    return completed.stdout.strip()


def inspect_pdf_tool_status() -> PDFToolStatus:
    available_extractors: list[str] = []
    for extractor_name, module_name in (("pymupdf", "pymupdf"), ("pypdf", "pypdf")):
        try:
            __import__(module_name)
        except ModuleNotFoundError:
            continue
        available_extractors.append(extractor_name)
    preferred_extractor = available_extractors[0] if available_extractors else None
    tesseract_available = shutil.which("tesseract") is not None
    return PDFToolStatus(
        available_extractors=available_extractors,
        preferred_extractor=preferred_extractor,
        tesseract_available=tesseract_available,
        scanned_pdf_support=(preferred_extractor == "pymupdf" and tesseract_available),
    )


async def extract_pdf_text(
    pdf_path: str | Path,
    *,
    max_pages: int | None = None,
    start_page: int = 1,
    max_extracted_chars: int | None = None,
    enable_ocr: bool = True,
    effective_permissions: EffectivePermissions | None = None,
) -> PDFExtractionResult:
    resolved = Path(pdf_path).expanduser()
    if not resolved.exists():
        raise FileNotFoundError(f"PDF file not found: {resolved}")

    extraction_errors: list[str] = []
    for extractor_name, extractor in (
        ("pymupdf", _extract_with_pymupdf),
        ("pypdf", _extract_with_pypdf),
    ):
        try:
            return await extractor(
                resolved,
                max_pages=max_pages,
                start_page=start_page,
                max_extracted_chars=max_extracted_chars,
                enable_ocr=enable_ocr,
                effective_permissions=effective_permissions,
            )
        except ModuleNotFoundError as exc:
            extraction_errors.append(f"{extractor_name}_missing:{exc.name}")
        except Exception as exc:
            extraction_errors.append(f"{extractor_name}_error:{type(exc).__name__}")

    raise RuntimeError(
        "No PDF extractor available. Install `pymupdf` or `pypdf` to enable paper summarization. "
        f"Attempted extractors: {', '.join(extraction_errors) if extraction_errors else 'none'}"
    )


async def extract_pdf_text_isolated(
    pdf_path: str | Path,
    *,
    max_pages: int,
    start_page: int = 1,
    max_extracted_chars: int,
    enable_ocr: bool = False,
    timeout_seconds: int = _REMOTE_PDF_TIMEOUT_SECONDS,
    effective_permissions: EffectivePermissions | None = None,
) -> PDFExtractionResult:
    resolved = Path(pdf_path).expanduser().resolve()
    if not resolved.is_file():
        raise FileNotFoundError(f"PDF file not found: {resolved}")
    source_root = Path(__file__).resolve().parents[2]
    existing_pythonpath = os.environ.get('PYTHONPATH', '')
    pythonpath = str(source_root)
    if existing_pythonpath:
        pythonpath = f'{pythonpath}{os.pathsep}{existing_pythonpath}'
    command = [
        sys.executable,
        '-m',
        'ownpi.tools.pdf_extract_worker',
        str(resolved),
        str(max_pages),
        str(max_extracted_chars),
        '1' if enable_ocr else '0',
        str(_REMOTE_PDF_CPU_SECONDS),
        str(_REMOTE_PDF_ADDRESS_SPACE_BYTES),
    ]
    if start_page != 1:
        command.append(str(start_page))
    completed = await SandboxManager().execute_async(
        SandboxCommandRequest(
            argv=tuple(command),
            cwd=source_root,
            env=build_subprocess_env(extra_env={'PYTHONPATH': pythonpath}),
            effective_permissions=(
                effective_permissions
                or default_runtime_permissions(project_root=resolved.parent)
            ),
            timeout_seconds=max(1, int(timeout_seconds)),
        )
    )
    if completed.timed_out:
        raise TimeoutError(f'remote PDF extraction exceeded {timeout_seconds}s')
    if completed.exit_code != 0:
        detail = completed.stderr.strip()
        evidence = DenialOutputEvidence()
        evidence.observe('stdout', completed.stdout)
        evidence.observe('stderr', completed.stderr)
        if is_sandbox_denied(
            sandbox_backend=completed.backend_kind, closed=True,
            exit_code=completed.exit_code, timed_out=completed.timed_out,
            matched_marker=evidence.matched_marker,
        ):
            raise PermissionError(evidence.output_excerpt or 'PDF extraction sandbox denied')
        raise RuntimeError(f'remote PDF extraction worker failed: {detail[-1000:] or completed.exit_code}')
    if len(completed.stdout.encode('utf-8')) > MAX_PDF_ISOLATED_RESULT_BYTES:
        raise RuntimeError('remote PDF extraction result exceeded the configured bound')
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError('remote PDF extraction worker returned invalid JSON') from exc
    return _extraction_from_isolated_payload(payload)


def _extraction_from_isolated_payload(payload: object) -> PDFExtractionResult:
    if not isinstance(payload, dict):
        raise RuntimeError('remote PDF extraction payload must be an object')
    raw_pages = payload.get('pages', [])
    if not isinstance(raw_pages, list):
        raise RuntimeError('remote PDF extraction pages must be a list')
    pages: list[PDFPageStatus] = []
    for raw_page in raw_pages:
        if not isinstance(raw_page, dict):
            continue
        text = str(raw_page.get('text', '') or '')
        pages.append(
            PDFPageStatus(
                page_index=int(raw_page.get('page_index', 0) or 0),
                char_count=int(raw_page.get('char_count', len(text)) or 0),
                readable=bool(raw_page.get('readable', False)),
                snippet=str(raw_page.get('snippet', '') or ''),
                text=text,
            )
        )
    return PDFExtractionResult(
        source_path=str(payload.get('source_path', '') or ''),
        extractor=str(payload.get('extractor', '') or ''),
        page_count=int(payload.get('page_count', len(pages)) or 0),
        readable_pages=int(payload.get('readable_pages', 0) or 0),
        pages=pages,
        combined_text=str(payload.get('combined_text', '') or ''),
        notes=[str(item) for item in payload.get('notes', []) if str(item)]
        if isinstance(payload.get('notes'), list)
        else [],
    )


def resolve_pdf_reader_effects(
    context: ToolExecutionContext,
    arguments: dict[str, Any],
) -> OperationEffects:
    dataset_path_override = getattr(context, 'dataset_path_override', None)
    raw_path = (
        arguments.get('file_path')
        or dataset_path_override
        or ''
    )
    if dataset_path_override and not str(raw_path).strip().lower().endswith('.pdf'):
        raw_path = dataset_path_override
    pdf_path = resolve_workspace_path(context.project_root, raw_path)
    accesses = [
        FileSystemAccessRequest(pdf_path, FileSystemAccessMode.READ),
    ]
    tesseract = shutil.which('tesseract')
    if not tesseract:
        return OperationEffects.declared(
            file_system_accesses=tuple(accesses),
            source='pdf_reader_files',
        )
    accesses.extend(
        (
            FileSystemAccessRequest(
                Path(tesseract).resolve(),
                FileSystemAccessMode.READ,
            ),
            FileSystemAccessRequest(
                Path(tempfile.gettempdir()).resolve(),
                FileSystemAccessMode.WRITE,
            ),
        )
    )
    return OperationEffects.declared(
        file_system_accesses=tuple(accesses),
        process_required=True,
        process_argv=(tesseract, str(pdf_path), 'stdout', '-l', _OCR_LANGUAGE),
        source='pdf_reader_ocr_process',
    )


async def run_pdf_reader(context: "ToolExecutionContext", arguments: dict[str, Any]) -> "ToolResult":
    from ownpi.runtime.contracts import ToolResult

    raw_path = (
        arguments.get("file_path")
        or context.dataset_path_override
        or ''
    )
    if context.dataset_path_override and not str(raw_path).strip().lower().endswith(".pdf"):
        raw_path = context.dataset_path_override
    pdf_path = resolve_workspace_path(context.project_root, raw_path)
    if not pdf_path.exists():
        return ToolResult(
            ok=False,
            tool_name="pdf_reader",
            error=f"PDF file not found: {pdf_path}",
            metadata=metadata_for_context(context, path=str(pdf_path)),
        )
    if not pdf_path.is_file():
        return ToolResult(
            ok=False,
            tool_name="pdf_reader",
            error=f"PDF path is not a file: {pdf_path}",
            metadata=metadata_for_context(context, path=str(pdf_path)),
        )
    if pdf_path.suffix.lower() != ".pdf":
        return ToolResult(
            ok=False,
            tool_name="pdf_reader",
            error=f"PDF path must end with .pdf: {pdf_path}",
            metadata=metadata_for_context(context, path=str(pdf_path)),
        )
    try:
        extraction = await extract_pdf_text(
            pdf_path,
            effective_permissions=_canonical_permissions(context),
        )
    except Exception as exc:
        return ToolResult(
            ok=False,
            tool_name="pdf_reader",
            error=f"PDF extraction failed: {exc}",
            metadata=metadata_for_context(context, path=str(pdf_path)),
        )
    requested_pages = arguments.get("pages")
    selected_page_numbers = (
        [
            int(item)
            for item in requested_pages
            if isinstance(item, int) or (isinstance(item, str) and str(item).strip().isdigit())
        ]
        if isinstance(requested_pages, list)
        else []
    )
    if selected_page_numbers:
        selected_page_set = {item for item in selected_page_numbers if item > 0}
        selected_pages = [
            page
            for page in extraction.pages
            if int(page.page_index) in selected_page_set
        ]
    else:
        selected_pages = list(extraction.pages)
    preview_source = (
        "\n\n".join(
            f"[Page {page.page_index}]\n{page.text.strip() or page.snippet}"
            for page in selected_pages
        )
        if selected_pages
        else extraction.combined_text
    )
    preview, truncated = truncate_text(preview_source, limit=_TOOL_PREVIEW_CHARS)
    page_samples = [
        page.to_dict()
        for page in selected_pages[: min(len(selected_pages), 12)]
    ]
    data = {
        "source_path": extraction.source_path,
        "extractor": extraction.extractor,
        "page_count": extraction.page_count,
        "readable_pages": extraction.readable_pages,
        "selected_pages": selected_page_numbers,
        "pages": page_samples,
        "text_preview": preview,
        "truncated": truncated,
        "notes": list(extraction.notes),
    }
    stdout = (
        f"path={extraction.source_path}\n"
        f"extractor={extraction.extractor}\n"
        f"pages={extraction.page_count} readable={extraction.readable_pages}\n"
        f"{preview}"
    ).strip()
    return ToolResult(
        ok=True,
        tool_name="pdf_reader",
        data=data,
        stdout=stdout,
        metadata=metadata_for_context(
            context,
            path=str(pdf_path),
            extractor=extraction.extractor,
            page_count=extraction.page_count,
            readable_pages=extraction.readable_pages,
            dataset_path_override_applied=bool(context.dataset_path_override),
        ),
        warnings=["PDF text truncated for prompt budget"] if truncated else [],
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
