from __future__ import annotations

from dataclasses import dataclass, replace
import hashlib
import json
import re
from typing import Any, Literal

from ownpi.llm.failure_diagnostics import diagnostic_message
from ownpi.llm.provider_retry import ProviderRetryPolicy
from ownpi.runtime.limits.recovery import MAX_RETRY_DELAY_SECONDS


ProviderFailureClass = Literal[
    "transient_provider_failure",
    "deterministic_provider_input_failure",
    "provider_protocol_failure",
    "local_runtime_failure",
]

ProviderErrorDisposition = Literal[
    "deterministic_input",
    "deterministic_auth_or_quota",
    "deterministic_policy",
    "retryable_provider",
    "retryable_transport",
    "protocol_invalid",
    "local_processing_error",
]
ProviderRetryKind = Literal["request", "stream"]
ProviderRetryOwner = Literal["inner_transport", "outer_sampling"]

_DETERMINISTIC_INPUT_ERROR_IDENTIFIERS = {
    "bad_request",
    "context_length_exceeded",
    "input_too_large",
    "invalid_prompt",
    "invalid_request",
    "invalid_request_error",
    "max_input_tokens",
    "max_output_tokens",
    "model_context_window_exceeded",
}
_DETERMINISTIC_POLICY_ERROR_IDENTIFIERS = {
    "bio_policy",
    "content_policy_violation",
    "cyber_policy",
    "policy_violation",
}
_RATE_LIMIT_RETRY_AFTER_PATTERN = re.compile(
    r"(?:try again|retry) in\s*(\d+(?:\.\d+)?)\s*(seconds?|ms|s)",
    flags=re.IGNORECASE,
)


@dataclass(frozen=True)
class ProviderErrorClassification:
    semantic_disposition: ProviderErrorDisposition
    failure_class: ProviderFailureClass
    retryable: bool
    retry_after_seconds: float | None = None
    failure_reason: str = "unknown"
    failure_origin: str = "provider_response"
    classification_basis: str = "unrecognized_provider_error"
    recovery_disposition: str = "unknown"


@dataclass(frozen=True)
class ProviderHTTPErrorClassification:
    status_code: int
    semantic_disposition: ProviderErrorDisposition
    failure_class: ProviderFailureClass
    retryable: bool
    retry_kind: ProviderRetryKind | None
    error_payload: dict[str, Any]
    retry_owner: ProviderRetryOwner | None = None
    retry_after_seconds: float | None = None
    failure_kind: str = "provider_http_error"
    body_sha256: str = ""
    body_excerpt: str = ""
    body_truncated: bool = False
    failure_reason: str = "unknown"
    failure_origin: str = "provider_response"
    classification_basis: str = "unrecognized_http_error"
    recovery_disposition: str = "unknown"

    def to_trace_metadata(self) -> dict[str, Any]:
        return {
            "http_status": self.status_code,
            **{
                key: getattr(self, key)
                for key in (
                    "failure_reason",
                    "failure_origin",
                    "classification_basis",
                    "recovery_disposition",
                )
            },
            "failure_class": self.failure_class,
            "failure_kind": self.failure_kind,
            "semantic_disposition": self.semantic_disposition,
            "retryable": self.retryable,
            "retry_kind": self.retry_kind,
            "retry_owner": self.retry_owner,
            "retry_after_seconds": self.retry_after_seconds,
            "provider_error_code": diagnostic_message(
                self.error_payload.get("code"), 160
            ),
            "provider_error_type": diagnostic_message(
                self.error_payload.get("type"), 160
            ),
            "provider_error_message": diagnostic_message(
                self.error_payload.get("message"), 400
            ),
            "provider_request_id": diagnostic_message(
                self.error_payload.get("request_id"), 160
            ),
            "http_error_body_sha256": self.body_sha256,
            "http_error_body_excerpt": self.body_excerpt,
            "http_error_body_truncated": self.body_truncated,
        }


