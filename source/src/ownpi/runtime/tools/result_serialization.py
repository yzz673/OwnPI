from __future__ import annotations

import json
import logging
from typing import Any

from ownpi.runtime.tools.projection_errors import ToolResultProjectionError


logger = logging.getLogger(__name__)


def tool_output_json_text(value: Any, *, tool_name: str) -> str:
    """Serialize one local tool view; serialization errors become JSON text."""
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    except ToolResultProjectionError:
        raise
    except (TypeError, ValueError, RecursionError) as error:
        return serialization_failure_text(error, tool_name=tool_name)


def serialization_failure_text(error: Exception, *, tool_name: str) -> str:
    """Report a serializer failure without rendering its object or exception text."""
    error_type = type(error).__name__
    logger.warning(
        "Tool output serialization failed: tool=%s exception=%s",
        tool_name, error_type,
    )
    return json.dumps(
        f"failed to serialize {tool_name} result: {error_type}",
        ensure_ascii=False,
    )
