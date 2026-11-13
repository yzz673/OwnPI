from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
import shlex
from urllib.parse import quote

from ownpi.runtime.environment.materialization.contracts import (
    MaterializationFailure,
    MaterializationReceipt,
)
from ownpi.runtime.environment.materialization.safe_archive import extract_tar_gz_safely
from ownpi.runtime.environment.materialization.service import verify_manifest_files
from ownpi.runtime.environment.materialization.transport import BoundedTransport
from ownpi.runtime.environment.materialization.file_io import MaterializationFileIO
from ownpi.runtime.permissions.models import EffectivePermissions


_REPOSITORY = re.compile(r'^[A-Za-z0-9][A-Za-z0-9._-]{0,95}/[A-Za-z0-9][A-Za-z0-9._-]{0,95}$')
_REVISION = re.compile(r'^[A-Za-z0-9][A-Za-z0-9._/-]{0,199}$')
CONTENT_MODES = frozenset({'source_snapshot', 'git_worktree'})


class GitHubRepositoryAdapter:
    resource_kind = 'repository'

    def __init__(
        self,
        *,
        repository: str,
        revision: str,
        content_mode: str,
        api_endpoint: str,
        archive_endpoint: str,
        api_route_status: str,
        ssh_route_status: str,
        ssh_host_alias: str,
        ssh_identity_path: str,
        transport: BoundedTransport,
        effective_permissions: EffectivePermissions | None = None,
        max_archive_bytes: int = 2_000_000_000,
        max_expanded_bytes: int = 2_000_000_000,
    ) -> None:
        self.resource_identity = validate_repository(repository)
        self.requested_revision = validate_revision(revision)
        if content_mode not in CONTENT_MODES:
            raise ValueError(f'content_mode must be one of: {", ".join(sorted(CONTENT_MODES))}')
        self.content_mode = content_mode
        self.cache_variant = content_mode
        self.route_class = 'github_api_codeload' if content_mode == 'source_snapshot' else 'github_git_ssh'
        self._api_endpoint = api_endpoint.rstrip('/') or 'https://api.github.com'
        self._archive_endpoint = archive_endpoint.rstrip('/') or 'https://codeload.github.com'
        self._api_route_status = api_route_status
        self._ssh_route_status = ssh_route_status
        self._ssh_host_alias = ssh_host_alias.strip()
        self._ssh_identity_path = ssh_identity_path.strip()
        if self._ssh_host_alias and not re.fullmatch(r'[A-Za-z0-9._-]+', self._ssh_host_alias):
            raise ValueError('configured GitHub SSH host alias contains unsupported characters')
        if '\x00' in self._ssh_identity_path or '\n' in self._ssh_identity_path:
            raise ValueError('configured GitHub SSH identity path is invalid')
        self._transport = transport
        self._io = MaterializationFileIO(effective_permissions)
        self._max_archive_bytes = max(1, int(max_archive_bytes))
        self._max_expanded_bytes = max(1, int(max_expanded_bytes))

    async def resolve_revision(self, scratch_dir: Path) -> str:
        if self.content_mode == 'git_worktree':
            return await self._resolve_with_ssh()
        api_failure: MaterializationFailure | None = None
        if self._api_route_status != 'unavailable':
            try:
                return await self._resolve_with_api(scratch_dir)
            except MaterializationFailure as exc:
                api_failure = exc
        if self._ssh_route_status != 'unavailable' and self._ssh_host_alias:
            self.route_class = 'github_ssh_codeload'
            return await self._resolve_with_ssh()
        if api_failure is not None:
            raise api_failure
        raise MaterializationFailure(
            'no configured GitHub revision-resolution route is available',
            owner='runtime_capability_missing',
            code='github_revision_route_unavailable',
        )

    async def _resolve_with_ssh(self) -> str:
        if not self._ssh_host_alias:
            raise MaterializationFailure(
                'configured GitHub SSH route is required',
                owner='runtime_capability_missing',
                code='github_ssh_not_configured',
            )
        remote = f'git@{self._ssh_host_alias}:{self.resource_identity}.git'
        result = await self._transport.execute(
            ['git', 'ls-remote', remote, self.requested_revision],
            cwd=Path.cwd(),
            env=self._git_env(),
        )
        sha = result.stdout.strip().split()[0] if result.stdout.strip() else ''
        return self._validate_commit(sha)

    async def _resolve_with_api(self, scratch_dir: Path) -> str:
        url = (
            f'{self._api_endpoint}/repos/{quote(self.resource_identity, safe="/")}'
            f'/commits/{quote(self.requested_revision, safe="")}'
        )
        response_path = scratch_dir / 'commit.json'
        self._io.write(response_path)
        self._io.read(response_path)
        try:
            await self._transport.execute(
                [
                    'curl', '--fail', '--location', '--silent', '--show-error',
                    '--max-filesize', '1000000',
                    '--output', str(response_path), url,
                ],
                cwd=Path.cwd(),
                partial_path=response_path,
                max_partial_bytes=1_000_000,
            )
        except MaterializationFailure as exc:
            if any(marker in str(exc).casefold() for marker in ('401', 'unauthorized', 'private repository')):
                raise MaterializationFailure(
                    'GitHub repository requires runtime-managed credentials',
                    owner='user_input_required',
                    code='resource_credentials_required',
                ) from exc
            raise
        try:
            if response_path.stat().st_size > 1_000_000:
                raise MaterializationFailure(
                    'GitHub commit response exceeds the configured bound',
                    owner='runtime_capability_missing',
                    code='manifest_too_large',
                )
            payload = json.loads(self._io.read_text(response_path))
        except PermissionError:
            raise
        except (OSError, json.JSONDecodeError) as exc:
            raise MaterializationFailure(
                'GitHub commit response is invalid JSON',
                owner='runtime_capability_missing',
                code='invalid_github_response',
            ) from exc
        sha = str(payload.get('sha', '') if isinstance(payload, dict) else '')
        return self._validate_commit(sha)

    @staticmethod
    def _validate_commit(sha: str) -> str:
        if not re.fullmatch(r'[0-9a-fA-F]{40}', sha):
            raise MaterializationFailure(
                'GitHub route did not resolve an immutable commit',
                owner='runtime_capability_missing',
                code='revision_not_immutable',
            )
        return sha.lower()

    async def populate(self, staging_dir: Path, resolved_revision: str) -> dict[str, object]:
        if self.content_mode == 'source_snapshot':
            return await self._populate_snapshot(staging_dir, resolved_revision)
        return await self._populate_worktree(staging_dir, resolved_revision)

    async def verify_cache(self, published_dir: Path, receipt: MaterializationReceipt) -> bool:
        if self.content_mode == 'git_worktree':
            try:
                result = await self._transport.execute(
                    ['git', '-C', str(published_dir), 'rev-parse', 'HEAD'],
                    cwd=published_dir,
                    env=self._git_env(),
                )
                manifest = json.loads(
                    self._io.read_text(published_dir / receipt.manifest_ref)
                )
                return (
                    result.stdout.strip() == receipt.resolved_revision
                    and verify_manifest_files(
                        published_dir,
                        manifest,
                        expected_files=receipt.verified_files,
                        expected_bytes=receipt.verified_bytes,
                        ignored_top_level=('.git',),
                        io=self._io,
                    )
                )
            except PermissionError:
                raise
            except (OSError, ValueError, json.JSONDecodeError, MaterializationFailure):
                return False
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

    async def _populate_snapshot(self, staging_dir: Path, resolved_revision: str) -> dict[str, object]:
        owner, repository = self.resource_identity.split('/', 1)
        archive = staging_dir.parent / f'{staging_dir.name}.tar.gz.partial'
        self._io.write(archive)
        url = f'{self._archive_endpoint}/{quote(owner)}/{quote(repository)}/tar.gz/{resolved_revision}'
        try:
            await self._transport.execute(
                [
                    'curl', '--fail', '--location', '--silent', '--show-error',
                    '--max-filesize', str(self._max_archive_bytes),
                    '--continue-at', '-', '--output', str(archive), url,
                ],
                cwd=staging_dir.parent,
                partial_path=archive,
                max_partial_bytes=self._max_archive_bytes,
            )
            archive_digest = _sha256(archive, io=self._io)
            files, expanded_bytes = extract_tar_gz_safely(
                archive,
                staging_dir,
                max_expanded_bytes=self._max_expanded_bytes,
                io=self._io,
            )
        finally:
            self._io.unlink(archive)
        manifest_files = _snapshot_manifest(staging_dir, io=self._io)
        return {
            'route_class': self.route_class,
            'verified_files': files,
            'verified_bytes': expanded_bytes,
            'manifest': {
                'files': manifest_files,
                'archive_sha256': archive_digest,
                'git_history': False,
                'submodules_materialized': False,
                'git_lfs_materialized': False,
            },
            'warnings': ('git history, submodules, and Git LFS objects are not materialized',),
        }

    async def _populate_worktree(self, staging_dir: Path, resolved_revision: str) -> dict[str, object]:
        if not self._ssh_host_alias:
            raise MaterializationFailure(
                'configured GitHub SSH route is required for git_worktree',
                owner='runtime_capability_missing',
                code='github_ssh_not_configured',
            )
        remote = f'git@{self._ssh_host_alias}:{self.resource_identity}.git'
        env = self._git_env()
        await self._transport.execute(['git', 'init', str(staging_dir)], cwd=staging_dir.parent, env=env)
        await self._transport.execute(['git', '-C', str(staging_dir), 'remote', 'add', 'origin', remote], cwd=staging_dir, env=env)
        await self._transport.execute(
            ['git', '-C', str(staging_dir), 'fetch', '--depth=1', 'origin', resolved_revision],
            cwd=staging_dir,
            env=env,
        )
        await self._transport.execute(['git', '-C', str(staging_dir), 'checkout', '--detach', 'FETCH_HEAD'], cwd=staging_dir, env=env)
        result = await self._transport.execute(['git', '-C', str(staging_dir), 'rev-parse', 'HEAD'], cwd=staging_dir, env=env)
        if result.stdout.strip() != resolved_revision:
            raise MaterializationFailure(
                'Git worktree HEAD does not match resolved commit',
                owner='runtime_capability_missing',
                code='git_head_mismatch',
            )
        manifest_files = _snapshot_manifest(staging_dir, exclude_git=True, io=self._io)
        verified_bytes = sum(int(item['size']) for item in manifest_files)
        return {
            'route_class': self.route_class,
            'verified_files': len(manifest_files),
            'verified_bytes': verified_bytes,
            'manifest': {
                'files': manifest_files,
                'git_history': 'shallow',
                'submodules_materialized': False,
                'git_lfs_materialized': False,
                'push_enabled': False,
            },
            'warnings': ('submodules, Git LFS objects, and push are not enabled',),
        }

    def _git_env(self) -> dict[str, str]:
        if not self._ssh_identity_path:
            return {'GIT_TERMINAL_PROMPT': '0'}
        command = f'ssh -i {shlex.quote(self._ssh_identity_path)} -o BatchMode=yes'
        return {'GIT_SSH_COMMAND': command, 'GIT_TERMINAL_PROMPT': '0'}


def validate_repository(value: str) -> str:
    normalized = str(value or '').strip().removesuffix('.git')
    if not _REPOSITORY.fullmatch(normalized):
        raise ValueError('repository must use the GitHub owner/repository form')
    return normalized


def validate_revision(value: str) -> str:
    normalized = str(value or 'HEAD').strip()
    if not _REVISION.fullmatch(normalized) or '..' in normalized.split('/'):
        raise ValueError('revision contains unsupported characters')
    return normalized


def _snapshot_manifest(root: Path, *, exclude_git: bool = False, io: MaterializationFileIO) -> list[dict[str, object]]:
    files: list[dict[str, object]] = []
    for path in sorted(item for item in io.walk(root) if item.is_file()):
        relative = path.relative_to(root)
        if exclude_git and relative.parts and relative.parts[0] == '.git':
            continue
        if relative.name.startswith('.materialization-'):
            continue
        files.append(
            {
                'path': relative.as_posix(),
                'size': path.stat().st_size,
                'sha256': _sha256(path, io=io),
            }
        )
    return files


def _sha256(path: Path, *, io: MaterializationFileIO) -> str:
    io.read(path)
    digest = hashlib.sha256()
    with path.open('rb') as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()
