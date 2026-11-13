from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Sequence

_MIN_TRUNCATION_BUDGET = 32
_OMISSION_MARKER_TEMPLATE = "<<<runtime_output_omitted bytes={omitted_bytes} chars={omitted_chars}>>>"


@dataclass(frozen=True)
class BoundedOutputText:
    text: str
    original_bytes: int
    retained_bytes: int
    omitted_bytes: int
    omitted_chars: int
    truncated: bool


@dataclass(frozen=True)
class FilePageProjection:
    text: str
    visible_start: int | None
    visible_end: int | None
    visible_line_count: int
    has_more: bool
    next_offset: int | None
    next_column: int | None
    cursor_kind: str
    partial_line: int | None
    content_truncated: bool
    projection_truncated: bool
    omitted_bytes: int
    omitted_chars: int


def truncate_output_text(
    text: str,
    *,
    max_output_bytes: int,
    position: Literal["middle", "prefix"] = "middle",
) -> BoundedOutputText:
    """Apply Codex-style UTF-8-safe truncation to generic tool output."""

    source = str(text or "")
    source_bytes = source.encode("utf-8")
    if len(source_bytes) <= max_output_bytes:
        return BoundedOutputText(
            text=source,
            original_bytes=len(source_bytes),
            retained_bytes=len(source_bytes),
            omitted_bytes=0,
            omitted_chars=0,
            truncated=False,
        )
    if max_output_bytes < _MIN_TRUNCATION_BUDGET:
        raise ValueError(
            f"max_output_bytes must be at least {_MIN_TRUNCATION_BUDGET}"
        )
    if position not in {"middle", "prefix"}:
        raise ValueError(f"unsupported truncation position: {position}")

    retained_budget = max_output_bytes
    for _ in range(6):
        prefix, suffix = _retained_fragments(
            source,
            max_bytes=retained_budget,
            position=position,
        )
        retained_text = f"{prefix}{suffix}"
        retained_bytes = len(retained_text.encode("utf-8"))
        omitted_bytes = len(source_bytes) - retained_bytes
        omitted_chars = len(source) - len(retained_text)
        marker = _omission_marker(
            omitted_bytes=omitted_bytes,
            omitted_chars=omitted_chars,
        )
        bounded = (
            f"{prefix}\n{marker}"
            if position == "prefix"
            else f"{prefix}\n{marker}\n{suffix}"
        )
        overflow = len(bounded.encode("utf-8")) - max_output_bytes
        if overflow <= 0:
            break
        retained_budget = max(0, retained_budget - overflow)
    if len(bounded.encode("utf-8")) > max_output_bytes:
        raise ValueError("omission marker exceeds max_output_bytes")
    return BoundedOutputText(
        text=bounded,
        original_bytes=len(source_bytes),
        retained_bytes=retained_bytes,
        omitted_bytes=omitted_bytes,
        omitted_chars=omitted_chars,
        truncated=True,
    )


