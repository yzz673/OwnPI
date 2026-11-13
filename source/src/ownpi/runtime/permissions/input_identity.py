"""Keep approval fingerprints binding secrets without publishing guessing oracles."""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
from typing import Any, Mapping

_PROCESS_KEY = secrets.token_bytes(32)


def permission_input_hash(payload: Mapping[str, Any]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(',', ':'), default=str).encode('utf-8')
    inputs = payload.get('input', payload)
    if isinstance(inputs, Mapping) and inputs.get('env_overrides'):
        # Such requests are never reusable across sessions/processes. An
        # ephemeral HMAC still distinguishes live approvals without storing a
        # public, unsalted fingerprint of low-entropy environment secrets.
        return hmac.new(_PROCESS_KEY, encoded, hashlib.sha256).hexdigest()
    return hashlib.sha256(encoded).hexdigest()
