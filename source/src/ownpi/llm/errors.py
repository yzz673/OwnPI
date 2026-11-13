from __future__ import annotations


class ProviderContextOverflowError(RuntimeError):
    """Provider rejected a request because the input context was too large."""


_CONTEXT_OVERFLOW_MARKERS = (
    'maximum context length',
    'context length exceeded',
    'context_length_exceeded',
    'context window',
    'prompt is too long',
    'prompt too long',
    'too many tokens',
    'token limit exceeded',
    'tokens exceeds',
    'request too large',
    'input is too long',
    'reduce the length of the messages',
)


def is_provider_context_overflow_error(exc: BaseException) -> bool:
    if isinstance(exc, ProviderContextOverflowError):
        return True
    detail = str(exc).casefold()
    if not detail:
        return False
    return any(marker in detail for marker in _CONTEXT_OVERFLOW_MARKERS)


def normalize_provider_context_overflow(
    exc: BaseException,
) -> BaseException:
    if isinstance(exc, ProviderContextOverflowError):
        return exc
    diagnostics = getattr(exc, "diagnostics", None)
    if (getattr(diagnostics, 'failure_kind', None) == 'response_incomplete'
            and getattr(diagnostics, 'semantic_disposition', None) == 'retryable_transport'):
        # Codex maps every incomplete reason to Stream, never context rejection.
        return exc
    if getattr(diagnostics, 'failure_kind', None) == 'response_failed':
        if getattr(diagnostics, 'error_code', None) == 'context_length_exceeded':
            return ProviderContextOverflowError(str(exc))
        return exc
    if getattr(diagnostics, 'failure_origin', None) != 'provider_response':
        return exc
    structured_values = {
        str(getattr(diagnostics, field, "") or "").strip().casefold()
        for field in ("incomplete_reason", "error_code", "error_type")
    }
    structured_values.discard("")
    if structured_values.intersection(
        {
            "context_length_exceeded",
            "input_too_large",
            "max_input_tokens",
            "model_context_window_exceeded",
        }
    ) or is_provider_context_overflow_error(exc):
        return ProviderContextOverflowError(
            "provider rejected the native tool request because its context was too large"
        )
    return exc


__all__ = [
    "ProviderContextOverflowError",
    "is_provider_context_overflow_error",
    "normalize_provider_context_overflow",
]
