from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from ownpi.runtime.tools.surface_utils import build_input_validation_feedback

if TYPE_CHECKING:
    from ownpi.runtime.tools.definitions import ToolDefinition, ToolExecutionContext


def validate_business_input(
    *,
    definition: ToolDefinition,
    execution_context: ToolExecutionContext,
    input: dict[str, Any],
) -> dict[str, Any] | None:
    if definition.input_validator is None:
        return None
    try:
        feedback = definition.input_validator(execution_context, dict(input))
    except Exception as exc:
        return {
            "error": f"custom validator failed for {definition.name}: {exc}",
            "compact_hint": f"{definition.name} input validator crashed before execution.",
            "observation_summary": f"{definition.name} input validator crashed before execution.",
            "validation_source": "custom",
            "allowed_input_keys": [],
            "required_input_keys": [],
            "invalid_input_keys": [],
            "missing_required_input_keys": [],
            "default_input_examples": [],
        }
    if not isinstance(feedback, dict) or not feedback:
        return None
    error = str(feedback.get("error") or "").strip()
    compact_hint = str(feedback.get("compact_hint") or error or "").strip()
    if not error and not compact_hint:
        return None
    return {
        "error": error or f"invalid input for {definition.name}",
        "compact_hint": compact_hint
        or f"{definition.name} input failed custom validation.",
        "observation_summary": str(
            feedback.get("observation_summary")
            or compact_hint
            or error
            or f"{definition.name} input failed custom validation."
        ),
        "validation_source": "custom",
        "validation_code": (
            str(feedback.get("validation_code")).strip()
            if feedback.get("validation_code") is not None
            else None
        ),
        "allowed_input_keys": list(feedback.get("allowed_input_keys", [])),
        "required_input_keys": list(feedback.get("required_input_keys", [])),
        "invalid_input_keys": list(feedback.get("invalid_input_keys", [])),
        "missing_required_input_keys": list(
            feedback.get("missing_required_input_keys", [])
        ),
        "default_input_examples": list(feedback.get("default_input_examples", [])),
        **(
            {"tool_call_recovery": dict(feedback["tool_call_recovery"])}
            if isinstance(feedback.get("tool_call_recovery"), dict)
            else {}
        ),
        **{key: feedback[key] for key in (
            'recoverable_validation_error', 'recovery_hint', 'suggested_tool_name',
            'corrective_action', 'missing_targets', 'suggested_targets',
            'invalid_root', 'suggested_roots',
        ) if key in feedback},
    }


def parse_function_arguments(raw_arguments: str) -> dict[str, Any]:
    arguments = json.loads(raw_arguments)
    if not isinstance(arguments, dict):
        raise ValueError('function arguments must be a JSON object')
    return arguments


def validate_tool_input(
    definition: ToolDefinition,
    input: dict[str, Any],
) -> dict[str, Any] | None:
    schema = definition.tool_abi().input_schema
    if not isinstance(input, dict):
        return {
            "error": f"invalid input for {definition.name}: expected object",
            "compact_hint": f"{definition.name} expects a JSON object input.",
            "violation_kind": "shape_mismatch",
            "validation_source": "schema",
            "allowed_input_keys": [],
            "required_input_keys": [],
            "invalid_input_keys": [],
            "missing_required_input_keys": [],
            "default_input_examples": [],
        }
    model_input = input
    if not isinstance(schema, dict):
        return None
    model_schema = schema
    required = [
        str(item)
        for item in model_schema.get("required", [])
        if str(item).strip()
    ]
    missing = [item for item in required if item not in model_input]
    properties = model_schema.get("properties")
    unexpected: list[str] = []
    if model_schema.get("additionalProperties", True) is False and isinstance(
        properties, dict
    ):
        allowed_keys = {str(key) for key in properties}
        unexpected = [key for key in model_input if key not in allowed_keys]
    if missing or unexpected:
        detail_payload = (
            definition.detail_builder() if definition.detail_builder is not None else {}
        )
        if not isinstance(detail_payload, dict):
            detail_payload = {}
        default_examples = (
            detail_payload.get("default_input_examples")
            if isinstance(detail_payload, dict)
            else None
        )
        return build_input_validation_feedback(
            tool_name=definition.name,
            schema=model_schema,
            input=model_input,
            default_input_examples=default_examples
            if isinstance(default_examples, list)
            else None,
        )
    issue = find_schema_validation_issue(model_input, model_schema)
    if issue is not None:
        return _schema_issue_feedback(tool_name=definition.name, issue=issue)
    return None


