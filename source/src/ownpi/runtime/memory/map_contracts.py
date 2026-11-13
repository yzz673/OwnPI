from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from enum import Enum
from typing import Any


MEMORY_MAP_SCHEMA_VERSION = 1
MEMORY_MAP_INJECTION_OWNER = 'runtime.memory.MemoryMap'
MAX_MEMORY_IDENTITY_LENGTH = 512
MAX_MEMORY_TOOL_NAME_LENGTH = 128
_TOOL_NAME_PATTERN = re.compile(r'^[A-Za-z][A-Za-z0-9_.-]*$')
_OPAQUE_ID_PATTERN = re.compile(r'^[A-Za-z0-9][A-Za-z0-9_.:@+-]*$')
_CALL_SCOPE_ID_PATTERN = re.compile(r'^memory-call:[0-9a-f]{24}$')
_MAP_ID_PATTERN = re.compile(r'^memory-map:[0-9a-f]{24}$')
_SHA256_PATTERN = re.compile(r'^[0-9a-f]{64}$')


class MemoryDomain(str, Enum):
    INTERNAL_MEMORY = 'internal_memory'
    EXTERNAL_KNOWLEDGE = 'external_knowledge'


class MemoryMapStatus(str, Enum):
    AVAILABLE = 'available'
    UNAVAILABLE = 'unavailable'
    DEGRADED = 'degraded'


class MemoryAuthority(str, Enum):
    AGENT_OWNED = 'agent_owned'
    FACTORY_OWNED = 'factory_owned'


class MemoryMutability(str, Enum):
    WRITABLE = 'writable'
    READ_ONLY = 'read_only'


@dataclass(frozen=True)
class MemoryMapSection:
    domain: MemoryDomain
    status: MemoryMapStatus
    authority: MemoryAuthority
    mutability: MemoryMutability
    search_tool: str = ''
    read_tool: str = ''
    identity: str = ''
    identity_digest: str = ''
    scopes: tuple[str, ...] = ()
    schema_version: int = MEMORY_MAP_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != MEMORY_MAP_SCHEMA_VERSION:
            raise ValueError('unsupported memory map section schema version')
        _require_enum(self.domain, MemoryDomain, field_name='domain')
        _require_enum(self.status, MemoryMapStatus, field_name='status')
        _require_enum(self.authority, MemoryAuthority, field_name='authority')
        _require_enum(self.mutability, MemoryMutability, field_name='mutability')
        _validate_scalar(self.identity, field_name='identity', max_length=MAX_MEMORY_IDENTITY_LENGTH)
        if self.identity and _OPAQUE_ID_PATTERN.fullmatch(self.identity) is None:
            raise ValueError('memory map section identity must be an opaque canonical identifier')
        expected_authority = (
            MemoryAuthority.AGENT_OWNED
            if self.domain == MemoryDomain.INTERNAL_MEMORY
            else MemoryAuthority.FACTORY_OWNED
        )
        expected_mutability = (
            MemoryMutability.WRITABLE
            if self.domain == MemoryDomain.INTERNAL_MEMORY
            else MemoryMutability.READ_ONLY
        )
        if self.authority != expected_authority or self.mutability != expected_mutability:
            raise ValueError('memory map section authority or mutability conflicts with its domain')
        for field_name in ('search_tool', 'read_tool'):
            value = getattr(self, field_name)
            _validate_scalar(value, field_name=field_name, max_length=MAX_MEMORY_TOOL_NAME_LENGTH)
            if value and _TOOL_NAME_PATTERN.fullmatch(value) is None:
                raise ValueError(f'{field_name} is not a canonical tool name')
        if bool(self.identity) != bool(self.identity_digest):
            raise ValueError('memory map section identity and digest must be present together')
        if self.identity_digest and self.identity_digest != _sha256_text(self.identity):
            raise ValueError('memory map section identity digest does not match identity')
        if not isinstance(self.scopes, tuple):
            raise TypeError('memory map section scopes must use an immutable tuple')
        if len(self.scopes) > 16 or len(set(self.scopes)) != len(self.scopes):
            raise ValueError('memory map section scopes must be bounded and unique')
        for scope in self.scopes:
            _validate_scalar(scope, field_name='scope', max_length=64)
            if _TOOL_NAME_PATTERN.fullmatch(scope) is None:
                raise ValueError('memory map section scope must be a canonical identifier')
        if self.status == MemoryMapStatus.UNAVAILABLE:
            if self.search_tool or self.read_tool:
                raise ValueError('unavailable memory map section cannot advertise tools')
        elif not self.identity or not self.search_tool or not self.read_tool:
            raise ValueError('callable memory map section requires identity and search/read tools')

    @classmethod
    def create(
        cls,
        *,
        domain: MemoryDomain,
        status: MemoryMapStatus,
        authority: MemoryAuthority,
        mutability: MemoryMutability,
        search_tool: str = '',
        read_tool: str = '',
        identity: str = '',
        scopes: tuple[str, ...] = (),
    ) -> MemoryMapSection:
        normalized_identity = str(identity or '').strip()
        return cls(
            domain=domain,
            status=status,
            authority=authority,
            mutability=mutability,
            search_tool=str(search_tool or '').strip(),
            read_tool=str(read_tool or '').strip(),
            identity=normalized_identity,
            identity_digest=_sha256_text(normalized_identity) if normalized_identity else '',
            scopes=scopes,
        )

    def to_identity_dict(self) -> dict[str, Any]:
        return {
            'schema_version': self.schema_version,
            'domain': self.domain.value,
            'status': self.status.value,
            'authority': self.authority.value,
            'mutability': self.mutability.value,
            'search_tool': self.search_tool,
            'read_tool': self.read_tool,
            'identity': self.identity,
            'identity_digest': self.identity_digest,
            'scopes': list(self.scopes),
        }


