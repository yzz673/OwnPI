"""Evidence available in the saved receipt, independent of tool execution state."""

from typing import Any


def web_evidence(data: dict[str, Any], *, hosted: bool = False) -> dict[str, str]:
    if hosted:
        action = data.get("action")
        sources = action.get("sources") if isinstance(action, dict) else None
        state = "sources" if isinstance(sources, list) and sources else "metadata_only"
    else:
        text = next(
            (
                data[k]
                for k in ("text_excerpt", "text", "content")
                if isinstance(data.get(k), str) and data[k].strip()
            ),
            "",
        )
        if text:
            state = (
                "abstract"
                if data.get("content_status") == "abstract_only"
                else "excerpt"
            )
        elif isinstance(data.get("results"), list) and data["results"]:
            state = "sources"
        else:
            state = "metadata_only"
    return {"availability": state}
