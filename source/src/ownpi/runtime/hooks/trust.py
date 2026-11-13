from __future__ import annotations

from dataclasses import dataclass, field

from ownpi.runtime.hooks.types import HookTrustStatus


@dataclass(frozen=True)
class HookTrustStore:
    approved_hashes: dict[str, str] = field(default_factory=dict)
    managed_sources: frozenset[str] = frozenset({"builtin", "managed", "settings"})

    def evaluate(
        self,
        *,
        trust_key: str,
        content_hash: str,
        source: str,
    ) -> HookTrustStatus:
        if source in self.managed_sources or source.startswith("plugin:app_home:"):
            return "managed"
        approved = str(self.approved_hashes.get(trust_key, "") or "").strip()
        if not approved:
            return "untrusted"
        if approved == content_hash:
            return "trusted"
        return "modified"

    def approve(self, *, trust_key: str, content_hash: str) -> "HookTrustStore":
        updated = dict(self.approved_hashes)
        updated[trust_key] = content_hash
        return HookTrustStore(
            approved_hashes=updated,
            managed_sources=self.managed_sources,
        )
