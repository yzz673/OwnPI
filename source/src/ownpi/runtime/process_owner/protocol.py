from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping


PROTOCOL_MAJOR = 1
PROTOCOL_MINOR = 0
PACKAGE_VERSION = "0.1.0"
SANDBOX_DIAGNOSTICS_CAPABILITY = "process.sandbox_diagnostics"
DEFAULT_CAPABILITIES = frozenset(
    {
        "process.read",
        "process.release",
        "process.signal",
        "process.start",
        "process.terminate",
        "process.write",
        "session.drain_and_shutdown",
    }
)
REQUIRED_CAPABILITIES = DEFAULT_CAPABILITIES


class ProcessOwnerProtocolError(ValueError):
    def __init__(self, message: str, *, start_outcome: str = "") -> None:
        super().__init__(message)
        self.start_outcome = start_outcome


@dataclass(frozen=True)
class ProtocolVersion:
    major: int = PROTOCOL_MAJOR
    minor: int = PROTOCOL_MINOR

    @classmethod
    def from_value(cls, value: Any) -> "ProtocolVersion":
        payload = require_mapping(value, name="protocol_version")
        reject_unknown_fields(
            payload, allowed={"major", "minor"}, name="protocol_version"
        )
        return cls(
            major=require_non_negative_int(
                payload.get("major"), name="protocol_version.major"
            ),
            minor=require_non_negative_int(
                payload.get("minor"), name="protocol_version.minor"
            ),
        )

    def to_dict(self) -> dict[str, int]:
        return {"major": self.major, "minor": self.minor}

    def negotiate(self, other: "ProtocolVersion") -> "ProtocolVersion":
        if self.major != other.major:
            raise ProcessOwnerProtocolError(
                f"protocol major mismatch: client={self.major} server={other.major}"
            )
        if self.minor > other.minor:
            raise ProcessOwnerProtocolError(
                f"protocol minor mismatch: client={self.minor} server={other.minor}"
            )
        return ProtocolVersion(major=self.major, minor=min(self.minor, other.minor))


@dataclass(frozen=True)
class InitializeParams:
    protocol_version: ProtocolVersion
    capabilities: tuple[str, ...]
    resume_session_id: str = ""
    package_version: str = ""

    @classmethod
    def from_value(cls, value: Any) -> "InitializeParams":
        payload = require_mapping(value, name="initialize params")
        reject_unknown_fields(
            payload,
            allowed={
                "protocol_version",
                "capabilities",
                "resume_session_id",
                "package_version",
            },
            name="initialize params",
        )
        capabilities = require_string_list(
            payload.get("capabilities", []), name="capabilities"
        )
        return cls(
            protocol_version=ProtocolVersion.from_value(
                payload.get("protocol_version")
            ),
            capabilities=tuple(dict.fromkeys(capabilities)),
            resume_session_id=optional_identifier(
                payload.get("resume_session_id"),
                name="resume_session_id",
            ),
            package_version=optional_string(
                payload.get("package_version"), name="package_version"
            ),
        )


def validate_request(value: Any) -> tuple[Any, str, dict[str, Any]]:
    payload = require_mapping(value, name="request")
    reject_unknown_fields(payload, allowed={"id", "method", "params"}, name="request")
    request_id = payload.get("id")
    if not isinstance(request_id, (str, int)) or isinstance(request_id, bool):
        raise ProcessOwnerProtocolError("request.id must be a string or integer")
    method = require_non_empty_string(payload.get("method"), name="request.method")
    params = require_mapping(payload.get("params", {}), name="request.params")
    return request_id, method, params


def success_response(request_id: Any, result: Mapping[str, Any]) -> dict[str, Any]:
    return {"id": request_id, "result": dict(result)}


def error_response(
    request_id: Any, *, code: str, message: str, start_outcome: str = "",
) -> dict[str, Any]:
    response = {
        "id": request_id,
        "error": {
            "code": str(code or "internal_error"),
            "message": str(message or "process owner request failed"),
        },
    }
    if start_outcome:
        response['error']['start_outcome'] = start_outcome
    return response


