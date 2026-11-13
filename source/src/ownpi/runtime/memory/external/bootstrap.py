from __future__ import annotations

from pathlib import Path
from typing import Any

from ownpi.runtime.memory.external.release import create_fresh_release_pin
from ownpi.runtime.state_models import RuntimeState


def initialize_external_knowledge_for_new_run(
    *,
    state: RuntimeState,
    config: Any,
) -> None:
    """Resolve CURRENT once for a fresh run and retain only its immutable pin."""
    memory_state = state.memory_state
    if not bool(getattr(config, 'enabled', False)):
        memory_state.external_knowledge_status = 'unavailable'
        memory_state.external_knowledge_diagnostic = 'disabled'
        return
    raw_root = str(getattr(config, 'delivery_root', '') or '').strip()
    root = Path(raw_root).expanduser() if raw_root else None
    if root is None or not root.is_absolute():
        memory_state.external_knowledge_status = 'unavailable'
        memory_state.external_knowledge_diagnostic = 'missing_or_relative_delivery_root'
        return
    validation = create_fresh_release_pin(root)
    memory_state.external_knowledge_pin = validation.pin
    memory_state.external_knowledge_status = validation.status.value
    memory_state.external_knowledge_diagnostic = ','.join(
        diagnostic.code for diagnostic in validation.diagnostics
    )[:240]
