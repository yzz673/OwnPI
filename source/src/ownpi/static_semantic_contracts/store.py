"""Read-only access to canonical static semantic Markdown resources."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from importlib import resources

from ownpi.static_semantic_contracts.index import CONTRACT_PATHS


_PACKAGE = "ownpi.static_semantic_contracts"


@dataclass(frozen=True)
class StaticSemanticContract:
    contract_id: str
    resource_path: str
    content: str
    content_sha256: str


class StaticSemanticContractStore:
    """Resolve and validate canonical contract text without owning runtime state."""

    def get(self, contract_id: str) -> StaticSemanticContract:
        key = str(contract_id or "").strip()
        try:
            relative_path = CONTRACT_PATHS[key]
        except KeyError as exc:
            raise KeyError(f"Unknown static semantic contract: {key}") from exc
        node = resources.files(_PACKAGE).joinpath(*relative_path.split("/"))
        if not node.is_file():
            raise FileNotFoundError(
                f"Static semantic contract is missing: {relative_path}"
            )
        raw = node.read_bytes()
        if not raw:
            raise ValueError(f"Static semantic contract is empty: {relative_path}")
        try:
            content = raw.decode("utf-8").strip()
        except UnicodeDecodeError as exc:
            raise ValueError(
                f"Static semantic contract is not UTF-8: {relative_path}"
            ) from exc
        if not content:
            raise ValueError(
                f"Static semantic contract is whitespace-only: {relative_path}"
            )
        return StaticSemanticContract(
            contract_id=key,
            resource_path=relative_path,
            content=content,
            content_sha256=sha256(content.encode("utf-8")).hexdigest(),
        )

    def list_ids(self) -> tuple[str, ...]:
        return tuple(sorted(CONTRACT_PATHS))


_DEFAULT_STORE = StaticSemanticContractStore()


def get_static_semantic_contract(contract_id: str) -> StaticSemanticContract:
    return _DEFAULT_STORE.get(contract_id)


def list_static_semantic_contract_ids() -> tuple[str, ...]:
    return _DEFAULT_STORE.list_ids()


__all__ = [
    "StaticSemanticContract",
    "StaticSemanticContractStore",
    "get_static_semantic_contract",
    "list_static_semantic_contract_ids",
]
