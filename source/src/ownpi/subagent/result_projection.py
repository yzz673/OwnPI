"""Preserve every child locator while budgeting feedback previews independently."""

from __future__ import annotations

import json
from typing import Any

from ownpi.runtime.contracts import ToolResult
from ownpi.runtime.limits.tool_output import TruncationPolicy


def reduce_batch_result(text: str, byte_limit: int) -> str:
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        return text
    if not isinstance(value, dict) or 'feedback_index' not in value or 'previews' not in value:
        return text
    # Reuse the renderer with current previews; already-truncated flags survive.
    children = []
    for index, preview in zip(value['feedback_index'], value['previews'], strict=True):
        children.append({**index, 'content': preview['feedback'], 'error': preview['diagnostic']})
    rendered, _ = format_batch_result(
        ToolResult(ok=value['ok'], tool_name='run_subagents',
                   data={'status': value['status'], 'children': children}),
        TruncationPolicy.bytes(byte_limit * 5 // 6),
    )
    result = json.loads(rendered)
    for original, index in zip(value['feedback_index'], result['feedback_index'], strict=True):
        for field in ('feedback_truncated', 'diagnostic_truncated', 'feedback_delivery_failed'):
            index[field] = original[field] or index[field]
    return json.dumps(result, ensure_ascii=False, separators=(',', ':'))


def format_batch_result(
    result: ToolResult,
    policy: TruncationPolicy,
) -> tuple[str, bool]:
    effective = policy.scaled(1.2)
    children = result.data.get("children", [])
    index: list[dict[str, Any]] = []
    previews: list[dict[str, str]] = []
    sources: list[tuple[str, str]] = []
    for child in children:
        submitted = bool(child.get("feedback_submitted"))
        content = str(child.get("content") or "")
        diagnostic = "\n".join(
            str(value)
            for value in (
                child.get("error"),
                child.get("feedback_error"),
                content if not submitted else None,
            )
            if value
        )
        feedback = content if submitted else ""
        index.append(
            {
                "task_name": child["task_name"],
                "child_id": child.get("child_id", ""),
                "status": child["status"],
                "reclaimed": bool(child.get("reclaimed")),
                "feedback_submitted": submitted,
                "feedback_available": bool(child.get("feedback_available")),
                "feedback_path": child.get("feedback_path"),
                "feedback_source": child.get("feedback_source"),
                "feedback_delivery_failed": bool(child.get("feedback_error")),
                "feedback_truncated": bool(feedback) if submitted else None,
                "diagnostic_truncated": bool(diagnostic),
            }
        )
        previews.append({"feedback": "", "diagnostic": ""})
        sources.append((feedback, diagnostic))

    payload = {
        "ok": result.ok,
        "status": result.data.get("status"),
        "feedback_index": index,
        "reading_instruction": (
            "The index includes every child, in task order; previews use the same order. "
            "When feedback_truncated is true, read the full feedback_path with file_reader "
            "and follow its pagination until complete. feedback_path is a rebuildable full-text copy; "
            "feedback_source identifies the original committed call. Unavailable readback does not "
            "revoke a submitted completion."
        ),
        "previews": previews,
    }

    def render() -> str:
        return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))

    # The complete index is an irreducible floor, even when it exceeds the soft policy.
    for position, (feedback, diagnostic) in enumerate(sources):
        remaining_children = len(sources) - position
        current_size = len(render().encode("utf-8"))
        allowance = max(0, effective.byte_budget - current_size) // remaining_children
        target_size = current_size + allowance
        for field, source in (("feedback", feedback), ("diagnostic", diagnostic)):
            low, high = 0, len(source)
            while low < high:
                middle = (low + high + 1) // 2
                previews[position][field] = source[:middle]
                index[position][field + "_truncated"] = middle < len(source)
                if len(render().encode("utf-8")) <= target_size:
                    low = middle
                else:
                    high = middle - 1
            previews[position][field] = source[:low]
            index[position][field + "_truncated"] = (
                low < len(source)
                if field != "feedback" or index[position]["feedback_submitted"]
                else None
            )
    truncated = any(
        item["feedback_truncated"] or item["diagnostic_truncated"] for item in index
    )
    return render(), truncated
