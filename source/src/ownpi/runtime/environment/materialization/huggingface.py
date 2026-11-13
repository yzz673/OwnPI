from __future__ import annotations

from fnmatch import fnmatchcase
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
from urllib.parse import quote

from ownpi.runtime.environment.materialization.contracts import (
    MaterializationFailure,
    MaterializationReceipt,
)
from ownpi.runtime.environment.materialization.service import verify_manifest_files
from ownpi.runtime.environment.materialization.transport import BoundedTransport
from ownpi.runtime.environment.materialization.file_io import MaterializationFileIO
from ownpi.runtime.permissions.models import EffectivePermissions


_REPO_ID = re.compile(r'^[A-Za-z0-9][A-Za-z0-9._-]{0,95}/[A-Za-z0-9][A-Za-z0-9._-]{0,95}$')
_REVISION = re.compile(r'^[A-Za-z0-9][A-Za-z0-9._/-]{0,199}$')


class HuggingFaceSnapshotAdapter:
    resource_kind = 'model_snapshot'
    route_class = 'huggingface_mirror'

    def __init__(
        self,
        *,
        repo_id: str,
        revision: str,
        allow_patterns: tuple[str, ...],
        endpoint: str,
        transport: BoundedTransport,
        effective_permissions: EffectivePermissions | None = None,
        max_snapshot_bytes: int = 50_000_000_000,
    ) -> None:
        self.resource_identity = validate_repo_id(repo_id)
        self.requested_revision = validate_revision(revision)
        self._allow_patterns = validate_patterns(allow_patterns)
        self.cache_variant = hashlib.sha256(
            '\0'.join(self._allow_patterns).encode('utf-8')
        ).hexdigest()[:16]
        self._endpoint = endpoint.rstrip('/')
        self._transport = transport
        self._io = MaterializationFileIO(effective_permissions)
        self._max_snapshot_bytes = max(1, int(max_snapshot_bytes))
        self._metadata: dict[str, object] | None = None

    async def resolve_revision(self, scratch_dir: Path) -> str:
        if not self._endpoint:
            raise MaterializationFailure(
                'Hugging Face mirror endpoint is not configured',
                owner='runtime_capability_missing',
                code='mirror_not_configured',
            )
        url = (
            f'{self._endpoint}/api/models/{quote(self.resource_identity, safe="/")}'
            f'/revision/{quote(self.requested_revision, safe="")}?blobs=true'
        )
        metadata_path = scratch_dir / 'model-info.json'
        self._io.write(metadata_path)
        self._io.read(metadata_path)
        try:
            await self._transport.execute(
                [
                    'curl', '--fail', '--location', '--silent', '--show-error',
                    '--max-filesize', '8000000',
                    '--output', str(metadata_path), url,
                ],
                cwd=Path.cwd(),
                partial_path=metadata_path,
                max_partial_bytes=8_000_000,
            )
        except MaterializationFailure as exc:
            raise _classify_access_failure(exc, resource='Hugging Face model') from exc
        try:
            if metadata_path.stat().st_size > 8_000_000:
                raise MaterializationFailure(
                    'Hugging Face manifest exceeds the configured bound',
                    owner='runtime_capability_missing',
                    code='manifest_too_large',
                )
            metadata = json.loads(self._io.read_text(metadata_path))
        except PermissionError:
            raise
        except (OSError, json.JSONDecodeError) as exc:
            raise MaterializationFailure(
                'Hugging Face manifest response is invalid JSON',
                owner='runtime_capability_missing',
                code='invalid_huggingface_manifest',
            ) from exc
        sha = str(metadata.get('sha', '') if isinstance(metadata, dict) else '')
        if not re.fullmatch(r'[0-9a-fA-F]{40,64}', sha):
            raise MaterializationFailure(
                'Hugging Face response did not resolve an immutable revision',
                owner='runtime_capability_missing',
                code='revision_not_immutable',
            )
        self._metadata = metadata
        return sha.lower()

    async def populate(self, staging_dir: Path, resolved_revision: str) -> dict[str, object]:
        metadata = self._metadata or {}
        selected = _selected_files(metadata, self._allow_patterns)
        if not selected:
            raise MaterializationFailure(
                'Hugging Face manifest contains no files matching allow_patterns',
                owner='runtime_capability_missing',
                code='empty_model_snapshot',
            )
        declared_sizes = [int(item.get('size', -1)) for item in selected]
        if any(size < 0 for size in declared_sizes):
            raise MaterializationFailure(
                'Hugging Face manifest omitted a selected file size',
                owner='runtime_capability_missing',
                code='model_size_unknown',
            )
        if sum(declared_sizes) > self._max_snapshot_bytes:
            raise MaterializationFailure(
                'Hugging Face snapshot exceeds the configured byte limit',
                owner='runtime_capability_missing',
                code='snapshot_byte_limit_exceeded',
            )
        manifest_files: list[dict[str, object]] = []
        verified_bytes = 0
        for item in selected:
            relative = str(item['path'])
            destination = staging_dir / PurePosixPath(relative)
            self._io.write(destination)
            self._io.mkdir(destination.parent)
            partial = destination.with_name(f'{destination.name}.partial')
            self._io.write(partial)
            self._io.read(partial)
            url = (
                f'{self._endpoint}/{quote(self.resource_identity, safe="/")}/resolve/'
                f'{quote(resolved_revision, safe="")}/{quote(relative, safe="/")}'
            )
            expected_size = int(item.get('size', -1))
            await _download(
                self._transport,
                url=url,
                partial=partial,
                cwd=staging_dir,
                max_bytes=expected_size,
            )
            if not partial.is_file() or (expected_size >= 0 and partial.stat().st_size != expected_size):
                raise MaterializationFailure(
                    f'Hugging Face file size mismatch: {relative}',
                    owner='runtime_capability_missing',
                    code='model_size_mismatch',
                )
            digest = _sha256(partial, io=self._io)
            expected_sha = str(item.get('sha256', '') or '')
            if expected_sha and digest != expected_sha:
                raise MaterializationFailure(
                    f'Hugging Face LFS SHA256 mismatch: {relative}',
                    owner='runtime_capability_missing',
                    code='model_sha256_mismatch',
                )
            self._io.replace(partial, destination)
            self._io.read(destination)
            size = destination.stat().st_size
            verified_bytes += size
            manifest_files.append({'path': relative, 'size': size, 'sha256': digest})
        return {
            'route_class': self.route_class,
            'verified_files': len(manifest_files),
            'verified_bytes': verified_bytes,
            'manifest': {'files': manifest_files, 'offline_only': True},
        }

    async def verify_cache(self, published_dir: Path, receipt: MaterializationReceipt) -> bool:
        try:
            manifest = json.loads(self._io.read_text(published_dir / receipt.manifest_ref))
            return verify_manifest_files(
                published_dir,
                manifest,
                expected_files=receipt.verified_files,
                expected_bytes=receipt.verified_bytes,
                io=self._io,
            )
        except PermissionError:
            raise
        except (OSError, ValueError, json.JSONDecodeError, MaterializationFailure):
            return False