def project_file_page(
    lines: Sequence[tuple[int, str]],
    *,
    total_lines: int,
    max_output_bytes: int,
) -> FilePageProjection:
    """Project one contiguous numbered-line prefix with an exact next-line cursor."""

    if max_output_bytes < _MIN_TRUNCATION_BUDGET:
        raise ValueError(f"max_output_bytes must be at least {_MIN_TRUNCATION_BUDGET}")
    if not lines:
        return FilePageProjection(
            text="",
            visible_start=None,
            visible_end=None,
            visible_line_count=0,
            has_more=False,
            next_offset=None,
            next_column=None,
            cursor_kind="line_offset",
            partial_line=None,
            content_truncated=False,
            projection_truncated=False,
            omitted_bytes=0,
            omitted_chars=0,
        )

    rendered = [f"{line_number:>5}: {line}" for line_number, line in lines]
    full_text = "\n".join(rendered)
    if len(full_text.encode("utf-8")) <= max_output_bytes:
        visible_end = lines[-1][0]
        has_more = visible_end < total_lines
        return FilePageProjection(
            text=full_text,
            visible_start=lines[0][0],
            visible_end=visible_end,
            visible_line_count=len(lines),
            has_more=has_more,
            next_offset=visible_end + 1 if has_more else None,
            next_column=None,
            cursor_kind="line_offset",
            partial_line=None,
            content_truncated=False,
            projection_truncated=False,
            omitted_bytes=0,
            omitted_chars=0,
        )

    visible: list[str] = []
    for index, rendered_line in enumerate(rendered):
        candidate = "\n".join((*visible, rendered_line))
        omitted_text = "\n".join(rendered[index + 1 :])
        marker = _omission_marker_for_text(omitted_text)
        candidate_with_marker = f"{candidate}\n{marker}"
        if len(candidate_with_marker.encode("utf-8")) > max_output_bytes:
            break
        visible.append(rendered_line)

    content_truncated = False
    if not visible:
        first_line_number, first_line = lines[0]
        label = f"{first_line_number:>5}: "
        remaining = "\n".join(
            (first_line, *(line for _, line in lines[1:]))
        )
        marker = _omission_marker_for_text(remaining)
        prefix_budget = max_output_bytes - len(label.encode("utf-8")) - len(marker.encode("utf-8")) - 1
        if prefix_budget < 1:
            raise ValueError("content page metadata exceeds max_output_bytes")
        prefix = _utf8_prefix(first_line, prefix_budget)
        for _ in range(3):
            remaining = "\n".join(
                (first_line[len(prefix):], *(line for _, line in lines[1:]))
            )
            marker = _omission_marker_for_text(remaining)
            available = max_output_bytes - len(label.encode("utf-8")) - len(marker.encode("utf-8")) - 1
            next_prefix = _utf8_prefix(first_line, max(1, available))
            if next_prefix == prefix:
                break
            prefix = next_prefix
        bounded_partial = f"{label}{prefix}\n{marker}"
        if not prefix:
            raise ValueError("content page budget cannot advance the character cursor")
        omitted_bytes = len(remaining.encode("utf-8"))
        omitted_chars = len(remaining)
        return FilePageProjection(
            text=bounded_partial,
            visible_start=first_line_number,
            visible_end=None,
            visible_line_count=0,
            has_more=True,
            next_offset=first_line_number,
            next_column=len(prefix),
            cursor_kind="line_column",
            partial_line=first_line_number,
            content_truncated=False,
            projection_truncated=True,
            omitted_bytes=omitted_bytes,
            omitted_chars=omitted_chars,
        )

    visible_count = len(visible)
    visible_start = lines[0][0]
    visible_end = lines[visible_count - 1][0]
    omitted_source = "\n".join(rendered[visible_count:])
    omitted_bytes = len(omitted_source.encode("utf-8"))
    omitted_chars = len(omitted_source)
    marker = _omission_marker(
        omitted_bytes=omitted_bytes,
        omitted_chars=omitted_chars,
    )
    visible_text = "\n".join(visible)
    text = f"{visible_text}\n{marker}"
    has_more = visible_end < total_lines
    return FilePageProjection(
        text=text,
        visible_start=visible_start,
        visible_end=visible_end,
        visible_line_count=visible_count,
        has_more=has_more,
        next_offset=visible_end + 1 if has_more else None,
        next_column=None,
        cursor_kind="line_offset",
        partial_line=None,
        content_truncated=content_truncated,
        projection_truncated=True,
        omitted_bytes=omitted_bytes,
        omitted_chars=omitted_chars,
    )


def _utf8_prefix(text: str, max_bytes: int) -> str:
    return text.encode("utf-8")[:max_bytes].decode("utf-8", errors="ignore")


def _utf8_suffix(text: str, max_bytes: int) -> str:
    payload = text.encode("utf-8")
    if len(payload) <= max_bytes:
        return text
    return payload[-max_bytes:].decode("utf-8", errors="ignore")


def _retained_fragments(
    text: str,
    *,
    max_bytes: int,
    position: Literal["middle", "prefix"],
) -> tuple[str, str]:
    if position == "prefix":
        return _utf8_prefix(text, max_bytes), ""
    head_budget = (max_bytes + 1) // 2
    tail_budget = max_bytes - head_budget
    prefix = _utf8_prefix(text, head_budget)
    suffix = _utf8_suffix(text[len(prefix) :], tail_budget)
    return prefix, suffix


def _omission_marker_for_text(text: str) -> str:
    return _omission_marker(
        omitted_bytes=len(text.encode("utf-8")),
        omitted_chars=len(text),
    )


def _omission_marker(*, omitted_bytes: int, omitted_chars: int) -> str:
    return _OMISSION_MARKER_TEMPLATE.format(
        omitted_bytes=max(0, omitted_bytes),
        omitted_chars=max(0, omitted_chars),
    )