_TRANSIENT_IDENTIFIERS = {
    "api_error",
    "first_token_timeout",
    "internal_server_error",
    "overloaded_error",
    "server_error",
    "server_is_overloaded",
    "service_unavailable",
    "service_unavailable_error",
    "upstream_error",
    "upstream_unavailable",
    "rate_limit_exceeded",
    "rate_limit_error",
    "too_many_requests",
    "stream_read_error",
}


def _decision(
    reason: str, disposition: ProviderErrorDisposition, basis: str
) -> ProviderErrorClassification:
    transient = disposition == "retryable_provider"
    deterministic = disposition.startswith("deterministic_")
    return ProviderErrorClassification(
        semantic_disposition=disposition,
        failure_class=(
            "transient_provider_failure"
            if transient
            else "deterministic_provider_input_failure"
            if deterministic
            else "provider_protocol_failure"
        ),
        retryable=transient,
        failure_reason=reason,
        classification_basis=basis,
        recovery_disposition="delayed_retry_candidate"
        if transient
        else "repair_required"
        if deterministic
        else "unknown",
    )


def classify_provider_error(
    error_payload: dict[str, Any],
) -> ProviderErrorClassification:
    values = {
        key: str(error_payload.get(key) or "").strip().casefold()
        for key in ("code", "type", "message")
    }
    identifiers = {
        str(error_payload.get(k) or "").strip().casefold() for k in ("code", "type")
    } - {""}
    matches: list[tuple[str, ProviderErrorDisposition, set[str]]] = [
        (
            "quota_exhausted",
            "deterministic_auth_or_quota",
            {
                "insufficient_quota",
                "billing_hard_limit_reached",
                "usage_not_included",
                "quota_exceeded",
            },
        ),
        ("billing_error", "deterministic_auth_or_quota", {"billing_error"}),
        (
            "authentication_failed",
            "deterministic_auth_or_quota",
            {"authentication_error", "invalid_api_key"},
        ),
        (
            "permission_denied",
            "deterministic_auth_or_quota",
            {"permission_denied", "permission_error"},
        ),
        (
            "policy_rejected",
            "deterministic_policy",
            _DETERMINISTIC_POLICY_ERROR_IDENTIFIERS,
        ),
        (
            "context_limit",
            "deterministic_input",
            {
                "context_length_exceeded",
                "input_too_large",
                "max_input_tokens",
                "model_context_window_exceeded",
            },
        ),
        (
            "invalid_request",
            "deterministic_input",
            _DETERMINISTIC_INPUT_ERROR_IDENTIFIERS,
        ),
        (
            "overloaded",
            "retryable_provider",
            {"overloaded_error", "server_is_overloaded"},
        ),
        (
            "rate_limited",
            "retryable_provider",
            {"rate_limit_exceeded", "rate_limit_error", "too_many_requests"},
        ),
        ("service_unavailable", "retryable_provider", _TRANSIENT_IDENTIFIERS),
    ]
    matched = [
        (reason, disposition, sorted(names & identifiers))
        for reason, disposition, names in matches
        if names & identifiers
    ]
    if not matched:
        # Preserve the historical retry route for a non-empty but unrecognised
        # provider error.  The classification layer may add diagnostics, but
        # an unknown error payload must not silently become non-retryable.
        if any(values.values()):
            return _decision(
                "unknown",
                "retryable_provider",
                "unrecognized_provider_error",
            )
        return _decision("unknown", "protocol_invalid", "empty_provider_error")
    reason, disposition, names = matched[0]
    basis = "provider_identifier:" + ",".join(names)
    if len(matched) > 1:
        basis += ";other_matches:" + ",".join(item[0] for item in matched[1:])
    decision = _decision(reason, disposition, basis)
    if decision.retryable:
        decision = replace(
            decision,
            retry_after_seconds=_parse_retry_after_seconds(
                code="", message=str(error_payload.get("message") or "")
            ),
        )
    return decision


