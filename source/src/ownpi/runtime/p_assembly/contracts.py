"""Typed contracts for canonical immutable-prefix requirements and assembly."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from enum import Enum
from typing import Any

from ownpi.controller.stages import Stage
from ownpi.schemas.workflow_plan import StageRuntimeProfile


class PAssemblyError(RuntimeError):
    """Base failure for canonical P requirement resolution and assembly."""


class PRequirementError(PAssemblyError):
    """Raised when a loop recipe or its selection dimensions are invalid."""


class PSourceResolutionError(PAssemblyError):
    """Raised when a canonical P source cannot be resolved safely."""


class PLoopKind(str, Enum):
    STAGE_CORE = "stage_core"
    REVIEWER = "reviewer"
    DIRECT_CRITIC_CORE = "direct_critic_core"
    DIRECT_CRITIC_REVIEWER = "direct_critic_reviewer"
    TASK_SPEC_AUTHORING = "task_spec_authoring"
    PLAN_MODE_STAGE_ENTRY = "plan_mode_stage_entry"
    SUBAGENT = "subagent"


class PSourceCategory(str, Enum):
    STATIC_SEMANTICS = "static_semantics"
    TASK_DEFINITION = "task_definition"
    CAPABILITY_NAVIGATION = "capability_and_knowledge_navigation"
    DURABLE_NAVIGATION = "workspace_and_durable_artifact_navigation"
    LOOP_HANDOFF = "loop_specific_handoff"
    HOOK_PREFIX = "hook_prefix"


class PRequirementMode(str, Enum):
    REQUIRED = "required"
    CONDITIONAL = "conditional"
    EXCLUDED = "excluded"


class PSourceKind(str, Enum):
    STATIC_CONTRACT = "static_contract"
    USER_INSTRUCTIONS = "user_instructions"
    RAW_USER_MESSAGE = "raw_user_message"
    TASK_SPEC = "task_spec"
    TASK_MESSAGE = "task_message"
    TASK_IMAGES = "task_images"
    SKILL_CATALOG = "skill_catalog"
    TOOL_NAVIGATION = "tool_navigation"
    MEMORY_MAP = "memory_map"
    PROJECT_WORKSPACE = "project_workspace"
    DURABLE_RESOURCES = "durable_resources"
    REVISIT_ASSIGNMENT = "revisit_assignment"
    FEEDBACK_HANDOFF = "feedback_handoff"
    REVIEW_HISTORY = "review_history"
    CORE_SUBMISSION = "core_submission"
    DIRECT_REQUEST_CONTEXT = "direct_request_context"
    DIRECT_REVIEW_EVIDENCE = "direct_review_evidence"
    REVIEW_CONTEXT = "review_context"
    SUBMITTED_ANSWER = "submitted_answer"
    HOOK_PREFIX = "hook_prefix"


class PCondition(str, Enum):
    SUBAGENT_ENABLED = "subagent_enabled"
    DEFERRED_TOOLS_PRESENT = "deferred_tools_present"
    USER_INSTRUCTIONS_PRESENT = "user_instructions_present"
    SKILL_CATALOG_PRESENT = "skill_catalog_present"
    MEMORY_MAP_PRESENT = "memory_map_present"
    TASK_IMAGES_PRESENT = "task_images_present"
    REVISIT_ASSIGNMENT_PRESENT = "revisit_assignment_present"
    FEEDBACK_HANDOFF_PRESENT = "feedback_handoff_present"
    REVIEW_CONTEXT_PRESENT = "review_context_present"
    SUBMITTED_ANSWER_PRESENT = "submitted_answer_present"
    HOOK_PREFIX_PRESENT = "hook_prefix_present"


class PContractSelector(str, Enum):
    COMMON = "common"
    ROLE = "role"
    STAGE = "stage"
    EXPLICIT = "explicit"


class PPrimaryRole(str, Enum):
    CORE = "core"
    REVIEWER = "reviewer"
    SUBAGENT = "subagent"


_CONDITION_SOURCE_KIND: dict[PCondition, PSourceKind] = {
    PCondition.SUBAGENT_ENABLED: PSourceKind.STATIC_CONTRACT,
    PCondition.DEFERRED_TOOLS_PRESENT: PSourceKind.TOOL_NAVIGATION,
    PCondition.USER_INSTRUCTIONS_PRESENT: PSourceKind.USER_INSTRUCTIONS,
    PCondition.SKILL_CATALOG_PRESENT: PSourceKind.SKILL_CATALOG,
    PCondition.MEMORY_MAP_PRESENT: PSourceKind.MEMORY_MAP,
    PCondition.TASK_IMAGES_PRESENT: PSourceKind.TASK_IMAGES,
    PCondition.REVISIT_ASSIGNMENT_PRESENT: PSourceKind.REVISIT_ASSIGNMENT,
    PCondition.FEEDBACK_HANDOFF_PRESENT: PSourceKind.FEEDBACK_HANDOFF,
    PCondition.REVIEW_CONTEXT_PRESENT: PSourceKind.REVIEW_CONTEXT,
    PCondition.SUBMITTED_ANSWER_PRESENT: PSourceKind.SUBMITTED_ANSWER,
    PCondition.HOOK_PREFIX_PRESENT: PSourceKind.HOOK_PREFIX,
}


@dataclass(frozen=True)
class PSourceRequirement:
    category: PSourceCategory
    mode: PRequirementMode
    section_id: str
    source_kind: PSourceKind | None = None
    condition: PCondition | None = None
    contract_selector: PContractSelector | None = None
    contract_id: str = ""
    contract_role: PPrimaryRole | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.category, PSourceCategory):
            raise TypeError("category must be a PSourceCategory")
        if not isinstance(self.mode, PRequirementMode):
            raise TypeError("mode must be a PRequirementMode")
        if self.mode is PRequirementMode.EXCLUDED:
            if self.source_kind is not None or self.section_id:
                raise PRequirementError(
                    "excluded requirement cannot name a source or section"
                )
            if self.condition is not None:
                raise PRequirementError("excluded requirement cannot have a condition")
            return
        if not isinstance(self.source_kind, PSourceKind):
            raise PRequirementError("active requirement must name a source kind")
        if self.condition is not None and not isinstance(self.condition, PCondition):
            raise PRequirementError("condition must be a PCondition")
        if not self.section_id.strip():
            raise PRequirementError("active requirement must name a section")
        if self.mode is PRequirementMode.CONDITIONAL and self.condition is None:
            raise PRequirementError("conditional requirement must name a condition")
        if (
            self.condition is not None
            and _CONDITION_SOURCE_KIND[self.condition] is not self.source_kind
        ):
            raise PRequirementError("condition does not match its source kind")
        if self.mode is PRequirementMode.REQUIRED and self.condition is not None:
            raise PRequirementError("required requirement cannot have a condition")
        self._validate_contract_selection()

    def _validate_contract_selection(self) -> None:
        is_contract = self.source_kind is PSourceKind.STATIC_CONTRACT
        if not is_contract:
            if (
                self.contract_selector is not None
                or self.contract_id
                or self.contract_role
            ):
                raise PRequirementError(
                    "non-contract source has contract selection metadata"
                )
            return
        if not isinstance(self.contract_selector, PContractSelector):
            raise PRequirementError("static contract selector is invalid")
        if self.contract_role is not None and not isinstance(
            self.contract_role,
            PPrimaryRole,
        ):
            raise PRequirementError("contract role must be a primary role")
        if self.contract_selector is None:
            raise PRequirementError("static contract source requires a selector")
        if (
            self.contract_selector is PContractSelector.EXPLICIT
            and not self.contract_id
        ):
            raise PRequirementError("explicit contract selector requires contract_id")
        if (
            self.contract_selector is PContractSelector.ROLE
            and self.contract_role is None
        ):
            raise PRequirementError("role contract selector requires contract_role")
        if (
            self.contract_selector is not PContractSelector.EXPLICIT
            and self.contract_id
        ):
            raise PRequirementError("contract_id is only valid for explicit selection")
        if self.contract_selector is not PContractSelector.ROLE and self.contract_role:
            raise PRequirementError("contract_role is only valid for role selection")


@dataclass(frozen=True)
class PLoopRecipe:
    loop_kind: PLoopKind
    requirements: tuple[PSourceRequirement, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.loop_kind, PLoopKind):
            raise TypeError("loop_kind must be a PLoopKind")
        if not isinstance(self.requirements, tuple):
            raise TypeError("requirements must be an immutable tuple")
        if any(not isinstance(item, PSourceRequirement) for item in self.requirements):
            raise TypeError("recipe entries must be PSourceRequirement values")
        categories = {item.category for item in self.requirements}
        if categories != set(PSourceCategory):
            raise PRequirementError("recipe must cover every P source category")
        for category in PSourceCategory:
            items = [item for item in self.requirements if item.category is category]
            excluded = [
                item for item in items if item.mode is PRequirementMode.EXCLUDED
            ]
            if excluded and len(items) != 1:
                raise PRequirementError(
                    "excluded category cannot contain active requirements"
                )


@dataclass(frozen=True)
class PAssemblyBindings:
    project_workspace_root: str = ""
    managed_venv_path: str = ""
    managed_non_python_dependency_root: str = ""
    app_home: str = ""
    current_working_directory: str = ""
    context_window_tokens: int | None = None

    @classmethod
    def from_runtime_state(
        cls,
        state: Any,
        *,
        app_home: str = "",
        current_working_directory: str = "",
        context_window_tokens: int | None = None,
    ) -> "PAssemblyBindings":
        """Build bindings from the RuntimeState-owned coordinates."""
        return cls(
            project_workspace_root=str(state.project_workspace_root),
            managed_venv_path=str(state.managed_venv_path),
            managed_non_python_dependency_root=(
                str(state.managed_non_python_dependency_root)
            ),
            app_home=app_home,
            current_working_directory=current_working_directory,
            context_window_tokens=context_window_tokens,
        )

    def __post_init__(self) -> None:
        for field_name in (
            "project_workspace_root",
            "managed_venv_path",
            "managed_non_python_dependency_root",
            "app_home",
            "current_working_directory",
        ):
            if not isinstance(getattr(self, field_name), str):
                raise TypeError(f"{field_name} must be a string")
        if self.context_window_tokens is not None and self.context_window_tokens <= 0:
            raise ValueError("context_window_tokens must be positive when provided")


@dataclass(frozen=True)
class PAssemblyRequest:
    loop_kind: PLoopKind
    level: StageRuntimeProfile | None = None
    stage: Stage | None = None
    stage_loop_index: int = 1
    bindings: PAssemblyBindings = PAssemblyBindings()

    def __post_init__(self) -> None:
        if not isinstance(self.loop_kind, PLoopKind):
            raise TypeError("loop_kind must be a PLoopKind")
        if self.level is not None and not isinstance(self.level, StageRuntimeProfile):
            raise TypeError("level must be a StageRuntimeProfile")
        if self.stage is not None and not isinstance(self.stage, Stage):
            raise TypeError("stage must be a Stage")
        if self.stage_loop_index < 1:
            raise ValueError("stage_loop_index must be positive")
        if not isinstance(self.bindings, PAssemblyBindings):
            raise TypeError("bindings must be PAssemblyBindings")


@dataclass(frozen=True)
class PSourceAtom:
    source_kind: PSourceKind
    identity: str
    content: str
    digest: str
    allowed_placeholders: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.source_kind, PSourceKind):
            raise TypeError("source_kind must be a PSourceKind")
        if not self.identity.strip() or not self.content.strip():
            raise PSourceResolutionError(
                "source identity and content must be non-empty"
            )
        actual = hashlib.sha256(self.content.encode("utf-8")).hexdigest()
        if self.digest != actual:
            raise PSourceResolutionError(f"source digest mismatch: {self.identity}")
        if not isinstance(self.allowed_placeholders, tuple):
            raise TypeError("allowed placeholders must be an immutable tuple")
        if len(set(self.allowed_placeholders)) != len(self.allowed_placeholders):
            raise PSourceResolutionError("allowed placeholders must be unique")

    @classmethod
    def create(
        cls,
        *,
        source_kind: PSourceKind,
        identity: str,
        content: str,
        allowed_placeholders: tuple[str, ...] = (),
    ) -> "PSourceAtom":
        canonical_content = str(content or "")
        return cls(
            source_kind=source_kind,
            identity=str(identity or "").strip(),
            content=canonical_content,
            digest=hashlib.sha256(canonical_content.encode("utf-8")).hexdigest(),
            allowed_placeholders=allowed_placeholders,
        )


@dataclass(frozen=True)
class PSourceProvenance:
    category: PSourceCategory
    source_kind: PSourceKind
    source_identity: str
    source_digest: str
    section_id: str
    section_digest: str
    framing_identity: str
    framing_digest: str
    binding_receipts: tuple[str, ...] = ()


@dataclass(frozen=True)
class ResolvedPSection:
    section_id: str
    category: PSourceCategory
    role: str
    content: str
    digest: str
    source_identities: tuple[str, ...]
    framing_identity: str
    framing_digest: str


@dataclass(frozen=True)
class CanonicalPMessage:
    role: str
    content: str
    section_id: str

    def to_dict(self) -> dict[str, str]:
        return {
            "role": self.role,
            "content": self.content,
        }


@dataclass(frozen=True)
class PAssemblyResult:
    messages: tuple[CanonicalPMessage, ...]
    digest: str
    resolved_sections: tuple[ResolvedPSection, ...]
    source_provenance: tuple[PSourceProvenance, ...]

    @property
    def hook_delivery_ids(self) -> tuple[str, ...]:
        return tuple(
            item.source_identity.removeprefix("hook-prefix:")
            for item in self.source_provenance
            if item.source_kind is PSourceKind.HOOK_PREFIX
        )

    def ledger_messages(self) -> tuple[dict[str, str], ...]:
        return tuple(message.to_dict() for message in self.messages)

    def to_dict(self) -> dict[str, Any]:
        return {
            "messages": [message.to_dict() for message in self.messages],
            "digest": self.digest,
            "resolved_sections": [
                {
                    "section_id": section.section_id,
                    "category": section.category.value,
                    "role": section.role,
                    "content": section.content,
                    "digest": section.digest,
                    "source_identities": list(section.source_identities),
                    "framing_identity": section.framing_identity,
                    "framing_digest": section.framing_digest,
                }
                for section in self.resolved_sections
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
                for item in self.source_provenance
            ],
            "hook_delivery_ids": list(self.hook_delivery_ids),
        }


__all__ = [
    "CanonicalPMessage",
    "PAssemblyBindings",
    "PAssemblyError",
    "PAssemblyRequest",
    "PAssemblyResult",
    "PCondition",
    "PContractSelector",
    "PLoopKind",
    "PLoopRecipe",
    "PPrimaryRole",
    "PRequirementError",
    "PRequirementMode",
    "PSourceAtom",
    "PSourceCategory",
    "PSourceKind",
    "PSourceProvenance",
    "PSourceRequirement",
    "PSourceResolutionError",
    "ResolvedPSection",
]
