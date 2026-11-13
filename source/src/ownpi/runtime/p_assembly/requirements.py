"""Canonical P requirements for each native provider loop family."""

from __future__ import annotations

from ownpi.schemas.workflow_plan import StageRuntimeProfile

from ownpi.runtime.p_assembly.contracts import (
    PAssemblyRequest,
    PCondition,
    PContractSelector,
    PLoopKind,
    PLoopRecipe,
    PPrimaryRole,
    PRequirementError,
    PRequirementMode,
    PSourceCategory,
    PSourceKind,
    PSourceRequirement,
)


def _required(
    category: PSourceCategory,
    source_kind: PSourceKind,
    section_id: str,
    *,
    contract_selector: PContractSelector | None = None,
    contract_id: str = "",
    contract_role: PPrimaryRole | None = None,
) -> PSourceRequirement:
    return PSourceRequirement(
        category=category,
        mode=PRequirementMode.REQUIRED,
        source_kind=source_kind,
        section_id=section_id,
        contract_selector=contract_selector,
        contract_id=contract_id,
        contract_role=contract_role,
    )


def _conditional(
    category: PSourceCategory,
    source_kind: PSourceKind,
    section_id: str,
    condition: PCondition,
) -> PSourceRequirement:
    return PSourceRequirement(
        category=category,
        mode=PRequirementMode.CONDITIONAL,
        source_kind=source_kind,
        section_id=section_id,
        condition=condition,
    )


def _excluded(category: PSourceCategory) -> PSourceRequirement:
    return PSourceRequirement(
        category=category,
        mode=PRequirementMode.EXCLUDED,
        section_id="",
    )


def _contract(
    category: PSourceCategory,
    section_id: str,
    selector: PContractSelector,
    *,
    contract_id: str = "",
    role: PPrimaryRole | None = None,
) -> PSourceRequirement:
    return _required(
        category,
        PSourceKind.STATIC_CONTRACT,
        section_id,
        contract_selector=selector,
        contract_id=contract_id,
        contract_role=role,
    )


S = PSourceCategory.STATIC_SEMANTICS
T = PSourceCategory.TASK_DEFINITION
C = PSourceCategory.CAPABILITY_NAVIGATION
D = PSourceCategory.DURABLE_NAVIGATION
H = PSourceCategory.LOOP_HANDOFF
K = PSourceCategory.HOOK_PREFIX


def _hook_prefix() -> PSourceRequirement:
    return _conditional(
        K,
        PSourceKind.HOOK_PREFIX,
        "hook_prefix_context",
        PCondition.HOOK_PREFIX_PRESENT,
    )


def _operating_instructions() -> tuple[PSourceRequirement, ...]:
    return (
        _contract(S, "operating_instructions", PContractSelector.COMMON),
        _conditional(
            S,
            PSourceKind.USER_INSTRUCTIONS,
            "operating_instructions",
            PCondition.USER_INSTRUCTIONS_PRESENT,
        ),
    )


def _primary_role_contract(role: PPrimaryRole) -> PSourceRequirement:
    return _contract(
        S,
        "role_contract",
        PContractSelector.ROLE,
        role=role,
    )


def _stage_contract() -> PSourceRequirement:
    return _contract(S, "current_stage_contract", PContractSelector.STAGE)


def _full_task_definition() -> tuple[PSourceRequirement, ...]:
    return (
        _required(T, PSourceKind.RAW_USER_MESSAGE, "raw_user_message"),
        _required(T, PSourceKind.TASK_SPEC, "task_spec"),
        _conditional(
            T,
            PSourceKind.TASK_IMAGES,
            "task_images",
            PCondition.TASK_IMAGES_PRESENT,
        ),
    )


def _subagent_recipe(*, staged: bool = True) -> PLoopRecipe:
    return PLoopRecipe(
        loop_kind=PLoopKind.SUBAGENT,
        requirements=(
            _primary_role_contract(PPrimaryRole.SUBAGENT),
            _required(T, PSourceKind.TASK_MESSAGE, "task_message"),
            *((*_full_task_definition(), _stage_contract()) if staged else ()),
            *_operating_instructions(),
            *_capability_navigation(),
            *_workspace_navigation(upstream=False),
            _excluded(H),
            _hook_prefix(),
        ),
    )


