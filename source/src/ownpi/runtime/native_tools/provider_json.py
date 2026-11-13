from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Mapping


def _invalid_constant(value: str) -> None:
    raise ValueError(f'provider JSON cannot contain {value}')


@dataclass(frozen=True)
class FrozenProviderJson:
    canonical_json: str = '{}'

    def __post_init__(self) -> None:
        if not isinstance(self.canonical_json, str):
            raise TypeError('provider JSON storage must be an immutable string')
        value = json.loads(self.canonical_json, parse_constant=_invalid_constant)
        object.__setattr__(self, 'canonical_json', json.dumps(
            value, ensure_ascii=False, allow_nan=False, separators=(',', ':'), sort_keys=True,
        ))

    @classmethod
    def from_value(cls, value: Any) -> 'FrozenProviderJson':
        return cls(json.dumps(value, ensure_ascii=False, allow_nan=False))

    def to_value(self) -> Any:
        return json.loads(self.canonical_json)

    def to_object(self) -> dict[str, Any]:
        value = self.to_value()
        if not isinstance(value, dict):
            raise TypeError('provider JSON value is not an object')
        return value

    def to_dict(self) -> dict[str, Any]:
        return {'canonical_json': self.canonical_json}

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> 'FrozenProviderJson':
        return cls(canonical_json=data['canonical_json'])


def immutable_members(value: Any, member_type: type, label: str) -> None:
    if not isinstance(value, tuple) or any(not isinstance(member, member_type) for member in value):
        raise TypeError(f'provider output {label} must be a tuple of {member_type.__name__}')


def required_sequence(value: Any, label: str) -> list[Any] | tuple[Any, ...]:
    if not isinstance(value, (list, tuple)):
        raise TypeError(f'provider output {label} must be an array')
    return value


def required_mapping(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise TypeError(f'provider output {label} must be an object')
    return value


def string_fields(instance: object, names: tuple[str, ...]) -> None:
    for name in names:
        if not isinstance(getattr(instance, name), str):
            raise TypeError(f'provider output {name} must be a string')
