"""Connection-local snapshot freshness; never a backend execution status."""

from dataclasses import dataclass
from datetime import datetime, timezone
import time
from typing import Any


@dataclass
class ViewHealth:
    state: str = "fresh"
    failures: int = 0
    first_failure: float | None = None
    last_success_at: str = ""
    error: str = ""

    def failed(self, error: Exception) -> dict[str, Any] | None:
        previous = (self.state, self.error)
        now = time.monotonic()
        self.failures += 1
        if self.first_failure is None:
            self.first_failure = now
        self.state = (
            "stale"
            if self.failures >= 3 or now - self.first_failure >= 2
            else "retrying"
        )
        self.error = str(error)
        return self.payload() if (self.state, self.error) != previous else None

    def succeeded(self) -> dict[str, Any] | None:
        recovered = self.state != "fresh"
        self.state, self.failures, self.first_failure, self.error = "fresh", 0, None, ""
        self.last_success_at = datetime.now(timezone.utc).isoformat()
        return self.payload() if recovered else None

    def payload(self) -> dict[str, Any]:
        return {
            "state": self.state,
            "consecutive_failures": self.failures,
            "last_success_at": self.last_success_at,
            "error": self.error,
        }
