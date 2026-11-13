"""Deterministic assembly of canonical immutable provider prefixes."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

from ownpi.runtime.p_assembly.contracts import (
    CanonicalPMessage,
    PAssemblyRequest,
    PAssemblyResult,
    PRequirementMode,
    PSourceAtom,
    PSourceProvenance,
    PSourceRequirement,
    PSourceResolutionError,
    ResolvedPSection,
)
from ownpi.runtime.p_assembly.framing import get_section_framing
from ownpi.runtime.p_assembly.requirements import resolve_p_recipe
from ownpi.runtime.p_assembly.source_access import PSourceAccess
from ownpi.runtime.dependency_paths import canonical_managed_dependency_path
from ownpi.runtime.project_workspace import canonical_project_workspace_root


_SECTION_ORDER = {
    "role_contract": 10,
    "raw_user_message": 20,
    "task_spec": 21,
    "task_message": 15,
    "direct_current_request": 20,
    "direct_request_context": 21,
    "direct_core_submission": 40,
    "review_context": 41,
    "submitted_answer": 40,
    "direct_review_evidence": 42,
    "task_images": 23,
    "current_stage_contract": 30,
    "active_revisit_assignment": 40,
    "authoritative_feedback_handoff": 41,
    "current_loop_review_history": 42,
    "subagent_usage_guide": 45,
    "operating_instructions": 50,
    "feedback_authoring_contract": 51,
    "skill_catalog": 60,
    "tool_navigation": 61,
    "memory_map": 62,
    "authoritative_project_workspace": 70,
    "hook_prefix_context": 80,
}


@dataclass(frozen=True)
class _BoundAtom:
    atom: PSourceAtom
    content: str
    binding_receipts: tuple[str, ...]


class PAssembler:
    """Assemble P exclusively from a canonical recipe and source access owner."""

    def __init__(self, source_access: PSourceAccess) -> None:
        self._source_access = source_access

    def assemble(self, request: PAssemblyRequest) -> PAssemblyResult:
        recipe = resolve_p_recipe(request)
        grouped: dict[str, list[tuple[PSourceRequirement, _BoundAtom]]] = {}
        section_order: list[str] = []
        seen_sources: set[str] = set()
        for requirement in recipe.requirements:
            if requirement.mode is PRequirementMode.EXCLUDED:
                continue
            atoms = self._source_access.resolve(requirement, request)
            if not atoms:
                if requirement.mode is PRequirementMode.REQUIRED:
                    raise PSourceResolutionError(
                        f"required P source is unavailable: {requirement.section_id}"
                    )
                continue
            if requirement.section_id not in grouped:
                grouped[requirement.section_id] = []
                section_order.append(requirement.section_id)
            for atom in atoms:
                if atom.identity in seen_sources:
                    raise PSourceResolutionError(
                        f"duplicate P source identity: {atom.identity}"
                    )
                seen_sources.add(atom.identity)
                grouped[requirement.section_id].append(
                    (requirement, self._bind_atom(atom, request))
                )

        section_order.sort(
            key=_section_sort_key
        )

        sections: list[ResolvedPSection] = []
        messages: list[CanonicalPMessage] = []
        provenance: list[PSourceProvenance] = []
        for section_id in section_order:
            entries = grouped[section_id]
            categories = {requirement.category for requirement, _ in entries}
            if len(categories) != 1:
                raise PSourceResolutionError(
                    f"P section mixes source categories: {section_id}"
                )
            category = next(iter(categories))
            framing = get_section_framing(section_id)
            source_body = "\n\n".join(bound.content for _, bound in entries)
            rendered = framing.render(source_body)
            section_digest = _sha256(rendered)
            source_identities = tuple(bound.atom.identity for _, bound in entries)
            sections.append(
                ResolvedPSection(
                    section_id=section_id,
                    category=category,
                    role=framing.role,
                    content=rendered,
                    digest=section_digest,
                    source_identities=source_identities,
                    framing_identity=framing.identity,
                    framing_digest=framing.digest,
                )
            )
            messages.append(
                CanonicalPMessage(
                    role=framing.role,
                    content=rendered,
                    section_id=section_id,
                )
            )
            for requirement, bound in entries:
                provenance.append(
                    PSourceProvenance(
                        category=requirement.category,
                        source_kind=bound.atom.source_kind,
                        source_identity=bound.atom.identity,
                        source_digest=bound.atom.digest,
                        section_id=section_id,
                        section_digest=section_digest,
                        framing_identity=framing.identity,
                        framing_digest=framing.digest,
                        binding_receipts=bound.binding_receipts,
                    )
                )

        digest = _digest_result(messages, provenance)
        return PAssemblyResult(
            messages=tuple(messages),
            digest=digest,
            resolved_sections=tuple(sections),
            source_provenance=tuple(provenance),
        )

    def _bind_atom(
        self,
        atom: PSourceAtom,
        request: PAssemblyRequest,
    ) -> _BoundAtom:
        content = atom.content
        receipts: list[str] = []
        path_bindings = {
            "<workspace>": (
                request.bindings.project_workspace_root,
                canonical_project_workspace_root,
                "P workspace binding",
            ),
            "<managed_venv>": (
                request.bindings.managed_venv_path,
                canonical_managed_dependency_path,
                "P managed venv binding",
            ),
            "<managed_non_python_dependency_root>": (
                request.bindings.managed_non_python_dependency_root,
                canonical_managed_dependency_path,
                "P managed non-Python dependency root binding",
            ),
        }
        for placeholder in atom.allowed_placeholders:
            binding = path_bindings.get(placeholder)
            if binding is None:
                raise PSourceResolutionError(
                    f"unsupported P placeholder {placeholder}: {atom.identity}"
                )
            if placeholder not in content:
                continue
            raw_value, canonicalizer, label = binding
            if not raw_value.strip():
                raise PSourceResolutionError(
                    f"{placeholder} binding is missing: {atom.identity}"
                )
            canonical = canonicalizer(raw_value, label=label)
            content = content.replace(placeholder, canonical)
            receipts.append(f"{placeholder}={canonical}")
        for placeholder in atom.allowed_placeholders:
            if placeholder in content:
                raise PSourceResolutionError(
                    f"unresolved P placeholder {placeholder}: {atom.identity}"
                )
        return _BoundAtom(
            atom=atom,
            content=content,
            binding_receipts=tuple(receipts),
        )


def _digest_result(
    messages: list[CanonicalPMessage],
    provenance: list[PSourceProvenance],
) -> str:
    payload = {
        "messages": [
            {
                "role": message.role,
                "content": message.content,
                "section_id": message.section_id,
            }
            for message in messages
        ],
        "source_provenance": [
            {
                "category": item.category.value,
                "source_kind": item.source_kind.value,
                "source_identity": item.source_identity,
                "source_digest": item.source_digest,
                "section_id": item.section_id,
                "section_digest": item.section_digest,
                "framing_identity": item.framing_identity,
                "framing_digest": item.framing_digest,
                "binding_receipts": list(item.binding_receipts),
            }
            for item in provenance
        ],
    }
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return _sha256(encoded)


def _section_sort_key(section_id: str) -> int:
    return _SECTION_ORDER.get(section_id, 1000)


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


__all__ = ["PAssembler"]
