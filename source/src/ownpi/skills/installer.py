from __future__ import annotations

import json
import os
import re
import shutil
import stat
import subprocess
import tempfile
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Callable

import yaml

from ownpi.app.paths import AppPaths
from ownpi.skills.bundled import get_bundled_skill_summaries
from ownpi.skills.catalog import build_skill_catalog_result, parse_skill_file

DEFAULT_SKILL_REPO = 'openai/skills'
DEFAULT_SKILL_REF = 'main'
SKILL_CHANNEL_PATHS = {
    'curated': 'skills/.curated',
    'experimental': 'skills/.experimental',
}

_MARKDOWN_LINK_PATTERN = re.compile(r'\[[^\]]+\]\(([^)]+)\)')
_RequestBytes = Callable[[str, str], bytes]


class SkillInstallerError(RuntimeError):
    def __init__(self, message: str, *, code: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class AvailableSkillInfo:
    name: str
    repo: str
    path: str
    ref: str
    channel: str
    installed: bool
    disabled: bool
    shadowed: bool

    def to_dict(self) -> dict[str, str | bool]:
        return {
            'name': self.name,
            'repo': self.repo,
            'path': self.path,
            'ref': self.ref,
            'channel': self.channel,
            'installed': self.installed,
            'disabled': self.disabled,
            'shadowed': self.shadowed,
        }


def list_available_skills(
    app_paths: AppPaths,
    *,
    channel: str = 'curated',
    repo: str = DEFAULT_SKILL_REPO,
    ref: str = DEFAULT_SKILL_REF,
    path: str | None = None,
    request_bytes: _RequestBytes | None = None,
) -> list[AvailableSkillInfo]:
    normalized_channel = channel.strip().casefold()
    repo_path = path or SKILL_CHANNEL_PATHS.get(normalized_channel)
    if not repo_path:
        raise SkillInstallerError(f'Unsupported skill channel: {channel}', code='invalid_channel')
    owner, repository = _split_repo(repo)
    request = request_bytes or _github_request
    url = (
        f'https://api.github.com/repos/{urllib.parse.quote(owner)}/'
        f'{urllib.parse.quote(repository)}/contents/{repo_path}?ref={urllib.parse.quote(ref)}'
    )
    try:
        payload = json.loads(request(url, 'ownpi-skill-list').decode('utf-8'))
    except urllib.error.HTTPError as exc:
        code = 'github_auth_failed' if exc.code in {401, 403} else 'github_listing_failed'
        raise SkillInstallerError(f'GitHub skill listing failed with HTTP {exc.code}.', code=code) from exc
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise SkillInstallerError(f'GitHub skill listing failed: {exc}', code='github_listing_failed') from exc
    if not isinstance(payload, list):
        raise SkillInstallerError('GitHub skill listing returned an unexpected payload.', code='invalid_listing')

    catalog = build_skill_catalog_result(app_paths.get_skill_roots())
    visible = {item.name.casefold(): item for item in [*catalog.skills, *get_bundled_skill_summaries()]}
    disabled = _disabled_skill_names(app_paths)
    results: list[AvailableSkillInfo] = []
    for item in payload:
        if not isinstance(item, dict) or item.get('type') != 'dir':
            continue
        name = str(item.get('name', '')).strip()
        if not name:
            continue
        destination_exists = (app_paths.skills_dir / name).exists()
        visible_item = visible.get(name.casefold())
        results.append(
            AvailableSkillInfo(
                name=name,
                repo=f'{owner}/{repository}',
                path=f'{repo_path.rstrip("/")}/{name}',
                ref=ref,
                channel=normalized_channel,
                installed=destination_exists,
                disabled=name.casefold() in disabled,
                shadowed=bool(visible_item is not None and visible_item.source != 'app_home' and not destination_exists),
            )
        )
    return sorted(results, key=lambda item: item.name.casefold())


def install_local_skill(
    app_paths: AppPaths,
    source: Path,
    *,
    name: str | None = None,
) -> Path:
    source_root = _normalize_skill_root(source.expanduser())
    return _atomic_install(app_paths, source_root, name or source_root.name)


def install_github_skill(
    app_paths: AppPaths,
    *,
    repo: str,
    ref: str,
    path: str,
    name: str | None = None,
    method: str = 'auto',
    request_bytes: _RequestBytes | None = None,
) -> Path:
    owner, repository = _split_repo(repo)
    relative_path = _validated_relative_path(path)
    if method not in {'auto', 'download', 'git'}:
        raise SkillInstallerError(f'Unsupported install method: {method}', code='invalid_method')
    request = request_bytes or _github_request
    with tempfile.TemporaryDirectory(prefix='ownpi-skill-source-') as temp_dir:
        temp_root = Path(temp_dir)
        repo_root: Path | None = None
        download_error: SkillInstallerError | None = None
        if method in {'auto', 'download'}:
            try:
                repo_root = _download_repository(owner, repository, ref, temp_root, request)
            except SkillInstallerError as exc:
                download_error = exc
                if method == 'download' or exc.code not in {'github_auth_failed', 'github_not_found'}:
                    raise
        if repo_root is None and method in {'auto', 'git'}:
            try:
                repo_root = _git_sparse_checkout(owner, repository, ref, relative_path, temp_root)
            except SkillInstallerError:
                if download_error is not None:
                    raise download_error
                raise
        if repo_root is None:
            raise SkillInstallerError('Unable to materialize GitHub repository.', code='materialization_failed')
        source_root = _normalize_skill_root(repo_root / relative_path)
        return _atomic_install(app_paths, source_root, name or source_root.name)


def _download_repository(
    owner: str,
    repository: str,
    ref: str,
    temp_root: Path,
    request: _RequestBytes,
) -> Path:
    url = f'https://codeload.github.com/{owner}/{repository}/zip/{urllib.parse.quote(ref)}'
    try:
        payload = request(url, 'ownpi-skill-install')
    except urllib.error.HTTPError as exc:
        if exc.code in {401, 403}:
            code = 'github_auth_failed'
        elif exc.code == 404:
            code = 'github_not_found'
        else:
            code = 'github_download_failed'
        raise SkillInstallerError(f'GitHub archive download failed with HTTP {exc.code}.', code=code) from exc
    except OSError as exc:
        raise SkillInstallerError(f'GitHub archive download failed: {exc}', code='github_download_failed') from exc
    archive = temp_root / 'repository.zip'
    archive.write_bytes(payload)
    extract_root = temp_root / 'archive'
    extract_root.mkdir()
    try:
        with zipfile.ZipFile(archive) as zipped:
            _safe_extract_zip(zipped, extract_root)
            top_levels = {PurePosixPath(item.filename).parts[0] for item in zipped.infolist() if item.filename}
    except (OSError, zipfile.BadZipFile) as exc:
        raise SkillInstallerError(f'Invalid GitHub archive: {exc}', code='invalid_archive') from exc
    if len(top_levels) != 1:
        raise SkillInstallerError('Unexpected GitHub archive layout.', code='invalid_archive')
    return extract_root / next(iter(top_levels))


def _safe_extract_zip(zipped: zipfile.ZipFile, destination: Path) -> None:
    root = destination.resolve()
    for item in zipped.infolist():
        parts = PurePosixPath(item.filename).parts
        if not parts or PurePosixPath(item.filename).is_absolute() or '..' in parts:
            raise SkillInstallerError('Archive contains an unsafe path.', code='unsafe_archive')
        mode = item.external_attr >> 16
        if stat.S_ISLNK(mode):
            raise SkillInstallerError('Archive contains a symbolic link.', code='unsafe_archive')
        target = (destination / Path(*parts)).resolve()
        if target != root and root not in target.parents:
            raise SkillInstallerError('Archive escapes the extraction directory.', code='unsafe_archive')
    zipped.extractall(destination)


def _git_sparse_checkout(owner: str, repository: str, ref: str, path: Path, temp_root: Path) -> Path:
    repo_root = temp_root / 'repository'
    https_url = f'https://github.com/{owner}/{repository}.git'
    ssh_url = f'git@github.com:{owner}/{repository}.git'
    errors: list[str] = []
    for url in (https_url, ssh_url):
        if repo_root.exists():
            shutil.rmtree(repo_root)
        clone = _run_git(
            [
                'git',
                'clone',
                '--filter=blob:none',
                '--depth',
                '1',
                '--sparse',
                '--single-branch',
                '--branch',
                ref,
                url,
                str(repo_root),
            ]
        )
        if clone.returncode != 0:
            errors.append(_sanitized_git_error(clone.stderr))
            continue
        sparse = _run_git(['git', '-C', str(repo_root), 'sparse-checkout', 'set', path.as_posix()])
        checkout = _run_git(['git', '-C', str(repo_root), 'checkout', ref])
        if sparse.returncode == 0 and checkout.returncode == 0:
            return repo_root
        errors.append(_sanitized_git_error(sparse.stderr or checkout.stderr))
    detail = '; '.join(item for item in errors if item) or 'Git sparse checkout failed.'
    raise SkillInstallerError(detail, code='git_checkout_failed')


def _run_git(args: list[str]) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(args, text=True, capture_output=True, check=False, timeout=120)
    except (OSError, subprocess.SubprocessError) as exc:
        raise SkillInstallerError(f'Git execution failed: {exc}', code='git_unavailable') from exc


def _atomic_install(app_paths: AppPaths, source_root: Path, skill_name: str) -> Path:
    safe_name = _validated_skill_name(skill_name)
    _validate_skill_tree(source_root)
    app_paths.ensure_directories()
    destination = app_paths.skills_dir / safe_name
    if destination.exists():
        raise FileExistsError(f'Skill already exists at destination: {destination}')
    staging_root = Path(tempfile.mkdtemp(prefix=f'.{safe_name}-', dir=app_paths.skills_dir))
    destination_created = False
    staged = staging_root / safe_name
    try:
        shutil.copytree(source_root, staged, symlinks=False)
        _validate_skill_tree(staged)
        os.replace(staged, destination)
        destination_created = True
        catalog = build_skill_catalog_result([('app_home', app_paths.skills_dir)])
        visible_paths = {Path(item.path).resolve() for item in catalog.skills}
        if (destination / 'SKILL.md').resolve() not in visible_paths:
            raise SkillInstallerError('Installed skill is not visible in the catalog.', code='catalog_validation_failed')
        return destination
    except Exception:
        if destination_created and destination.exists():
            shutil.rmtree(destination)
        raise
    finally:
        shutil.rmtree(staging_root, ignore_errors=True)


def _validate_skill_tree(root: Path) -> None:
    if not root.is_dir():
        raise SkillInstallerError(f'Skill directory not found: {root}', code='missing_skill')
    if any(path.is_symlink() for path in root.rglob('*')):
        raise SkillInstallerError('Skill package contains a symbolic link.', code='unsafe_skill')
    skill_file = root / 'SKILL.md'
    if not skill_file.is_file():
        raise SkillInstallerError('Skill package is missing SKILL.md.', code='missing_skill_file')
    try:
        parsed = parse_skill_file(skill_file)
    except (OSError, UnicodeError, ValueError, yaml.YAMLError) as exc:
        raise SkillInstallerError(f'Invalid SKILL.md: {exc}', code='invalid_skill') from exc
    if not str(parsed.metadata.get('name', '')).strip() or not str(parsed.metadata.get('description', '')).strip():
        raise SkillInstallerError('SKILL.md must define name and description frontmatter.', code='invalid_frontmatter')
    for raw_target in _MARKDOWN_LINK_PATTERN.findall(parsed.raw_text):
        target = raw_target.strip().split('#', 1)[0]
        if not target or '://' in target or target.startswith(('mailto:', '#', '/')):
            continue
        resolved = (root / urllib.parse.unquote(target)).resolve()
        if root.resolve() not in resolved.parents and resolved != root.resolve():
            raise SkillInstallerError(f'Skill reference escapes package root: {raw_target}', code='unsafe_reference')
        if not resolved.exists():
            raise SkillInstallerError(f'Skill reference is missing: {raw_target}', code='missing_reference')


def _normalize_skill_root(path: Path) -> Path:
    if path.is_file():
        if path.name != 'SKILL.md':
            raise SkillInstallerError(f'Expected SKILL.md, got: {path}', code='invalid_source')
        return path.parent.resolve()
    if not path.exists():
        raise FileNotFoundError(f'Skill source not found: {path}')
    return path.resolve()


def _validated_relative_path(value: str) -> Path:
    path = Path(value)
    if path.is_absolute() or '..' in path.parts or not path.parts:
        raise SkillInstallerError('Skill path must stay inside the repository.', code='invalid_path')
    return path


def _validated_skill_name(value: str) -> str:
    name = value.strip()
    if not name or name in {'.', '..'} or Path(name).name != name or '/' in name or '\\' in name:
        raise SkillInstallerError('Skill name must be one path segment.', code='invalid_name')
    return name


def _split_repo(repo: str) -> tuple[str, str]:
    parts = [item for item in repo.strip().strip('/').split('/') if item]
    if len(parts) != 2:
        raise SkillInstallerError('GitHub repo must use owner/repo format.', code='invalid_repo')
    return parts[0], parts[1]


def _github_request(url: str, user_agent: str) -> bytes:
    headers = {'User-Agent': user_agent}
    token = os.environ.get('GITHUB_TOKEN') or os.environ.get('GH_TOKEN')
    if token:
        headers['Authorization'] = f'Bearer {token}'
    request = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(request, timeout=30) as response:
        return response.read()


def _disabled_skill_names(app_paths: AppPaths) -> set[str]:
    path = app_paths.app_home / 'skills_state.json'
    if not path.exists():
        return set()
    try:
        payload = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, json.JSONDecodeError):
        return set()
    items = payload.get('disabled_skills', [])
    return {str(item).strip().casefold() for item in items if str(item).strip()} if isinstance(items, list) else set()


def _sanitized_git_error(value: str) -> str:
    text = str(value or '').strip()
    return re.sub(r'https://[^\s/@]+@github\.com', 'https://github.com', text)
