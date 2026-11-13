from __future__ import annotations

import hashlib
import json
from typing import Any

def canonical_content_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(',', ':'),
        ensure_ascii=False,
        default=str,
    ).encode('utf-8')



def canonical_content_hash(value: Any) -> str:
    return hashlib.sha256(canonical_content_bytes(value)).hexdigest()
