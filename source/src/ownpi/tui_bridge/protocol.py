"""One versioned, request-correlated IPC protocol. stdout is exclusively JSONL."""

from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Any, TextIO

SCHEMA = "ownpi.tui.v1"
METHODS = frozenset(
    {
        "initialize",
        "submit",
        "resume",
        "inspect",
        "stop",
        "answer",
        "permission",
        "command",
        "configure",
        "new_session",
        "shutdown",
    }
)
MAX_COMMAND_BYTES = 2 * 1024 * 1024


@dataclass(frozen=True)
class Command:
    request_id: str
    method: str
    params: dict[str, Any]

    @classmethod
    def parse(cls, line: str) -> Command:
        if len(line.encode("utf-8")) > MAX_COMMAND_BYTES:
            raise ValueError("Command is too large")
        value = json.loads(line)
        if not isinstance(value, dict) or set(value) != {
            "schema",
            "request_id",
            "method",
            "params",
        }:
            raise ValueError("Expected a TUI command envelope")
        if value["schema"] != SCHEMA:
            raise ValueError(f"Unsupported TUI protocol; expected {SCHEMA}")
        if not isinstance(value["request_id"], str) or not value["request_id"].strip():
            raise ValueError("A nonempty request_id is required")
        if value["method"] not in METHODS or not isinstance(value["params"], dict):
            raise ValueError("Unsupported method or invalid params")
        return cls(value["request_id"], value["method"], value["params"])

    def fingerprint(self) -> str:
        return json.dumps(
            [self.method, self.params], sort_keys=True, ensure_ascii=False
        )


class EventWriter:
    def __init__(self, output: TextIO) -> None:
        self.output = output
        self.sequence = 0

    def emit(
        self,
        kind: str,
        data: dict[str, Any],
        *,
        request_id: str = "",
        identity: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        self.sequence += 1
        event = {
            "schema": SCHEMA,
            "seq": self.sequence,
            "type": kind,
            "request_id": request_id,
            "identity": identity or {},
            "data": data,
        }
        self.output.write(json.dumps(event, ensure_ascii=False, allow_nan=False) + "\n")
        self.output.flush()
        return event