@dataclass(frozen=True)
class MemoryMapIdentityReceipt:
    map_id: str
    digest: str
    call_scope_id: str
    section_statuses: tuple[tuple[str, str], ...]
    rendered_bytes: int
    owner: str = MEMORY_MAP_INJECTION_OWNER
    schema_version: int = MEMORY_MAP_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != MEMORY_MAP_SCHEMA_VERSION:
            raise ValueError('unsupported memory map receipt schema version')
        for field_name in ('map_id', 'digest', 'call_scope_id', 'owner'):
            if not str(getattr(self, field_name) or '').strip():
                raise ValueError(f'{field_name} must be non-empty')
        _validate_map_identity_fields(
            map_id=self.map_id,
            digest=self.digest,
            call_scope_id=self.call_scope_id,
        )
        if self.rendered_bytes < 0:
            raise ValueError('rendered_bytes must be non-negative')
        expected_domains = tuple(domain.value for domain in MemoryDomain)
        if tuple(domain for domain, _status in self.section_statuses) != expected_domains:
            raise ValueError('memory map receipt statuses must use stable internal/external order')
        valid_statuses = {status.value for status in MemoryMapStatus}
        if any(status not in valid_statuses for _domain, status in self.section_statuses):
            raise ValueError('memory map receipt contains an unsupported section status')

    def to_dict(self) -> dict[str, Any]:
        statuses = dict(self.section_statuses)
        return {
            'schema_version': self.schema_version,
            'map_id': self.map_id,
            'digest': self.digest,
            'call_scope_id': self.call_scope_id,
            'owner': self.owner,
            'internal_status': statuses.get(MemoryDomain.INTERNAL_MEMORY.value, ''),
            'external_status': statuses.get(MemoryDomain.EXTERNAL_KNOWLEDGE.value, ''),
            'rendered_bytes': self.rendered_bytes,
        }


@dataclass(frozen=True)
class MemoryMap:
    map_id: str
    call_scope_id: str
    sections: tuple[MemoryMapSection, MemoryMapSection]
    digest: str
    schema_version: int = MEMORY_MAP_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != MEMORY_MAP_SCHEMA_VERSION:
            raise ValueError('unsupported memory map schema version')
        if not self.map_id.strip() or not self.call_scope_id.strip():
            raise ValueError('memory map identity fields must be non-empty')
        _validate_map_identity_fields(
            map_id=self.map_id,
            digest=self.digest,
            call_scope_id=self.call_scope_id,
        )
        expected_domains = (
            MemoryDomain.INTERNAL_MEMORY,
            MemoryDomain.EXTERNAL_KNOWLEDGE,
        )
        if tuple(section.domain for section in self.sections) != expected_domains:
            raise ValueError('memory map sections must use stable internal/external order')
        expected_digest = memory_map_digest(self.sections)
        if self.digest != expected_digest:
            raise ValueError('memory map digest does not match sections')
        if self.map_id != memory_map_id(self.call_scope_id, self.digest):
            raise ValueError('memory map id does not match scope and digest')

    def identity_receipt(self, *, rendered_text: str) -> MemoryMapIdentityReceipt:
        return MemoryMapIdentityReceipt(
            map_id=self.map_id,
            digest=self.digest,
            call_scope_id=self.call_scope_id,
            section_statuses=tuple(
                (section.domain.value, section.status.value) for section in self.sections
            ),
            rendered_bytes=len(rendered_text.encode('utf-8')),
        )