def validate_repo_id(value: str) -> str:
    normalized = str(value or '').strip()
    if not _REPO_ID.fullmatch(normalized):
        raise ValueError('repo_id must use the Hugging Face owner/repository form')
    return normalized


def validate_revision(value: str) -> str:
    normalized = str(value or 'main').strip()
    if not _REVISION.fullmatch(normalized) or '..' in normalized.split('/'):
        raise ValueError('revision contains unsupported characters')
    return normalized


def validate_patterns(values: tuple[str, ...]) -> tuple[str, ...]:
    patterns = values or ('*',)
    if len(patterns) > 32:
        raise ValueError('allow_patterns cannot contain more than 32 entries')
    output: list[str] = []
    for value in patterns:
        pattern = str(value or '').strip()
        if not pattern or pattern.startswith('/') or '..' in PurePosixPath(pattern).parts or len(pattern) > 200:
            raise ValueError('allow_patterns contains an unsafe pattern')
        output.append(pattern)
    return tuple(output)


def _selected_files(metadata: dict[str, object], patterns: tuple[str, ...]) -> list[dict[str, object]]:
    siblings = metadata.get('siblings', [])
    selected: list[dict[str, object]] = []
    if not isinstance(siblings, list):
        return selected
    for sibling in siblings:
        if not isinstance(sibling, dict):
            continue
        relative = str(sibling.get('rfilename', ''))
        path = PurePosixPath(relative)
        if not relative or path.is_absolute() or '..' in path.parts:
            raise MaterializationFailure(
                f'unsafe Hugging Face manifest path: {relative}',
                owner='runtime_capability_missing',
                code='unsafe_resource_path',
            )
        if not any(fnmatchcase(relative, pattern) for pattern in patterns):
            continue
        lfs = sibling.get('lfs', {}) if isinstance(sibling.get('lfs'), dict) else {}
        selected.append(
            {
                'path': relative,
                'size': int(lfs.get('size', sibling.get('size', -1)) or -1),
                'sha256': str(lfs.get('sha256', lfs.get('oid', '')) or '').removeprefix('sha256:'),
            }
        )
        if len(selected) > 2_048:
            raise MaterializationFailure(
                'Hugging Face snapshot selection exceeds the file-count bound',
                owner='runtime_capability_missing',
                code='snapshot_file_limit_exceeded',
            )
    return selected


def _sha256(path: Path, *, io: MaterializationFileIO) -> str:
    io.read(path)
    digest = hashlib.sha256()
    with path.open('rb') as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


async def _download(
    transport: BoundedTransport,
    *,
    url: str,
    partial: Path,
    cwd: Path,
    max_bytes: int,
) -> None:
    try:
        await transport.execute(
            [
                'curl', '--fail', '--location', '--silent', '--show-error',
                '--max-filesize', str(max_bytes),
                '--continue-at', '-', '--output', str(partial), url,
            ],
            cwd=cwd,
            partial_path=partial,
            max_partial_bytes=max_bytes,
        )
    except MaterializationFailure as curl_failure:
        try:
            await transport.execute(
                [
                    'aria2c', '--allow-overwrite=true', '--auto-file-renaming=false',
                    '--continue=true', '--dir', str(partial.parent), '--out', partial.name, url,
                ],
                cwd=cwd,
                partial_path=partial,
                max_partial_bytes=max_bytes,
            )
        except MaterializationFailure:
            raise _classify_access_failure(curl_failure, resource='Hugging Face model')


def _classify_access_failure(exc: MaterializationFailure, *, resource: str) -> MaterializationFailure:
    detail = str(exc).casefold()
    if any(marker in detail for marker in ('401', 'unauthorized', 'gated repository', 'private repository')):
        return MaterializationFailure(
            f'{resource} requires runtime-managed credentials',
            owner='user_input_required',
            code='resource_credentials_required',
        )
    return exc
