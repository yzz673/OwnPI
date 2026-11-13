"""Native function wire schemas and execution-only optional-null projection."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from typing import Any

from jsonschema import Draft202012Validator


def _allows_null(schema: dict[str, Any]) -> bool:
    return Draft202012Validator(schema).is_valid(None)


def _nullable_branch(schema: dict[str, Any]) -> dict[str, Any] | None:
    branches = schema.get('anyOf')
    if not isinstance(branches, list) or len(branches) != 2:
        return None
    for index, branch in enumerate(branches):
        if branch == {'type': 'null'} and isinstance(branches[1 - index], dict):
            return branches[1 - index]
    return None


@dataclass(frozen=True)
class NativeFunctionSchema:
    strict: bool
    parameters: dict[str, Any]
    reason: str = ''


class _UnsupportedSchema(Exception):
    pass


def native_function_schema(
    schema: dict[str, Any], *, strict: bool,
) -> NativeFunctionSchema:
    if not strict:
        return NativeFunctionSchema(False, deepcopy(schema))

    def convert(node: Any, path: str) -> dict[str, Any]:
        def incompatible(reason: str) -> None:
            raise _UnsupportedSchema(f'{path}: {reason}')

        if not isinstance(node, dict):
            incompatible('expected a schema object')
        supported = {'type', 'properties', 'required', 'additionalProperties', 'items',
                     'anyOf', 'enum', 'const', 'description', 'title', 'default', 'examples',
                     'minLength', 'maxLength', 'pattern', 'format', 'minimum', 'maximum',
                     'exclusiveMinimum', 'exclusiveMaximum', 'multipleOf', 'minItems', 'maxItems'}
        unknown = set(node) - supported
        if unknown:
            incompatible(f'unsupported keywords: {sorted(unknown)}')
        result = deepcopy(node)
        if 'anyOf' in node:
            branch = _nullable_branch(node)
            if path == '$' or branch is None:
                incompatible('only a nested schema-or-null anyOf is supported')
            result['anyOf'] = [convert(branch, f'{path}.anyOf'), {'type': 'null'}]
        node_type = node.get('type')
        types = node_type if isinstance(node_type, list) else [node_type]
        if 'anyOf' not in node and (not types or any(t not in {
            'object', 'array', 'string', 'integer', 'number', 'boolean', 'null',
        } for t in types)):
            incompatible('explicit supported type required')
        if 'format' in node and node['format'] not in {
            'date-time', 'time', 'date', 'duration', 'email', 'hostname', 'ipv4', 'ipv6', 'uuid',
        }:
            incompatible('unsupported format')
        if 'object' in types or 'properties' in node:
            if node.get('additionalProperties') is not False:
                incompatible('open objects/maps cannot be represented without changing their contract')
            properties = node.get('properties', {})
            required = node.get('required', [])
            result['properties'] = {}
            for name, child in properties.items():
                projected = convert(child, f'{path}.properties.{name}')
                if name not in required:
                    if _allows_null(child):
                        incompatible(f'optional nullable property {name} loses absence semantics')
                    projected = {'anyOf': [projected, {'type': 'null'}]}
                result['properties'][name] = projected
            result['required'] = list(properties)
            result['additionalProperties'] = False
        if 'items' in node:
            result['items'] = convert(node['items'], f'{path}.items')
        elif 'array' in types:
            incompatible('array requires an items schema')
        if 'const' in result:
            value = result.pop('const')
            result['enum'] = [value] if 'enum' not in result or value in result['enum'] else []
        return result

    if schema.get('type') != 'object':
        return NativeFunctionSchema(False, deepcopy(schema), 'root must be an object')
    try:
        return NativeFunctionSchema(True, convert(schema, '$'))
    except _UnsupportedSchema as error:
        return NativeFunctionSchema(False, deepcopy(schema), str(error))


def canonical_function_arguments(
    arguments: dict[str, Any], schema: dict[str, Any], *, strict: bool,
) -> dict[str, Any]:
    """Remove only null placeholders introduced for non-nullable optional keys."""
    if not native_function_schema(schema, strict=strict).strict:
        return deepcopy(arguments)

    def restore(value: Any, node: dict[str, Any]) -> Any:
        branch = _nullable_branch(node)
        if branch is not None and value is not None:
            node = branch
        if isinstance(value, dict):
            properties = node.get('properties', {})
            required = node.get('required', [])
            return {
                name: restore(child, properties.get(name, {}))
                for name, child in value.items()
                if not (
                    child is None and name in properties and name not in required
                    and not _allows_null(properties[name])
                )
            }
        if isinstance(value, list) and isinstance(node.get('items'), dict):
            return [restore(child, node['items']) for child in value]
        return deepcopy(value)

    return restore(arguments, schema)