def _schema_issue_feedback(
    *,
    tool_name: str,
    issue: SchemaValidationIssue,
) -> dict[str, Any]:
    prefix = f"failed to parse {tool_name} arguments: field `{issue.path}`"
    if issue.kind == "type":
        error = (
            f"{prefix} has invalid type; expected {issue.expected}, "
            f"got {issue.received}"
        )
    elif issue.kind == "required":
        error = f"{prefix} is required"
    elif issue.kind == "unknown":
        error = f"{prefix} is not allowed; allowed fields: {issue.allowed}"
    elif issue.kind == "enum":
        error = f"{prefix} must be one of {issue.allowed}"
    else:
        allowed = issue.allowed
        if allowed.startswith("[") and allowed.endswith("]"):
            allowed = allowed[1:-1]
        error = f"{prefix} must equal {allowed}"
    return {
        "error": error,
        "compact_hint": error,
        "violation_kind": "shape_mismatch",
        "validation_source": "schema",
        "validation_field": issue.path,
        "allowed_input_keys": [],
        "required_input_keys": [],
        "invalid_input_keys": [],
        "missing_required_input_keys": [],
        "default_input_examples": [],
    }


_JSON_TYPES = frozenset(
    {"array", "boolean", "integer", "null", "number", "object", "string"}
)


@dataclass(frozen=True)
class SchemaValidationIssue:
    path: str
    kind: str
    expected: str = ""
    received: str = ""
    allowed: str = ""


def find_schema_validation_issue(
    value: Any,
    schema: Any,
    *,
    path: str = "",
) -> SchemaValidationIssue | None:
    if not isinstance(schema, Mapping):
        return None
    expected_types = _expected_types(schema.get("type"))
    if expected_types and not any(
        _matches_json_type(value, expected) for expected in expected_types
    ):
        return SchemaValidationIssue(
            path=path or "$",
            kind="type",
            expected=_format_expected_types(expected_types),
            received=_json_type_name(value),
        )
    if "enum" in schema and isinstance(schema["enum"], list):
        allowed_values = schema["enum"]
        if not any(_json_values_equal(value, candidate) for candidate in allowed_values):
            return SchemaValidationIssue(
                path=path or "$",
                kind="enum",
                allowed=_format_allowed_values(allowed_values),
            )
    if "const" in schema and not _json_values_equal(value, schema["const"]):
        return SchemaValidationIssue(
            path=path or "$",
            kind="const",
            allowed=_format_allowed_values([schema["const"]]),
        )
    if isinstance(value, Mapping):
        issue = _validate_object(value, schema, path=path)
        if issue is not None:
            return issue
    if isinstance(value, list) and isinstance(schema.get("items"), Mapping):
        for index, item in enumerate(value):
            issue = find_schema_validation_issue(
                item,
                schema["items"],
                path=f"{path}[{index}]" if path else f"[{index}]",
            )
            if issue is not None:
                return issue
    return None


def _validate_object(
    value: Mapping[Any, Any],
    schema: Mapping[Any, Any],
    *,
    path: str,
) -> SchemaValidationIssue | None:
    properties = schema.get("properties")
    property_schemas = properties if isinstance(properties, Mapping) else {}
    required = schema.get("required")
    if isinstance(required, list):
        for raw_name in required:
            name = str(raw_name)
            if name and name not in value:
                return SchemaValidationIssue(
                    path=_field_path(path, name),
                    kind="required",
                )
    if schema.get("additionalProperties", True) is False:
        allowed_names = tuple(str(name) for name in property_schemas)
        for raw_name in value:
            name = str(raw_name)
            if name not in property_schemas:
                return SchemaValidationIssue(
                    path=_field_path(path, name),
                    kind="unknown",
                    allowed=", ".join(allowed_names[:16]) or "none",
                )
    for raw_name, property_schema in property_schemas.items():
        name = str(raw_name)
        if name not in value:
            continue
        issue = find_schema_validation_issue(
            value[name],
            property_schema,
            path=_field_path(path, name),
        )
        if issue is not None:
            return issue
    return None


def _expected_types(value: Any) -> tuple[str, ...]:
    candidates = value if isinstance(value, list) else [value]
    return tuple(
        candidate
        for candidate in candidates
        if isinstance(candidate, str) and candidate in _JSON_TYPES
    )


def _matches_json_type(value: Any, expected: str) -> bool:
    if expected == "null":
        return value is None
    if expected == "boolean":
        return isinstance(value, bool)
    if expected == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if expected == "string":
        return isinstance(value, str)
    if expected == "array":
        return isinstance(value, list)
    if expected == "object":
        return isinstance(value, Mapping)
    return True


def _json_type_name(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    if isinstance(value, Mapping):
        return "object"
    return type(value).__name__


def _json_values_equal(left: Any, right: Any) -> bool:
    return _json_type_name(left) == _json_type_name(right) and left == right


def _format_expected_types(expected_types: tuple[str, ...]) -> str:
    if len(expected_types) == 1:
        return expected_types[0]
    return " or ".join(expected_types)


def _format_allowed_values(values: list[Any]) -> str:
    rendered = json.dumps(values[:8], ensure_ascii=True, separators=(", ", ": "))
    if len(values) > 8:
        rendered = f"{rendered[:-1]}, \"...\"]"
    if len(rendered) > 240:
        rendered = f"{rendered[:236]}...]"
    return rendered


def _field_path(parent: str, name: str) -> str:
    return f"{parent}.{name}" if parent else name