def classify_provider_http_error(
    status_code: int,
    detail: str,
    *,
    error_payload: dict[str, Any] | None = None,
) -> ProviderHTTPErrorClassification:
    status = int(status_code)
    raw = str(detail or "")
    payload = (
        _normalize_http_error_payload(error_payload)
        if isinstance(error_payload, dict)
        else _http_error_payload(raw)
    )
    decision = classify_provider_error(payload) if payload else None

    def build(
        *,
        semantic_disposition: ProviderErrorDisposition,
        failure_class: ProviderFailureClass,
        retryable: bool,
        retry_kind: ProviderRetryKind | None,
        retry_owner: ProviderRetryOwner | None,
        failure_reason: str,
        classification_basis: str,
        recovery_disposition: str,
        retry_after_seconds: float | None = None,
        failure_kind: str = "provider_http_error",
    ) -> ProviderHTTPErrorClassification:
        return ProviderHTTPErrorClassification(
            status_code=status,
            semantic_disposition=semantic_disposition,
            failure_class=failure_class,
            retryable=retryable,
            retry_kind=retry_kind,
            retry_owner=retry_owner,
            retry_after_seconds=retry_after_seconds,
            error_payload=payload,
            failure_reason=failure_reason,
            failure_origin="provider_response",
            classification_basis=classification_basis,
            recovery_disposition=recovery_disposition,
            failure_kind=failure_kind,
            body_sha256=hashlib.sha256(
                raw.encode("utf-8", errors="replace")
            ).hexdigest(),
            body_excerpt=diagnostic_message(raw),
            body_truncated=len(raw) > 400,
        )

    # These statuses have always represented deterministic request, auth, or
    # endpoint failures.  Their body may contain an unknown provider payload,
    # but diagnostics must not turn that payload into a retry route.
    hard_status: dict[int, tuple[str, ProviderErrorDisposition, ProviderFailureClass]] = {
        401: (
            "authentication_failed",
            "deterministic_auth_or_quota",
            "deterministic_provider_input_failure",
        ),
        403: (
            "permission_denied",
            "deterministic_auth_or_quota",
            "deterministic_provider_input_failure",
        ),
        404: (
            "endpoint_or_model_not_found",
            "deterministic_input",
            "deterministic_provider_input_failure",
        ),
        407: (
            "authentication_failed",
            "deterministic_auth_or_quota",
            "deterministic_provider_input_failure",
        ),
        413: (
            "invalid_request",
            "deterministic_input",
            "deterministic_provider_input_failure",
        ),
    }
    if status in hard_status:
        reason, disposition, failure_class = hard_status[status]
        if decision is not None and not decision.retryable and decision.failure_reason != "unknown":
            reason = decision.failure_reason
        return build(
            semantic_disposition=disposition,
            failure_class=failure_class,
            retryable=False,
            retry_kind=None,
            retry_owner=None,
            failure_reason=reason,
            classification_basis=f"http_status:{status}",
            recovery_disposition="repair_required",
        )

    # Preserve deterministic provider identifiers before applying status
    # fallback.  Classification diagnostics must not change their route.
    if decision is not None and not decision.retryable:
        return build(
            semantic_disposition=decision.semantic_disposition,
            failure_class=decision.failure_class,
            retryable=False,
            retry_kind=None,
            retry_owner=None,
            failure_reason=decision.failure_reason,
            classification_basis=decision.classification_basis,
            recovery_disposition=decision.recovery_disposition,
        )

    if _is_inner_request_retry(status=status, error_payload=payload):
        reason = (
            "rate_limited"
            if status == 429
            else "overloaded"
            if status == 529
            else "service_unavailable"
        )
        return build(
            semantic_disposition="retryable_provider",
            failure_class="transient_provider_failure",
            retryable=True,
            retry_kind="request",
            retry_owner="inner_transport",
            failure_reason=decision.failure_reason if decision else reason,
            classification_basis=(
                decision.classification_basis
                if decision and decision.retryable
                else f"http_status:{status}"
            ),
            recovery_disposition="delayed_retry_candidate",
            retry_after_seconds=decision.retry_after_seconds if decision else None,
        )

    if payload:
        if status == 400:
            return build(
                semantic_disposition="deterministic_input",
                failure_class="deterministic_provider_input_failure",
                retryable=False,
                retry_kind=None,
                retry_owner=None,
                failure_reason="invalid_request",
                classification_basis="http_status:400",
                recovery_disposition="repair_required",
            )
        if status in {401, 407}:
            return build(
                semantic_disposition="deterministic_auth_or_quota",
                failure_class="deterministic_provider_input_failure",
                retryable=False,
                retry_kind=None,
                retry_owner=None,
                failure_reason="authentication_failed",
                classification_basis=f"http_status:{status}",
                recovery_disposition="repair_required",
            )
        return build(
            semantic_disposition="retryable_provider",
            failure_class="transient_provider_failure",
            retryable=True,
            retry_kind="stream",
            retry_owner="outer_sampling",
            failure_reason=decision.failure_reason if decision else "unknown",
            classification_basis=(
                decision.classification_basis
                if decision
                else "unrecognized_http_error"
            ),
            recovery_disposition="delayed_retry_candidate",
            retry_after_seconds=decision.retry_after_seconds if decision else None,
            failure_kind="unexpected_http_status",
        )

    if status == 400:
        return build(
            semantic_disposition="deterministic_input",
            failure_class="deterministic_provider_input_failure",
            retryable=False,
            retry_kind=None,
            retry_owner=None,
            failure_reason="invalid_request",
            classification_basis="http_status:400",
            recovery_disposition="repair_required",
        )
    if status in {401, 407}:
        return build(
            semantic_disposition="deterministic_auth_or_quota",
            failure_class="deterministic_provider_input_failure",
            retryable=False,
            retry_kind=None,
            retry_owner=None,
            failure_reason="authentication_failed",
            classification_basis=f"http_status:{status}",
            recovery_disposition="repair_required",
        )
    return build(
        semantic_disposition="protocol_invalid",
        failure_class="provider_protocol_failure",
        retryable=False,
        retry_kind=None,
        retry_owner=None,
        failure_reason="unknown",
        classification_basis="unrecognized_http_error",
        recovery_disposition="unknown",
    )


