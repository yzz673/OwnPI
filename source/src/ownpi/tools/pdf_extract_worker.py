from __future__ import annotations

import asyncio
import json
from pathlib import Path
import sys


def _apply_limits(*, cpu_seconds: int, address_space_bytes: int) -> list[str]:
    import resource

    notes: list[str] = []
    for name, requested in [('RLIMIT_CPU', cpu_seconds), ('RLIMIT_AS', address_space_bytes)]:
        if name == 'RLIMIT_AS' and sys.platform == 'darwin':
            notes.append('resource_limit_unavailable:RLIMIT_AS:darwin; CPU and parent wall timeout remain active')
            continue
        limit = getattr(resource, name)
        soft, hard = resource.getrlimit(limit)
        finite = [value for value in (requested, soft, hard) if value != resource.RLIM_INFINITY]
        effective = min(finite)
        resource.setrlimit(limit, (effective, hard))
        notes.append(f'resource_limit_applied:{name}:{effective}')
    return notes


def _payload(result: object) -> dict[str, object]:
    pages = []
    for page in getattr(result, 'pages', []):
        pages.append(
            {
                'page_index': int(getattr(page, 'page_index', 0)),
                'char_count': int(getattr(page, 'char_count', 0)),
                'readable': bool(getattr(page, 'readable', False)),
                'snippet': str(getattr(page, 'snippet', '') or ''),
                'text': str(getattr(page, 'text', '') or ''),
            }
        )
    return {
        'source_path': str(getattr(result, 'source_path', '') or ''),
        'extractor': str(getattr(result, 'extractor', '') or ''),
        'page_count': int(getattr(result, 'page_count', len(pages)) or 0),
        'readable_pages': int(getattr(result, 'readable_pages', 0) or 0),
        'pages': pages,
        'combined_text': str(getattr(result, 'combined_text', '') or ''),
        'notes': [str(item) for item in getattr(result, 'notes', [])],
    }


def main(argv: list[str]) -> int:
    if len(argv) not in {7, 8}:
        sys.stderr.write('expected pdf_path, max_pages, max_chars, enable_ocr, cpu_seconds, address_space_bytes\n')
        return 2
    pdf_path = Path(argv[1]).resolve()
    max_pages = max(1, int(argv[2]))
    max_chars = max(1, int(argv[3]))
    enable_ocr = argv[4] == '1'
    notes = _apply_limits(cpu_seconds=max(1, int(argv[5])), address_space_bytes=max(1, int(argv[6])))

    from ownpi.tools.pdf_reader import extract_pdf_text

    result = asyncio.run(extract_pdf_text(
        pdf_path,
        max_pages=max_pages,
        max_extracted_chars=max_chars,
        enable_ocr=enable_ocr,
        start_page=max(1, int(argv[7])) if len(argv) == 8 else 1,
    ))
    payload = _payload(result)
    payload['notes'] = list(result.notes) + notes
    sys.stdout.write(json.dumps(payload, ensure_ascii=True, separators=(',', ':')))
    return 0


if __name__ == '__main__':
    raise SystemExit(main(sys.argv))
