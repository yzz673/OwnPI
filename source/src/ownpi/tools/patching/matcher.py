from __future__ import annotations

from collections.abc import Callable, Sequence

from ownpi.tools.patching.models import PatchError


_UNICODE_PUNCTUATION_TRANSLATION = str.maketrans(
    {
        '\u2010': '-',
        '\u2011': '-',
        '\u2012': '-',
        '\u2013': '-',
        '\u2014': '-',
        '\u2015': '-',
        '\u2212': '-',
        '\u2018': "'",
        '\u2019': "'",
        '\u201a': "'",
        '\u201b': "'",
        '\u201c': '"',
        '\u201d': '"',
        '\u201e': '"',
        '\u201f': '"',
        '\u00a0': ' ',
        '\u2002': ' ',
        '\u2003': ' ',
        '\u2004': ' ',
        '\u2005': ' ',
        '\u2006': ' ',
        '\u2007': ' ',
        '\u2008': ' ',
        '\u2009': ' ',
        '\u200a': ' ',
        '\u202f': ' ',
        '\u205f': ' ',
        '\u3000': ' ',
    }
)


def seek_sequence(
    lines: Sequence[str],
    pattern: Sequence[str],
    start: int,
    eof: bool,
) -> int | None:
    if not pattern:
        return start
    if len(pattern) > len(lines):
        return None

    candidate_starts = _candidate_starts(
        line_count=len(lines),
        pattern_count=len(pattern),
        start=start,
        eof=eof,
    )
    for normalize in (_identity, str.rstrip, str.strip):
        for index in candidate_starts:
            if _matches(lines, pattern, index, normalize):
                return index

    unicode_matches = [
        index
        for index in candidate_starts
        if _matches(lines, pattern, index, _normalize_unicode)
    ]
    if len(unicode_matches) > 1:
        raise PatchError(
            'ambiguous Unicode-normalized context: multiple candidate locations',
            failure_kind='context_mismatch',
        )
    return unicode_matches[0] if unicode_matches else None


def _candidate_starts(
    *,
    line_count: int,
    pattern_count: int,
    start: int,
    eof: bool,
) -> tuple[int, ...]:
    last_start = line_count - pattern_count
    bounded_start = max(0, min(start, last_start + 1))
    candidates = list(range(bounded_start, last_start + 1))
    if not eof:
        return tuple(candidates)
    if last_start in candidates:
        candidates.remove(last_start)
    return (last_start, *candidates)


def _matches(
    lines: Sequence[str],
    pattern: Sequence[str],
    index: int,
    normalize: Callable[[str], str],
) -> bool:
    return all(
        normalize(lines[index + offset]) == normalize(expected)
        for offset, expected in enumerate(pattern)
    )


def _identity(value: str) -> str:
    return value


def _normalize_unicode(value: str) -> str:
    return value.strip().translate(_UNICODE_PUNCTUATION_TRANSLATION)


__all__ = ['seek_sequence']