def _capability_navigation() -> tuple[PSourceRequirement, ...]:
    return (
        _conditional(
            C, PSourceKind.SKILL_CATALOG, "skill_catalog",
            PCondition.SKILL_CATALOG_PRESENT,
        ),
        _conditional(
            C, PSourceKind.TOOL_NAVIGATION, "tool_navigation",
            PCondition.DEFERRED_TOOLS_PRESENT,
        ),
        _conditional(
            C, PSourceKind.MEMORY_MAP, "memory_map",
            PCondition.MEMORY_MAP_PRESENT,
        ),
    )


def _workspace_navigation(*, upstream: bool) -> tuple[PSourceRequirement, ...]:
    requirements = [
        _required(D, PSourceKind.PROJECT_WORKSPACE, "authoritative_project_workspace")
    ]
    requirements.append(
        _required(D, PSourceKind.DURABLE_RESOURCES, "durable_resources")
    )
    if upstream:
        requirements.extend(
            (
            )
        )
    return tuple(requirements)


def _subagent_usage() -> PSourceRequirement:
    return PSourceRequirement(
        category=S,
        mode=PRequirementMode.CONDITIONAL,
        source_kind=PSourceKind.STATIC_CONTRACT,
        section_id="subagent_usage_guide",
        condition=PCondition.SUBAGENT_ENABLED,
        contract_selector=PContractSelector.EXPLICIT,
        contract_id="common.subagent_usage",
    )


def _stage_core_recipe() -> PLoopRecipe:
    return PLoopRecipe(
        loop_kind=PLoopKind.STAGE_CORE,
        requirements=(
            _subagent_usage(),
            _primary_role_contract(PPrimaryRole.CORE),
            *_full_task_definition(),
            _stage_contract(),
            _conditional(
                H,
                PSourceKind.REVISIT_ASSIGNMENT,
                "active_revisit_assignment",
                PCondition.REVISIT_ASSIGNMENT_PRESENT,
            ),
            _conditional(
                H,
                PSourceKind.FEEDBACK_HANDOFF,
                "authoritative_feedback_handoff",
                PCondition.FEEDBACK_HANDOFF_PRESENT,
            ),
            *_operating_instructions(),
            *_capability_navigation(),
            *_workspace_navigation(upstream=True),
            _hook_prefix(),
        ),
    )


def _critic_recipe(loop_kind: PLoopKind, role: PPrimaryRole) -> PLoopRecipe:
    return PLoopRecipe(
        loop_kind=loop_kind,
        requirements=(
            _subagent_usage(),
            _primary_role_contract(role),
            *_full_task_definition(),
            _stage_contract(),
            _conditional(H, PSourceKind.REVIEW_CONTEXT, 'review_context',
                         PCondition.REVIEW_CONTEXT_PRESENT),
            _conditional(H, PSourceKind.SUBMITTED_ANSWER, 'submitted_answer',
                         PCondition.SUBMITTED_ANSWER_PRESENT),
            _required(
                H, PSourceKind.REVIEW_HISTORY, "current_loop_review_history"
            ),
            *_operating_instructions(),
            _contract(S, "feedback_authoring_contract", PContractSelector.EXPLICIT,
                      contract_id="authoring.feedback"),
            *_capability_navigation(),
            *_workspace_navigation(upstream=False),
            _hook_prefix(),
        ),
    )


def _task_spec_authoring_recipe() -> PLoopRecipe:
    return PLoopRecipe(
        loop_kind=PLoopKind.TASK_SPEC_AUTHORING,
        requirements=(
            _contract(
                S,
                "task_spec_authoring_contract",
                PContractSelector.EXPLICIT,
                contract_id="authoring.task_spec",
            ),
            _required(T, PSourceKind.RAW_USER_MESSAGE, "raw_user_message"),
            _excluded(C),
            _excluded(D),
            _excluded(H),
            _hook_prefix(),
        ),
    )


