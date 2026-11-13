from __future__ import annotations

from pathlib import Path
from typing import Any

from ownpi.app.paths import AppPaths
from ownpi.runtime.memory.external.release import (
    create_fresh_release_pin,
    validate_full_delivery,
)


def default_external_knowledge_install_root(app_paths: AppPaths) -> Path:
    return app_paths.app_home / 'external_knowledge' / 'delivery'


def inspect_external_knowledge_installation(
    delivery_root: Path,
    *,
    full: bool = False,
) -> dict[str, Any]:
    root = delivery_root.expanduser().resolve(strict=False)
    validation = validate_full_delivery(root) if full else create_fresh_release_pin(root)
    pin = validation.pin
    return {
        'mode': 'external_knowledge_delivery_inspect',
        'delivery_root': str(root),
        'validation_mode': validation.mode.value,
        'status': validation.status.value,
        'ready': pin is not None,
        'release_id': pin.release_id if pin is not None else '',
        'snapshot_sha256': pin.snapshot_sha256 if pin is not None else '',
        'release_content_digest': pin.release_content_digest if pin is not None else '',
        'catalog_sha256': pin.catalog_sha256 if pin is not None else '',
        'sources': [item.to_dict() for item in pin.sources] if pin is not None else [],
        'accelerators': [item.to_dict() for item in pin.accelerators] if pin is not None else [],
        'diagnostics': [
            {'code': item.code, 'message': item.message} for item in validation.diagnostics
        ],
    }
