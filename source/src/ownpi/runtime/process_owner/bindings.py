from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal


ProcessOwnerTransportKind = Literal["local_unix", "remote_ssh_unix"]


@dataclass(frozen=True)
class ProcessOwnerBinding:
    owner_id: str
    transport_kind: ProcessOwnerTransportKind
    endpoint_ref: str = ""
    resume_session_id: str = ""

    def __post_init__(self) -> None:
        if not self.owner_id.strip():
            raise ValueError("process owner binding requires owner_id")
        if self.transport_kind not in {"local_unix", "remote_ssh_unix"}:
            raise ValueError(
                f"unsupported process owner transport: {self.transport_kind}"
            )
        if self.transport_kind == "remote_ssh_unix" and not self.endpoint_ref.strip():
            raise ValueError("remote process owner binding requires endpoint_ref")
        if any(character.isspace() for character in self.owner_id):
            raise ValueError("process owner owner_id must not contain whitespace")

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ProcessOwnerBinding":
        allowed = {"owner_id", "transport_kind", "endpoint_ref", "resume_session_id"}
        unknown = sorted(set(data) - allowed)
        if unknown:
            raise ValueError(
                f"process owner binding contains unknown fields: {unknown}"
            )
        return cls(
            owner_id=str(data.get("owner_id", "") or ""),
            transport_kind=str(data.get("transport_kind", "") or ""),  # type: ignore[arg-type]
            endpoint_ref=str(data.get("endpoint_ref", "") or ""),
            resume_session_id=str(data.get("resume_session_id", "") or ""),
        )

    def to_dict(self) -> dict[str, str]:
        payload = {
            "owner_id": self.owner_id,
            "transport_kind": self.transport_kind,
            "endpoint_ref": self.endpoint_ref,
            "resume_session_id": self.resume_session_id,
        }
        return {key: value for key, value in payload.items() if value}


@dataclass
class ProcessOwnerBindingRegistry:
    bindings: dict[str, ProcessOwnerBinding] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "ProcessOwnerBindingRegistry":
        payload = data if isinstance(data, dict) else {}
        unknown = sorted(set(payload) - {"bindings"})
        if unknown:
            raise ValueError(
                f"process owner binding registry contains unknown fields: {unknown}"
            )
        raw_bindings = payload.get("bindings", {})
        if not isinstance(raw_bindings, dict):
            raise ValueError("process owner bindings must be a mapping")
        bindings: dict[str, ProcessOwnerBinding] = {}
        for owner_id, item in raw_bindings.items():
            if not isinstance(item, dict):
                raise ValueError("process owner binding entries must be objects")
            binding = ProcessOwnerBinding.from_dict(item)
            if str(owner_id) != binding.owner_id:
                raise ValueError("process owner binding key must match owner_id")
            bindings[binding.owner_id] = binding
        return cls(bindings=bindings)

    def to_dict(self) -> dict[str, Any]:
        return {
            "bindings": {
                owner_id: binding.to_dict()
                for owner_id, binding in sorted(self.bindings.items())
            }
        }

    def get(self, owner_id: str) -> ProcessOwnerBinding | None:
        return self.bindings.get(str(owner_id or ""))

    def upsert(self, binding: ProcessOwnerBinding) -> None:
        self.bindings[binding.owner_id] = binding

    def remove(self, owner_id: str) -> ProcessOwnerBinding | None:
        return self.bindings.pop(str(owner_id or ""), None)
