"""Best-effort developer diagnostics, independent of rollout and recovery."""
from __future__ import annotations

import json
import os
import threading
import traceback
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from ownpi.llm.failure_diagnostics import diagnostic_message
from ownpi.runtime.payload_redaction import redact_payload

_PATH: ContextVar[Path | None] = ContextVar('runtime_diagnostic_path', default=None)
_LOCK = threading.Lock()
_OMITTED = {'prompt', 'prompt_head', 'input', 'output', 'body', 'headers',
            'provider_payload', 'raw_response', 'exact_usage', 'usage'}


def _clean(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): _clean(v) for k, v in value.items() if k not in _OMITTED}
    if isinstance(value, list):
        return [_clean(v) for v in value[:100]]
    if isinstance(value, str):
        return diagnostic_message(value, limit=16000)
    return value


def record_diagnostic(event: dict[str, Any]) -> None:
    path = _PATH.get()
    if path is None:
        return
    try:
        payload = redact_payload(_clean(event))
        payload.setdefault('timestamp', datetime.now(timezone.utc).isoformat())
        payload['pid'] = os.getpid()
        line = json.dumps(payload, ensure_ascii=True) + '\n'
        with _LOCK:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open('a', encoding='utf-8') as stream:
                stream.write(line)
    except Exception:
        # Diagnostics must never change execution or recovery outcomes.
        pass


def record_exception(error: BaseException, **metadata: Any) -> None:
    if _PATH.get() is None:
        return
    try:
        record_diagnostic({
            **metadata,
            'event_type': 'exception',
            'error_type': type(error).__name__,
            'error_message': str(error),
            'traceback': ''.join(traceback.format_exception(error)),
        })
    except Exception:
        pass


@contextmanager
def diagnostic_session(run_dir: Path, *, operation: str) -> Iterator[None]:
    token = _PATH.set(Path(run_dir) / 'diagnostics' / 'runtime.jsonl')
    record_diagnostic({'event_type': 'diagnostic_session_started', 'operation': operation})
    try:
        yield
    except BaseException as error:
        record_exception(error, operation=operation)
        raise
    finally:
        record_diagnostic({'event_type': 'diagnostic_session_finished', 'operation': operation})
        _PATH.reset(token)
