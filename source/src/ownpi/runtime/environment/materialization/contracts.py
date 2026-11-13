from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Protocol


FAILURE_OWNERS = frozenset(
    {'runtime_capability_missing', 'user_input_required', 'policy_owned', 'external_route'}
)
ROUTE_STATUSES = frozenset({'available', 'degraded', 'unavailable'})


class MaterializationFailure(RuntimeError):
    def __init__(self, message: str, *, owner: str, code: str) -> None:
        super().__init__(message)
        if owner not in FAILURE_OWNERS:
            raise ValueError(f'unsupported materialization failure owner: {owner}')
        self.owner = owner
        self.code = code


@dataclass(frozen=True)
class RouteCapability:
    route_class: str
    status: str = 'unavailable'

    def __post_init__(self) -> None:
        if self.status not in ROUTE_STATUSES:
            raise ValueError(f'unsupported route status: {self.status}')


@dataclass(frozen=True)
class TransportRequest:
    argv: tuple[str, ...]
    cwd: Path
    timeout_seconds: int
    partial_path: Path | None = None
    max_partial_bytes: int | None = None
    env: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class TransportResult:
    ok: bool
    exit_code: int | None = None
    stdout: str = ''
    stderr: str = ''
    timed_out: bool = False


class TransportExecutor(Protocol):
    async def __call__(self, request: TransportRequest) -> TransportResult: ...


@dataclass(frozen=True)
class MaterializationReceipt:
    resource_kind: str
    resource_identity: str
    requested_revision: str
    resolved_revision: str
    route_class: str
    verified_files: int
    verified_bytes: int
    cache_status: str
    manifest_ref: str
    warnings: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, object]:
        payload = asdict(self)
        payload['warnings'] = list(self.warnings)
        return payload


@dataclass(frozen=True)
class MaterializationResult:
    ok: bool
    local_path: Path | None = None
    receipt: MaterializationReceipt | None = None
    receipt_ref: Path | None = None
    error: str = ''
    failure_owner: str = ''
    failure_code: str = ''

    def bounded_payload(self) -> dict[str, object]:
        receipt = self.receipt
        return {
            'status': 'materialized' if self.ok else 'unavailable',
            'resource_kind': receipt.resource_kind if receipt else '',
            'resource_identity': receipt.resource_identity if receipt else '',
            'requested_revision': receipt.requested_revision if receipt else '',
            'resolved_revision': receipt.resolved_revision if receipt else '',
            'local_path': str(self.local_path) if self.local_path else '',
            'cache_status': receipt.cache_status if receipt else '',
            'route_class': receipt.route_class if receipt else '',
            'verified_files': receipt.verified_files if receipt else 0,
            'verified_bytes': receipt.verified_bytes if receipt else 0,
            'manifest_ref': receipt.manifest_ref if receipt else '',
            'receipt_ref': str(self.receipt_ref) if self.receipt_ref else '',
            'warnings': list(receipt.warnings[:8]) if receipt else [],
            'failure_owner': self.failure_owner,
            'failure_code': self.failure_code,
            'error': self.error[:1000],
        }


class MaterializationAdapter(Protocol):
    resource_kind: str
    resource_identity: str
    requested_revision: str
    route_class: str
    cache_variant: str

    async def resolve_revision(self, scratch_dir: Path) -> str: ...

    async def populate(self, staging_dir: Path, resolved_revision: str) -> dict[str, object]: ...

    async def verify_cache(self, published_dir: Path, receipt: MaterializationReceipt) -> bool: ...
