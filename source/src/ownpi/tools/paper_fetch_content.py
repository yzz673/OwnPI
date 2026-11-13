from __future__ import annotations

import time
from typing import Any

from ownpi.runtime.limits.resources import (
    MAX_PAPER_PDF_BYTES,
    MAX_PAPER_PDF_EXTRACTED_CHARS,
)
from ownpi.runtime.permissions.models import EffectivePermissions, FileSystemAccessMode
from ownpi.runtime.permissions.policy import require_file_access
from ownpi.tools.pdf_reader import extract_pdf_text_isolated
from ownpi.tools.paper_fetch_sources import PaperIdentity, _parse_publisher_html
from ownpi.tools.web_tools import _http_get, _response_content, _temporary_transfer_files


def failure(exc: Exception, phase: str) -> dict[str, str]:
    message = (
        str(exc).strip().splitlines()[-1] if str(exc).strip() else type(exc).__name__
    )
    category = "network_error" if phase == "fetch" else "parse_error"
    if isinstance(exc, TimeoutError):
        category = "budget_exhausted"
    elif "403" in message or "401" in message:
        category = "access_denied"
    elif "404" in message:
        category = "not_found"
    return {"status": "failed", "error_code": category, "error": message[:320]}


async def read_content(
    *,
    url: str,
    httpx: Any,
    permissions: EffectivePermissions,
    deadline: float,
    max_chars: int,
    start_page: int,
    page_offset: int,
    expected_doi: str = "",
) -> tuple[list[dict[str, Any]], dict[str, Any], dict[str, Any]]:
    attempt: dict[str, Any] = {"source": "open_content", "url": url, "status": "failed"}
    phase = "fetch"
    try:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("paper retrieval budget exhausted")
        response = await _http_get(
            httpx=httpx,
            url=url,
            timeout=remaining,
            deadline=deadline,
            max_bytes=MAX_PAPER_PDF_BYTES,
            effective_permissions=permissions,
        )
        final_url = str(response.url)
        attempt["final_url"] = final_url
        phase = "parse"
        content = _response_content(response)
        if not content.startswith(b"%PDF"):
            return _read_html(
                response.text, final_url, max_chars, page_offset, attempt, expected_doi
            )
        remaining = deadline - time.monotonic()
        if remaining < 1:
            raise TimeoutError("paper retrieval budget exhausted before extraction")
        with _temporary_transfer_files(
            permissions, prefix='ownpi-paper-', names=('source.pdf',),
        ) as directory:
            pdf_path = directory / 'source.pdf'
            require_file_access(permissions, pdf_path, FileSystemAccessMode.WRITE)
            with pdf_path.open('xb') as handle:
                handle.write(content)
            require_file_access(permissions, pdf_path, FileSystemAccessMode.READ)
            result = await extract_pdf_text_isolated(
                pdf_path,
                max_pages=6,
                start_page=start_page,
                max_extracted_chars=MAX_PAPER_PDF_EXTRACTED_CHARS,
                enable_ocr=False,
                timeout_seconds=int(remaining),
                effective_permissions=permissions,
            )
        passages: list[dict[str, Any]] = []
        next_input = None
        remaining_chars = max_chars
        for page in result.pages:
            text = page.text
            offset = page_offset if page.page_index == start_page else 0
            excerpt = text[offset : offset + remaining_chars]
            if excerpt and page.readable:
                passages.append(
                    {
                        "page": page.page_index,
                        "text": excerpt,
                        "source_url": final_url,
                        "offset": offset,
                        "truncated": offset + len(excerpt) < len(text),
                    }
                )
            remaining_chars -= len(excerpt)
            if offset + len(excerpt) < len(text):
                next_input = {
                    "identifier": final_url,
                    "start_page": page.page_index,
                    "page_offset": offset + len(excerpt),
                }
                break
            elif page.page_index < result.page_count:
                next_input = {
                    "identifier": final_url,
                    "start_page": page.page_index + 1,
                    "page_offset": 0,
                }
            else:
                next_input = None
            if remaining_chars <= 0:
                break
        coverage = {
            "kind": "pdf_page",
            "start_page": start_page,
            "page_offset": page_offset,
            "page_count": result.page_count,
            "returned_pages": [p["page"] for p in passages],
            "extraction_notes": result.notes,
            "ocr_enabled": False,
            "extraction_char_limit": MAX_PAPER_PDF_EXTRACTED_CHARS,
            "next_input": next_input,
        }
        attempt.update(status="completed" if passages else "empty", content_type="pdf")
        return passages, coverage, attempt
    except PermissionError:
        raise
    except Exception as exc:
        attempt.update(failure(exc, phase))
        return [], {}, attempt


def _read_html(
    text: str,
    url: str,
    max_chars: int,
    offset: int,
    attempt: dict[str, Any],
    expected_doi: str,
) -> tuple[list[dict[str, Any]], dict[str, Any], dict[str, Any]]:
    candidate = _parse_publisher_html(
        text, PaperIdentity(url, "url", canonical_url=url)
    )
    if expected_doi and candidate.doi and candidate.doi.lower() != expected_doi.lower():
        attempt.update(status="rejected", error_code="identity_conflict")
        return [], {}, attempt
    excerpt = candidate.body[offset : offset + max_chars]
    if not excerpt:
        attempt.update(
            status="unsupported",
            error_code="unsupported_content",
            error="No supported structured article body; try another location for the same paper.",
        )
        return [], {}, attempt
    end = offset + len(excerpt)
    continuation = (
        {"identifier": url, "page_offset": end} if end < len(candidate.body) else None
    )
    attempt.update(status="completed", content_type="html")
    return (
        [
            {
                "text": excerpt,
                "source_url": url,
                "offset": offset,
                "truncated": continuation is not None,
            }
        ],
        {"kind": "structured_html", "next_input": continuation},
        attempt,
    )