def _plan_mode_recipe() -> PLoopRecipe:
    return PLoopRecipe(
        loop_kind=PLoopKind.PLAN_MODE_STAGE_ENTRY,
        requirements=(
            _contract(
                S,
                "plan_mode_contract",
                PContractSelector.EXPLICIT,
                contract_id="runtime_mode.plan_mode",
            ),
            _conditional(
                S,
                PSourceKind.USER_INSTRUCTIONS,
                "operating_instructions",
                PCondition.USER_INSTRUCTIONS_PRESENT,
            ),
            _stage_contract(),
            *_full_task_definition(),
            *_capability_navigation(),
            *_workspace_navigation(upstream=True),
            _excluded(H),
            _hook_prefix(),
        ),
    )


def _direct_critic_recipe(*, reviewer: bool) -> PLoopRecipe:
    return PLoopRecipe(
        loop_kind=PLoopKind.DIRECT_CRITIC_REVIEWER if reviewer else PLoopKind.DIRECT_CRITIC_CORE,
        requirements=(
            _subagent_usage(),
            _primary_role_contract(PPrimaryRole.REVIEWER if reviewer else PPrimaryRole.CORE),
            *(
                (_required(T, PSourceKind.TASK_MESSAGE, 'direct_current_request'),
                 _required(H, PSourceKind.DIRECT_REQUEST_CONTEXT, 'direct_request_context'),
                 _required(H, PSourceKind.CORE_SUBMISSION, 'direct_core_submission'),
                 _conditional(H, PSourceKind.REVIEW_CONTEXT, 'review_context',
                              PCondition.REVIEW_CONTEXT_PRESENT),
                 _required(H, PSourceKind.DIRECT_REVIEW_EVIDENCE, 'direct_review_evidence'),
                 _required(H, PSourceKind.REVIEW_HISTORY, 'current_loop_review_history'),
                 _contract(S, 'feedback_authoring_contract', PContractSelector.EXPLICIT,
                           contract_id='authoring.feedback'))
                if reviewer else
                (_excluded(T), _excluded(H))
            ),
            *_operating_instructions(),
            *_capability_navigation(),
            *_workspace_navigation(upstream=False),
            _hook_prefix(),
        ),
    )


RECIPES: dict[PLoopKind, PLoopRecipe] = {
    PLoopKind.DIRECT_CRITIC_CORE: _direct_critic_recipe(reviewer=False),
    PLoopKind.DIRECT_CRITIC_REVIEWER: _direct_critic_recipe(reviewer=True),
    PLoopKind.STAGE_CORE: _stage_core_recipe(),
    PLoopKind.REVIEWER: _critic_recipe(PLoopKind.REVIEWER, PPrimaryRole.REVIEWER),
    PLoopKind.TASK_SPEC_AUTHORING: _task_spec_authoring_recipe(),
    PLoopKind.PLAN_MODE_STAGE_ENTRY: _plan_mode_recipe(),
    PLoopKind.SUBAGENT: _subagent_recipe(),
}


_STAGE_BOUND_LOOPS = {
    PLoopKind.STAGE_CORE,
    PLoopKind.REVIEWER,
    PLoopKind.PLAN_MODE_STAGE_ENTRY,
    PLoopKind.SUBAGENT,
}


def resolve_p_recipe(request: PAssemblyRequest) -> PLoopRecipe:
    """Resolve one canonical loop recipe and validate its selection dimensions."""
    try:
        recipe = RECIPES[request.loop_kind]
    except KeyError as exc:
        raise PRequirementError(
            f"unsupported P loop kind: {request.loop_kind}"
        ) from exc
    if request.loop_kind in _STAGE_BOUND_LOOPS:
        if request.level is None or request.stage is None:
            raise PRequirementError(
                f"{request.loop_kind.value} requires level and stage bindings"
            )
    elif request.loop_kind in {PLoopKind.DIRECT_CRITIC_CORE, PLoopKind.DIRECT_CRITIC_REVIEWER}:
        if request.level is not StageRuntimeProfile.TASK_LEVEL or request.stage is not None:
            raise PRequirementError('direct critic requires task-level and no stage contract')
    elif request.level is not None or request.stage is not None:
        raise PRequirementError(
            f"{request.loop_kind.value} does not accept level or stage bindings"
        )
    if (request.loop_kind is PLoopKind.SUBAGENT
            and request.level is StageRuntimeProfile.TASK_LEVEL
            and request.stage.value == 'direct_qa'):
        return _subagent_recipe(staged=False)
    return recipe


__all__ = ["RECIPES", "resolve_p_recipe"]
