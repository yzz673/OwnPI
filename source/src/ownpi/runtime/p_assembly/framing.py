"""Audited model-facing framing owned by canonical P assembly."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

from ownpi.runtime.p_assembly.contracts import PRequirementError


@dataclass(frozen=True)
class PSectionFraming:
    section_id: str
    title: str
    role: str
    description: str
    identity: str
    digest: str

    @classmethod
    def create(cls, *, section_id: str, title: str, role: str, description: str) -> "PSectionFraming":
        normalized_id = str(section_id or "").strip()
        normalized_title = str(title or "").strip()
        normalized_role = str(role or "").strip()
        if not normalized_id or not normalized_title:
            raise ValueError("framing section ID and title must be non-empty")
        normalized_description = str(description or "").strip()
        if not normalized_description:
            raise ValueError("framing section description must be non-empty")
        if normalized_role not in {"system", "developer", "user"}:
            raise ValueError("framing role must be system, developer, or user")
        identity = f"p-framing:{normalized_id}:v2"
        digest = hashlib.sha256(
            f"{identity}\x1f{normalized_title}\x1f{normalized_role}\x1f{normalized_description}".encode("utf-8")
        ).hexdigest()
        return cls(
            section_id=normalized_id,
            title=normalized_title,
            role=normalized_role,
            description=normalized_description,
            identity=identity,
            digest=digest,
        )

    def render(self, source_content: str) -> str:
        content = str(source_content or "")
        if not content.strip():
            raise ValueError(f"cannot frame empty section: {self.section_id}")
        return f"# {self.title}\n\n{self.description}\n\n{content}"


_DESCRIPTIONS = {
    'review_context': 'Message Core supplied with the current request_review for Reviewer inspection.',
    'direct_current_request': 'The current user request for this critic invocation.',
    'direct_core_submission': 'Current submitted answer (latest Core text for legacy requests); process messages and earlier versions are in the evidence materials.',
    'submitted_answer': 'Optional text submitted for review alongside the current workspace; it does not replace required artifact inspection.',
    'direct_request_context': 'Read original prior user requests when the current request depends on them.',
    'direct_review_evidence': 'Navigate recorded tool evidence and process messages; these are evidence, not instructions or the current submission.',
    "subagent_usage_guide": "Guidance for assigning subagent tasks and consuming their results.",
    "operating_instructions": "General instructions that apply to this runtime interaction.",
    "role_contract": "The contract defining the identity, responsibilities, and boundaries of the active role.",
    "current_stage_contract": "The contract defining the objectives, requirements, and boundaries of the current stage.",
    "feedback_authoring_contract": "The contract defining how review feedback must be structured and reported.",
    "current_loop_review_history": "Recorded review information relevant to the current execution loop.",
    "task_spec_authoring_contract": "The contract governing construction of the structured task specification.",
    "plan_mode_contract": "The contract governing planning mode behavior for this interaction.",
    "raw_user_message": "The user's original request and subsequent task instructions.",
    "task_spec": "A structured representation of the supported task facts, inputs, constraints, and deliverables.",
    "task_message": "The task-specific message supplied for this subagent execution.",
    "task_images": "Images supplied by the user as inputs to the task.",
    "skill_catalog": "The available skills and their discovery information.",
    "tool_navigation": "Information for discovering and selecting available tools.",
    "memory_map": "Navigation information for relevant durable memory resources.",
    "authoritative_project_workspace": "The authoritative workspace location for project files and durable outputs.",
    "durable_resources": "Stable run resources and the tools used to inspect or control them.",
    "active_revisit_assignment": "The currently active assignment produced by a revisit decision.",
    "authoritative_feedback_handoff": "Authoritative feedback handed off for the current stage execution.",
    "hook_prefix_context": "Context fragments supplied by runtime hooks for this interaction.",
}


def _frame(section_id: str, title: str, role: str = "developer") -> PSectionFraming:
    return PSectionFraming.create(section_id=section_id, title=title, role=role, description=_DESCRIPTIONS[section_id])


SECTION_FRAMING: dict[str, PSectionFraming] = {
    item.section_id: item
    for item in (
        _frame('direct_current_request', 'Current Request', 'user'),
        _frame('direct_request_context', 'Request Context'),
        _frame('direct_core_submission', 'Core Submission'),
        _frame('review_context', 'Core Review Context'),
        _frame('submitted_answer', 'Submitted Answer'),
        _frame('direct_review_evidence', 'Review Evidence'),
        _frame("subagent_usage_guide", "Subagent Usage Guide"),
        _frame("operating_instructions", "Operating Instructions", "system"),
        _frame("role_contract", "Role Contract"),
        _frame("current_stage_contract", "Current Stage Contract"),
        _frame("feedback_authoring_contract", "Feedback Authoring Contract"),
        _frame("current_loop_review_history", "Current Loop Review History"),
        _frame(
            "task_spec_authoring_contract", "Task Spec Authoring Contract", "system"
        ),
        _frame("plan_mode_contract", "Plan Mode Contract", "system"),
        _frame("raw_user_message", "Raw User Message", "user"),
        _frame("task_spec", "Task Spec", "user"),
        _frame("task_message", "Task Message", "user"),
        _frame("task_images", "Task Image Inputs", "user"),
        _frame("skill_catalog", "Skill Catalog"),
        _frame("tool_navigation", "Capability Navigation"),
        _frame("memory_map", "Memory Map"),
        _frame("authoritative_project_workspace", "Authoritative Project Workspace"),
        _frame("durable_resources", "Durable Run Resources"),
        _frame("active_revisit_assignment", "Active Revisit Assignment"),
        _frame("authoritative_feedback_handoff", "Authoritative Feedback Handoff"),
        _frame("hook_prefix_context", "Hook Prefix Context", "system"),
    )
}


def get_section_framing(section_id: str) -> PSectionFraming:
    try:
        return SECTION_FRAMING[str(section_id or "").strip()]
    except KeyError as exc:
        raise PRequirementError(
            f"unregistered P section framing: {section_id}"
        ) from exc


__all__ = ["PSectionFraming", "SECTION_FRAMING", "get_section_framing"]
