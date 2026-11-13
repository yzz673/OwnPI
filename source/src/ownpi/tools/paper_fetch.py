from __future__ import annotations

import sys
import tempfile
import time
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ownpi.runtime.contracts import ToolResult
from ownpi.runtime.limits.resources import MAX_PAPER_PDF_BYTES
from ownpi.runtime.permissions.action_compiler import OperationEffects
from ownpi.runtime.permissions.models import EffectivePermissions, FileSystemAccessMode
from ownpi.runtime.permissions.orchestrator import FileSystemAccessRequest
from ownpi.runtime.permissions.runtime_config import default_runtime_permissions
from ownpi.runtime.tools.errors import ToolPreflightValidationError
from ownpi.tools.common import clamp_int, metadata_for_context
from ownpi.tools.paper_fetch_content import failure, read_content
from ownpi.tools.paper_fetch_sources import (
    PaperCandidate,
    PaperIdentity,
    _doi_from_value,
    _is_pdf_url,
    _metadata_routes,
    _normalize_identity,
)
from ownpi.tools.web_tools import _http_get, _load_httpx, _response_content

if TYPE_CHECKING:
    from ownpi.runtime.tools.definitions import ToolExecutionContext


IDENTIFIER_HINT = (
    "Use a DOI, arXiv identifier, or HTTP(S) URL from the user or an identifiable source. "
    "Correct its format from that source, or locate the paper by title/author using web_search; "
    "do not try guessed identifiers."
)
ACCESS_HINT = (
    "Try another accessible location for the same paper. "
    "An access or parsing failure does not invalidate its identifier."
)


async def run_paper_fetch(
    context: ToolExecutionContext, arguments: dict[str, Any]
) -> ToolResult:
    try:
        return await _run_paper_fetch(context, arguments)
    except PermissionError as exc:
        return ToolResult(
            ok=False, tool_name='paper_fetch', error=str(exc),
            data={'outcome': 'permission_denied', 'recovery_hint':
                  'Resolve execution permissions before retrying the same paper; its identity has not been invalidated.'},
        )