def _http_error_payload(detail: str) -> dict[str, Any]:
    try:
        payload = json.loads(detail)
    except (json.JSONDecodeError, TypeError):
        return {}
    return _normalize_http_error_payload(payload)


def _is_inner_request_retry(
    *,
    status: int,
    error_payload: dict[str, Any],
) -> bool:
    if ProviderRetryPolicy.retry_http_status(status):
        return True
    if status != 400:
        return False
    identifier = str(
        error_payload.get("type") or error_payload.get("code") or ""
    ).strip().casefold()
    return identifier in {
        "api_error",
        "first_token_timeout",
        "internal_server_error",
        "overloaded_error",
        "server_error",
        "server_is_overloaded",
        "service_unavailable",
        "service_unavailable_error",
        "upstream_error",
        "upstream_unavailable",
    }


def _normalize_http_error_payload(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {}
    error = payload.get("error")
    candidate = error if isinstance(error, dict) else payload
    normalized = {
        key: candidate.get(key)
        for key in ("code", "type", "message", "param", "request_id")
        if key in candidate
    }
    if "request_id" not in normalized and payload.get("request_id") is not None:
        normalized["request_id"] = payload.get("request_id")
    if not any(str(value or "").strip() for value in normalized.values()):
        return {}
    return normalized


def _parse_retry_after_seconds(*, code: str, message: str) -> float | None:
    del code
    match = _RATE_LIMIT_RETRY_AFTER_PATTERN.search(message)
    if match is None:
        return None
    value = float(match.group(1))
    unit = match.group(2).casefold()
    seconds = value / 1000.0 if unit == "ms" else value
    return min(MAX_RETRY_DELAY_SECONDS, max(0.0, seconds))