def render_memory_map(memory_map: MemoryMap) -> str:
    lines = [
        'Memory map (navigation only; no memory body is embedded):',
        f'- map_id={memory_map.map_id}',
        f'- call_scope_id={memory_map.call_scope_id}',
    ]
    for section in memory_map.sections:
        lines.append(
            ' '.join(
                (
                    f'- domain={section.domain.value}',
                    f'status={section.status.value}',
                    f'authority={section.authority.value}',
                    f'mutability={section.mutability.value}',
                )
            )
        )
        if section.identity and section.status != MemoryMapStatus.UNAVAILABLE:
            lines.append(f'  identity={section.identity}')
        if section.scopes and section.status != MemoryMapStatus.UNAVAILABLE:
            lines.append(f"  scopes: {','.join(section.scopes)}")
        if section.search_tool and section.read_tool:
            lines.append(f'  tools: search={section.search_tool} read={section.read_tool}')
        if (
            section.domain == MemoryDomain.EXTERNAL_KNOWLEDGE
            and section.status != MemoryMapStatus.UNAVAILABLE
        ):
            from ownpi.runtime.memory.external.navigation import (
                render_external_knowledge_hierarchy_guide,
            )

            lines.append(render_external_knowledge_hierarchy_guide())
    return '\n'.join(lines)


def memory_map_digest(
    sections: tuple[MemoryMapSection, MemoryMapSection],
) -> str:
    return _sha256_payload(
        {
            'schema_version': MEMORY_MAP_SCHEMA_VERSION,
            'sections': [section.to_identity_dict() for section in sections],
        }
    )


def memory_map_id(call_scope_id: str, digest: str) -> str:
    if not call_scope_id.strip() or not digest.strip():
        raise ValueError('memory map id requires scope and digest')
    if _CALL_SCOPE_ID_PATTERN.fullmatch(call_scope_id) is None:
        raise ValueError('memory map id requires a canonical call scope id')
    if _SHA256_PATTERN.fullmatch(digest) is None:
        raise ValueError('memory map id requires a canonical SHA-256 digest')
    identity_digest = _sha256_text(f'{call_scope_id}\x1f{digest}')
    return f'memory-map:{identity_digest[:24]}'


def _validate_scalar(value: object, *, field_name: str, max_length: int) -> None:
    if not isinstance(value, str):
        raise TypeError(f'{field_name} must be a string')
    if len(value) > max_length:
        raise ValueError(f'{field_name} exceeds maximum length')


def _require_enum(value: object, enum_type: type[Enum], *, field_name: str) -> None:
    if not isinstance(value, enum_type):
        raise TypeError(f'{field_name} must use {enum_type.__name__}')


def _validate_map_identity_fields(*, map_id: str, digest: str, call_scope_id: str) -> None:
    if _MAP_ID_PATTERN.fullmatch(map_id) is None:
        raise ValueError('memory map id must be a canonical opaque identifier')
    if _CALL_SCOPE_ID_PATTERN.fullmatch(call_scope_id) is None:
        raise ValueError('memory map call scope id must be a canonical opaque identifier')
    if _SHA256_PATTERN.fullmatch(digest) is None:
        raise ValueError('memory map digest must be a canonical SHA-256 digest')


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode('utf-8')).hexdigest()


def _sha256_payload(payload: dict[str, Any]) -> str:
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(',', ':'),
        ensure_ascii=False,
    ).encode('utf-8')
    return hashlib.sha256(encoded).hexdigest()