def notification(method: str, params: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "method": require_non_empty_string(method, name="notification.method"),
        "params": dict(params),
    }


def require_mapping(value: Any, *, name: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ProcessOwnerProtocolError(f"{name} must be an object")
    if any(not isinstance(key, str) for key in value):
        raise ProcessOwnerProtocolError(f"{name} field names must be strings")
    return dict(value)


def reject_unknown_fields(
    payload: Mapping[str, Any],
    *,
    allowed: set[str],
    name: str,
) -> None:
    unknown = sorted(set(payload) - allowed)
    if unknown:
        raise ProcessOwnerProtocolError(f"{name} contains unknown fields: {unknown}")


def require_non_empty_string(value: Any, *, name: str, max_length: int = 4096) -> str:
    if not isinstance(value, str):
        raise ProcessOwnerProtocolError(f"{name} must be a string")
    text = value.strip()
    if not text:
        raise ProcessOwnerProtocolError(f"{name} is required")
    if len(text) > max_length:
        raise ProcessOwnerProtocolError(f"{name} exceeds {max_length} characters")
    return text


def require_string(value: Any, *, name: str, max_length: int = 4096) -> str:
    if not isinstance(value, str):
        raise ProcessOwnerProtocolError(f"{name} must be a string")
    if len(value) > max_length:
        raise ProcessOwnerProtocolError(f"{name} exceeds {max_length} characters")
    return value


def optional_identifier(value: Any, *, name: str) -> str:
    if value in (None, ""):
        return ""
    return require_identifier(value, name=name)


def optional_string(value: Any, *, name: str, max_length: int = 200) -> str:
    if value in (None, ""):
        return ""
    if not isinstance(value, str):
        raise ProcessOwnerProtocolError(f"{name} must be a string")
    if len(value) > max_length:
        raise ProcessOwnerProtocolError(f"{name} exceeds {max_length} characters")
    return value


def require_identifier(value: Any, *, name: str) -> str:
    text = require_non_empty_string(value, name=name, max_length=200)
    if any(character.isspace() for character in text):
        raise ProcessOwnerProtocolError(f"{name} must not contain whitespace")
    return text


def require_non_negative_int(value: Any, *, name: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ProcessOwnerProtocolError(f"{name} must be a non-negative integer")
    return value


def require_positive_int(value: Any, *, name: str, maximum: int | None = None) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise ProcessOwnerProtocolError(f"{name} must be a positive integer")
    if maximum is not None and value > maximum:
        raise ProcessOwnerProtocolError(f"{name} exceeds maximum {maximum}")
    return value


def require_bool(value: Any, *, name: str) -> bool:
    if not isinstance(value, bool):
        raise ProcessOwnerProtocolError(f"{name} must be a boolean")
    return value


def require_string_list(
    value: Any,
    *,
    name: str,
    max_items: int = 4096,
    min_items: int = 0,
) -> list[str]:
    if not isinstance(value, list):
        raise ProcessOwnerProtocolError(f"{name} must be an array")
    if len(value) < min_items:
        raise ProcessOwnerProtocolError(
            f"{name} must contain at least {min_items} items"
        )
    if len(value) > max_items:
        raise ProcessOwnerProtocolError(f"{name} exceeds {max_items} items")
    return [require_non_empty_string(item, name=f"{name}[]") for item in value]


def require_string_mapping(
    value: Any, *, name: str, max_items: int = 4096
) -> dict[str, str]:
    payload = require_mapping(value, name=name)
    if len(payload) > max_items:
        raise ProcessOwnerProtocolError(f"{name} exceeds {max_items} entries")
    result: dict[str, str] = {}
    for key, item in payload.items():
        if not isinstance(item, str):
            raise ProcessOwnerProtocolError(f"{name} values must be strings")
        result[require_non_empty_string(key, name=f"{name}.key", max_length=1024)] = (
            item
        )
    return result
