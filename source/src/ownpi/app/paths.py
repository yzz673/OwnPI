from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path

_APP_HOME_ENV = 'OWNPI_HOME'
_MANAGED_HOME_ENV = 'OWNPI_MANAGED_HOME'
_DEFAULT_APP_DIRNAME = '.ownpi'


def _discover_repo_root() -> Path | None:
    current = Path(__file__).resolve()
    for parent in current.parents:
        if (parent / 'pyproject.toml').exists() and (parent / 'src' / 'ownpi').exists():
            return parent
    return None


@dataclass(frozen=True)
class AppPaths:
    app_home: Path
    managed_home: Path
    config_path: Path
    env_path: Path
    env_example_path: Path
    tasks_dir: Path
    skills_dir: Path
    memory_root: Path
    stage_archetype_root: Path
    stage_archetype_official_dir: Path
    stage_template_root: Path
    stage_template_official_dir: Path
    stage_template_candidates_dir: Path
    stage_template_promotions_dir: Path
    workflow_template_root: Path
    workflow_template_official_dir: Path
    workflow_template_candidates_dir: Path
    workflow_template_promotions_dir: Path
    # Development/source checkout root. Product installs should not require this.
    repo_root: Path | None = None

    @property
    def source_repo_root(self) -> Path | None:
        return self.repo_root

    @property
    def plans_dir(self) -> Path:
        return self.app_home / 'plans'

    @property
    def internal_memory_root(self) -> Path:
        return self.memory_root / 'internal'

    @property
    def managed_venv_path(self) -> Path:
        return self.app_home / 'env' / 'ownpi'

    @property
    def managed_non_python_dependency_root(self) -> Path:
        return self.app_home / 'tools'

    @classmethod
    def discover(
        cls,
        *,
        app_home: Path | None = None,
        repo_root: Path | None = None,
    ) -> 'AppPaths':
        env_value = os.environ.get(_APP_HOME_ENV)
        resolved_home = Path(env_value).expanduser() if env_value else (app_home or Path.home() / _DEFAULT_APP_DIRNAME)
        resolved_home = resolved_home.resolve()
        managed_env_value = os.environ.get(_MANAGED_HOME_ENV)
        resolved_managed_home = (
            Path(managed_env_value).expanduser().resolve()
            if managed_env_value
            else _default_managed_home()
        )
        resolved_repo_root = repo_root if repo_root is not None else _discover_repo_root()
        stage_archetype_root = resolved_home / 'stage_archetypes'
        stage_template_root = resolved_home / 'stage_templates'
        workflow_template_root = resolved_home / 'workflow_templates'
        return cls(
            app_home=resolved_home,
            managed_home=resolved_managed_home,
            config_path=resolved_home / 'config.yaml',
            env_path=resolved_home / '.env',
            env_example_path=resolved_home / '.env.example',
            tasks_dir=resolved_home / 'tasks',
            skills_dir=resolved_home / 'skills',
            memory_root=resolved_home / 'memory',
            stage_archetype_root=stage_archetype_root,
            stage_archetype_official_dir=stage_archetype_root / 'official',
            stage_template_root=stage_template_root,
            stage_template_official_dir=stage_template_root / 'official',
            stage_template_candidates_dir=stage_template_root / 'candidates',
            stage_template_promotions_dir=stage_template_root / 'promotions',
            workflow_template_root=workflow_template_root,
            workflow_template_official_dir=workflow_template_root / 'official',
            workflow_template_candidates_dir=workflow_template_root / 'candidates',
            workflow_template_promotions_dir=workflow_template_root / 'promotions',
            repo_root=resolved_repo_root,
        )

    def ensure_directories(self) -> None:
        self.app_home.mkdir(parents=True, exist_ok=True)
        self.tasks_dir.mkdir(parents=True, exist_ok=True)
        self.plans_dir.mkdir(parents=True, exist_ok=True)
        self.skills_dir.mkdir(parents=True, exist_ok=True)
        self.internal_memory_root.mkdir(parents=True, exist_ok=True)
        self.stage_archetype_official_dir.mkdir(parents=True, exist_ok=True)
        self.stage_template_official_dir.mkdir(parents=True, exist_ok=True)
        self.stage_template_candidates_dir.mkdir(parents=True, exist_ok=True)
        self.stage_template_promotions_dir.mkdir(parents=True, exist_ok=True)
        self.workflow_template_official_dir.mkdir(parents=True, exist_ok=True)
        self.workflow_template_candidates_dir.mkdir(parents=True, exist_ok=True)
        self.workflow_template_promotions_dir.mkdir(parents=True, exist_ok=True)

    def iter_skill_roots(self) -> list[Path]:
        return [path for _, path in self.get_skill_roots()]

    def get_skill_roots(self) -> list[tuple[str, Path]]:
        roots: list[tuple[str, Path]] = [('app_home', self.skills_dir)]
        if self.source_repo_root is not None:
            repo_skills = self.source_repo_root / 'skills'
            if repo_skills.exists():
                roots.append(('repo', repo_skills))
        return roots

    def get_extension_roots(self, *, project_root: Path | None = None) -> list[tuple[str, Path]]:
        roots: list[tuple[str, Path]] = [('app_home', self.app_home)]
        if project_root is not None:
            project_extension_root = Path(project_root).expanduser().resolve() / '.ownpi'
            if project_extension_root.exists():
                roots.append(('project', project_extension_root))
        return roots

def _default_managed_home() -> Path:
    if sys.platform == 'darwin':
        return Path('/Library/Application Support/OwnPI')
    if sys.platform.startswith('win'):
        return Path('C:/Program Files/OwnPI')
    return Path('/etc/ownpi')