async def _run_paper_fetch(
    context: ToolExecutionContext, arguments: dict[str, Any]
) -> ToolResult:
    raw = str(arguments.get("identifier", "") or arguments.get("url", "") or "").strip()
    identity, error = _normalize_identity(raw)
    if error or identity is None:
        return ToolResult(
            ok=False,
            tool_name="paper_fetch",
            error=error,
            data={"outcome": "invalid_input", "recovery_hint": IDENTIFIER_HINT},
        )
    timeout = clamp_int(
        arguments.get("timeout_seconds"), default=30, minimum=1, maximum=120
    )
    max_chars = clamp_int(
        arguments.get("max_chars"), default=16000, minimum=1000, maximum=16000
    )
    start_page = clamp_int(
        arguments.get("start_page"), default=1, minimum=1, maximum=100000
    )
    offset = clamp_int(
        arguments.get("page_offset"), default=0, minimum=0, maximum=MAX_PAPER_PDF_BYTES
    )
    deadline = time.monotonic() + timeout
    permissions = _canonical_permissions(context)
    httpx = _load_httpx()
    merged = PaperCandidate(source="")
    attempts: list[dict[str, Any]] = []
    locations: list[dict[str, str]] = []
    confirmed: dict[str, str] = {}
    visited: set[str] = set()
    passages: list[dict[str, Any]] = []
    coverage: dict[str, Any] = {}
    pdf_url = ""
    content_url = ""
    routes = _metadata_routes(identity)
    if identity.kind == "arxiv":
        routes.append(("arxiv_pdf", f"https://arxiv.org/pdf/{identity.arxiv_id}", None))
    # Direct PDF input still takes the same content, permission, and budget path.
    if _is_pdf_url(identity.canonical_url):
        routes = [("direct", identity.canonical_url, None)]
    for source, url, parser in routes:
        if time.monotonic() >= deadline:
            break
        candidate = PaperCandidate(source=source)
        if parser is None:
            candidate.content_urls = [url]
        else:
            candidate, attempt = await _fetch_candidate(
                identity, source, url, parser, httpx, permissions, deadline
            )
            attempts.append(attempt)
            if candidate is None:
                continue
            match, verified = _check_identity(identity, candidate, merged)
            if not match:
                attempt.update(status="rejected", error_code="identity_conflict")
                continue
            if identity.kind == "doi" and candidate.has_metadata() and not verified:
                attempt.update(status="unresolved", error_code="identity_unconfirmed")
            if verified and not confirmed:
                confirmed = {
                    "source": source,
                    "url": str(attempt.get("final_url") or url),
                }
            if verified or identity.kind != "doi":
                _merge_candidate(merged, candidate)
        candidates = list(dict.fromkeys([candidate.pdf_url, *candidate.content_urls]))
        if candidate.body:
            candidates.append(candidate.landing_url)
        for location in candidates:
            if not location or location in visited or len(visited) >= 12:
                continue
            visited.add(location)
            locations.append(
                {"kind": "content_candidate", "url": location, "source": source}
            )
            if time.monotonic() >= deadline:
                break
            passages, coverage, attempt = await read_content(
                url=location,
                httpx=httpx,
                permissions=permissions,
                deadline=deadline,
                max_chars=max_chars,
                start_page=start_page,
                page_offset=offset,
                expected_doi=identity.doi or merged.doi,
            )
            attempts.append(attempt)
            if passages or coverage.get("next_input"):
                content_url = str(attempt.get("final_url") or location)
                pdf_url = content_url if attempt.get("content_type") == "pdf" else ""
                break
        if passages or coverage.get("next_input"):
            break
    abstract_limit = min(4000, max_chars // 4)
    # The body budget is independent during retrieval, then bounded for the combined result.
    if passages:
        abstract_limit = max(0, max_chars - sum(len(p["text"]) for p in passages))
    abstract = merged.abstract[:abstract_limit]
    abstract_truncated = len(abstract) < len(merged.abstract)
    metadata_available = merged.has_metadata()
    if confirmed:
        locations.insert(0, {"kind": "identity", **confirmed})
    identity_status = (
        "verified"
        if confirmed
        else (
            "conflict"
            if any(a.get("error_code") == "identity_conflict" for a in attempts)
            else "unresolved"
        )
    )
    outcome = (
        "content_available"
        if passages
        else (
            "abstract_only"
            if abstract
            else ("metadata_only" if metadata_available else "unavailable")
        )
    )
    data = {
        "normalized_identity": identity.to_dict(),
        "outcome": outcome,
        "identity_status": identity_status,
        "identity_source": confirmed or None,
        "metadata_status": "available" if metadata_available else "unavailable",
        "content_status": "body_available"
        if passages
        else ("abstract_only" if abstract else "unavailable"),
        "selected_source": confirmed.get("source")
        or merged.source
        or ("open_content" if passages else None),
        "attempted_sources": attempts,
        "title": merged.title[:500],
        "authors": [a[:160] for a in merged.authors[:80]],
        "publication_date": merged.publication_date[:80],
        "doi": merged.doi[:300],
        "abstract": abstract,
        "abstract_truncated": abstract_truncated,
        "passages": passages,
        "coverage": coverage
        or {"kind": "abstract_only" if abstract else "none", "next_input": None},
        "source_locations": locations,
        "open_pdf_url": pdf_url or merged.pdf_url or None,
        "content_url": content_url or None,
        "stop_reason": "content_available"
        if passages
        else (
            "budget_exhausted"
            if time.monotonic() >= deadline
            else (
                "continuation_available"
                if coverage.get("next_input")
                else "candidates_exhausted"
            )
        ),
    }
    warnings = []
    if not passages:
        warnings.append(
            "Readable full text was not retrieved; metadata or abstract is not full-text access."
        )
        identity_failure = identity_status == "conflict" or (
            identity_status == "unresolved"
            and any(
                a.get("error_code") in {"not_found", "identity_unconfirmed"}
                for a in attempts
            )
        )
        data["recovery_hint"] = IDENTIFIER_HINT if identity_failure else ACCESS_HINT
    if abstract_truncated:
        warnings.append("Abstract truncated for the result budget.")
    return ToolResult(
        ok=True,
        tool_name="paper_fetch",
        data=data,
        warnings=warnings,
        stdout=f"outcome={outcome}\nidentity_status={identity_status}\ncoverage={data['coverage']['kind']}",
        metadata=metadata_for_context(
            context,
            retrieval_outcome=outcome,
            paper_content_available=bool(passages or abstract),
            paper_passage_count=len(passages),
            identifier=raw,
            paper_identity=identity.doi or identity.arxiv_id or identity.canonical_url,
            paper_abstract_chars=len(abstract),
        ),
    )


def _merge_candidate(target: PaperCandidate, candidate: PaperCandidate) -> None:
    if not target.source and candidate.has_metadata():
        target.source = candidate.source
    for name in (
        "title",
        "publication_date",
        "abstract",
        "doi",
        "landing_url",
        "pdf_url",
    ):
        if not getattr(target, name) and getattr(candidate, name):
            setattr(target, name, getattr(candidate, name))
    if not target.authors and candidate.authors:
        target.authors = [item for item in candidate.authors if item]


async def _fetch_candidate(
    identity: PaperIdentity,
    source: str,
    url: str,
    parser: Any,
    httpx: Any,
    permissions: EffectivePermissions,
    deadline: float,
) -> tuple[PaperCandidate | None, dict[str, Any]]:
    attempt: dict[str, Any] = {"source": source, "url": url}
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
        attempt.update(status="completed", final_url=final_url)
        if _response_content(response).startswith(b"%PDF"):
            return PaperCandidate(source=source, content_urls=[final_url]), attempt
        phase = "parse"
        candidate = parser(response.text, replace(identity, canonical_url=final_url))
        if (
            not candidate.has_metadata()
            and not candidate.content_urls
            and not candidate.pdf_url
        ):
            attempt.update(status="empty", error_code="identity_unconfirmed")
        return candidate, attempt
    except PermissionError:
        raise
    except Exception as exc:
        attempt.update(failure(exc, phase))
        if "conflict" in str(exc):
            attempt["error_code"] = "identity_conflict"
        elif "not a paper entry" in str(exc):
            attempt["error_code"] = "identity_unconfirmed"
        return None, attempt


def _check_identity(
    requested: PaperIdentity,
    candidate: PaperCandidate,
    merged: PaperCandidate,
) -> tuple[bool, bool]:
    doi = _doi_from_value(candidate.doi).lower()
    for expected in (requested.doi, merged.doi):
        if doi and expected and doi != expected.lower():
            return False, False
    if requested.kind == "doi":
        return True, bool(doi == requested.doi and candidate.title)
    if requested.kind == "arxiv":
        return True, bool(candidate.arxiv_id and candidate.title)
    return True, bool(doi and candidate.title)


def resolve_paper_fetch_effects(
    context: ToolExecutionContext,
    arguments: dict[str, Any],
) -> OperationEffects:
    raw_identifier = str(
        arguments.get("identifier", "") or arguments.get("url", "") or ""
    ).strip()
    identity, validation_error = _normalize_identity(raw_identifier)
    if validation_error or identity is None:
        raise ToolPreflightValidationError(
            validation_error
            or "identifier must be a DOI, arXiv identifier, or HTTP(S) URL",
            validation_code="paper_fetch_identifier_invalid",
            compact_hint=IDENTIFIER_HINT,
            field_name="identifier",
        )
    endpoints = tuple(
        dict.fromkeys(
            url
            for url in (
                *(route[1] for route in _metadata_routes(identity)),
                identity.canonical_url,
            )
            if url
        )
    )
    source_root = Path(__file__).resolve().parents[2]
    return OperationEffects.declared(
        file_system_accesses=(
            FileSystemAccessRequest(source_root, FileSystemAccessMode.READ),
            FileSystemAccessRequest(
                Path(sys.executable).resolve(), FileSystemAccessMode.READ
            ),
            FileSystemAccessRequest(
                Path(tempfile.gettempdir()).resolve(),
                FileSystemAccessMode.WRITE,
            ),
        ),
        network_required=True,
        network_endpoints=endpoints,
        process_required=True,
        process_argv=(
            sys.executable,
            "-m",
            "ownpi.tools.pdf_extract_worker",
        ),
        source="paper_fetch_network_and_pdf_worker",
    )


def _canonical_permissions(context: ToolExecutionContext) -> EffectivePermissions:
    value = getattr(context, "effective_permissions", None)
    if isinstance(value, EffectivePermissions):
        return value
    state = getattr(getattr(context, "state_store", None), "state", None)
    value = getattr(getattr(state, "tool_state", None), "effective_permissions", None)
    if isinstance(value, EffectivePermissions):
        return value
    return default_runtime_permissions(project_root=context.project_root)
