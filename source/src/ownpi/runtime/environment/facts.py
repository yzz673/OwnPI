from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

from ownpi.runtime.events import now_utc_iso
from ownpi.runtime.state_models import EnvironmentFact


def record_environment_fact(
    state_store: Any,
    *,
    fact_kind: str,
    scope_identity: str,
    subject: str,
    value: bool | str | int | float | None,
    evidence_ref: str,
    validation_token: str,
) -> EnvironmentFact | None:
    environment_state = getattr(getattr(state_store, "state", None), "environment_state", None)
    if environment_state is None:
        return None
    fact = EnvironmentFact(
        fact_kind=str(fact_kind or "").strip(),
        scope_identity=str(scope_identity or "").strip(),
        subject=str(subject or "").strip(),
        value=_fact_value(value),
        evidence_ref=str(evidence_ref or "").strip(),
        observed_at=now_utc_iso(),
        validation_token=str(validation_token or "").strip(),
    )
    if not all(fact.stable_key) or not fact.evidence_ref:
        return None
    facts = [item for item in environment_state.environment_facts if item.stable_key != fact.stable_key]
    environment_state.environment_facts = [*facts, fact]
    touch = getattr(state_store, "_touch", None)
    if callable(touch):
        touch()
    return fact


def environment_fact_scope(context: Any) -> str:
    provider = str(getattr(context, "environment_provider_kind", "") or "host_local")
    descriptor = getattr(context, "environment_descriptor", None)
    env_name = str(descriptor.get("env_name", "") or "") if isinstance(descriptor, dict) else ""
    python_executable = (
        str(descriptor.get('python_executable', '') or '') if isinstance(descriptor, dict) else ''
    )
    return _scope_identity(provider=provider, env_name=env_name, python_executable=python_executable)


def path_validation_token(path: Path) -> str:
    if not path.exists():
        return 'absent'
    stat = path.stat()
    return f'present:{stat.st_mode}:{stat.st_size}:{stat.st_mtime_ns}'


def executable_validation_token(name: str, *, search_path: str | None = None) -> str:
    return str(shutil.which(name, path=search_path) or 'not_found')


def valid_environment_facts(state: Any) -> list[EnvironmentFact]:
    environment_state = getattr(state, 'environment_state', None)
    if environment_state is None:
        return []
    active_scope = _active_scope(environment_state)
    return [
        fact
        for fact in environment_state.environment_facts
        if fact.scope_identity == active_scope
        and _fact_is_current(fact)
    ]


def _active_scope(environment_state: Any) -> str:
    latest = (
        environment_state.latest_environment_receipts[-1]
        if getattr(environment_state, 'latest_environment_receipts', None)
        else {}
    )
    descriptor = latest.get('descriptor', {}) if isinstance(latest, dict) else {}
    provider = str(
        (latest.get('provider_kind') if isinstance(latest, dict) else '')
        or (descriptor.get('provider_kind') if isinstance(descriptor, dict) else '')
        or getattr(environment_state, 'default_provider', '')
        or 'host_local'
    )
    env_name = str(descriptor.get('env_name', '') or '') if isinstance(descriptor, dict) else ''
    python_executable = (
        str(descriptor.get('python_executable', '') or '') if isinstance(descriptor, dict) else ''
    )
    return _scope_identity(provider=provider, env_name=env_name, python_executable=python_executable)


def _scope_identity(*, provider: str, env_name: str, python_executable: str) -> str:
    base = f"{provider}:{env_name or 'default'}"
    return f'{base}:python={python_executable}' if python_executable else base


def _fact_is_current(fact: EnvironmentFact) -> bool:
    if fact.fact_kind == 'path_exists':
        return path_validation_token(Path(fact.subject)) == fact.validation_token
    if fact.fact_kind == 'executable_available':
        if fact.validation_token == 'not_found':
            return True
        return Path(fact.validation_token).exists()
    if fact.fact_kind == 'provider_capability':
        return True
    return True


def _fact_value(value: Any) -> bool | str | int | float | None:
    if value is None or isinstance(value, (bool, str, int, float)):
        return value
    return str(value)
