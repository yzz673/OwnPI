from __future__ import annotations


class ToolPreflightValidationError(ValueError):
    """Expected tool-input rejection while resolving canonical operation effects."""

    def __init__(
        self,
        message: str,
        *,
        validation_code: str,
        compact_hint: str = "",
        field_name: str = "",
    ) -> None:
        normalized_message = str(message or "Invalid tool input during preflight.").strip()
        super().__init__(normalized_message)
        self.validation_code = str(validation_code or "tool_preflight_input_invalid").strip()
        self.compact_hint = str(compact_hint or normalized_message).strip()
        self.field_name = str(field_name or "").strip()


__all__ = ["ToolPreflightValidationError"]
