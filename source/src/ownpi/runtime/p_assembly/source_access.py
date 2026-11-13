"""Typed access to canonical sources consumed by immutable-prefix assembly."""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

from ownpi.runtime.hooks.context_delivery import is_eligible_hook_prefix
from ownpi.runtime.instructions.resolver import discover_instructions
from ownpi.runtime.memory.map_contracts import render_memory_map
from ownpi.runtime.p_assembly.contracts import (
    PAssemblyRequest,
    PCondition,
    PContractSelector,
    PLoopKind,
    PSourceAtom,
    PSourceKind,
    PSourceRequirement,
    PSourceResolutionError,
)
from ownpi.runtime.p_assembly.source_inputs import PSourceInputs
from ownpi.runtime.p_assembly.source_projections import (
    project_feedback_handoff,
    project_revisit_assignment,
    project_review_history,
    project_task_images,
)
from ownpi.runtime.project_workspace import canonical_project_workspace_root
from ownpi.runtime.task_context import RuntimeTaskContext
from ownpi.runtime.tools.navigation import render_tool_navigation
from ownpi.schemas.workflow_plan import StageRuntimeProfile
from ownpi.skills.prompt_surface import build_skill_catalog_instruction
from ownpi.static_semantic_contracts.index import COMMON_CONTRACT_IDS
from ownpi.static_semantic_contracts.store import StaticSemanticContractStore
from ownpi.static_semantic_contracts.stage_contracts import resolve_stage_contract
from ownpi.static_semantic_contracts.taxonomy import (
    role_contract_id,
    stage_contract_id,
)


class PSourceAccess(Protocol):
    """Resolve source atoms without owning their canonical content."""

    def resolve(
        self,
        requirement: PSourceRequirement,
        request: PAssemblyRequest,
    ) -> tuple[PSourceAtom, ...]: ...


class CanonicalPSourceAccess:
    """Adapt existing source owners to canonical P source atoms."""

    def __init__(
        self,
        inputs: PSourceInputs,
        *,
        contract_store: StaticSemanticContractStore | None = None,
    ) -> None:
        if not isinstance(inputs, PSourceInputs):
            raise TypeError("inputs must be PSourceInputs")
        self._inputs = inputs
        self._contract_store = contract_store or StaticSemanticContractStore()

    def resolve(
        self,
        requirement: PSourceRequirement,
        request: PAssemblyRequest,
    ) -> tuple[PSourceAtom, ...]:
        if requirement.source_kind is None:
            return ()
        resolver = {
            PSourceKind.STATIC_CONTRACT: self._static_contracts,
            PSourceKind.USER_INSTRUCTIONS: self._user_instructions,
            PSourceKind.RAW_USER_MESSAGE: self._raw_user_message,
            PSourceKind.TASK_SPEC: self._task_spec,
            PSourceKind.TASK_MESSAGE: self._task_message,
            PSourceKind.TASK_IMAGES: self._task_images,
            PSourceKind.SKILL_CATALOG: self._skill_catalog,
            PSourceKind.TOOL_NAVIGATION: self._tool_navigation,
            PSourceKind.MEMORY_MAP: self._memory_map,
            PSourceKind.PROJECT_WORKSPACE: self._project_workspace,
            PSourceKind.DURABLE_RESOURCES: self._durable_resources,
            PSourceKind.REVISIT_ASSIGNMENT: self._revisit_assignment,
            PSourceKind.FEEDBACK_HANDOFF: self._feedback_handoff,
            PSourceKind.REVIEW_HISTORY: self._review_history,
            PSourceKind.CORE_SUBMISSION: self._core_submission,
            PSourceKind.DIRECT_REQUEST_CONTEXT: self._direct_review_navigation,
            PSourceKind.DIRECT_REVIEW_EVIDENCE: self._direct_review_navigation,
            PSourceKind.REVIEW_CONTEXT: self._review_context,
            PSourceKind.SUBMITTED_ANSWER: self._direct_review_navigation,
            PSourceKind.HOOK_PREFIX: self._hook_prefix,
        }[requirement.source_kind]
        atoms = resolver(requirement, request)
        for atom in atoms:
            if atom.source_kind is not requirement.source_kind:
                raise PSourceResolutionError(
                    f"source adapter returned wrong kind for {requirement.section_id}"
                )
        return atoms

    def _static_contracts(
        self,
        requirement: PSourceRequirement,
        request: PAssemblyRequest,
    ) -> tuple[PSourceAtom, ...]:
        selector = requirement.contract_selector
        if requirement.condition is PCondition.SUBAGENT_ENABLED and not self._inputs.subagent_enabled:
            return ()
        if selector is PContractSelector.COMMON:
            contract_ids = COMMON_CONTRACT_IDS
        elif selector is PContractSelector.ROLE:
            if request.level is None or requirement.contract_role is None:
                raise PSourceResolutionError(
                    "role contract resolution requires level and role"
                )
            contract_ids = (
                role_contract_id(requirement.contract_role.value, request.level.value),
            )
        elif selector is PContractSelector.STAGE:
            if request.level is None or request.stage is None:
                raise PSourceResolutionError(
                    "stage contract resolution requires level and stage"
                )
            bundle = resolve_stage_contract(request.stage, request.level)
            try:
                loop = bundle.loop_contracts[request.stage_loop_index - 1]
            except IndexError as exc:
                raise PSourceResolutionError(
                    f"stage loop index is out of range: {request.stage_loop_index}"
                ) from exc
            feedback = self._inputs.feedback_state
            if request.loop_kind is PLoopKind.REVIEWER and feedback is not None:
                target = feedback.critic_loop.review_target
                if target is not None and (
                    target.stage != request.stage.value
                    or target.loop_index != loop.loop_index
                    or target.contract_sha256 != loop.contract_sha256
                ):
                    raise PSourceResolutionError(
                        "current stage contract differs from the submitted workspace review target"
                    )
            return (PSourceAtom.create(
                source_kind=PSourceKind.STATIC_CONTRACT,
                identity=f"contract:{stage_contract_id(request.stage.value, request.level.value)}:loop-{loop.loop_index:03d}",
                content=loop.contract,
                allowed_placeholders=("<workspace>",),
            ),)
        elif selector is PContractSelector.EXPLICIT:
            contract_ids = (requirement.contract_id,)
        else:
            raise PSourceResolutionError("unknown static contract selector")
        atoms: list[PSourceAtom] = []
        for contract_id in contract_ids:
            contract = self._contract_store.get(contract_id)
            if contract.contract_id == "common.dependency_handling":
                placeholders = (
                    "<managed_venv>",
                    "<managed_non_python_dependency_root>",
                )
            elif selector is PContractSelector.STAGE:
                placeholders = ("<workspace>",)
            else:
                placeholders = ()
            atoms.append(
                PSourceAtom(
                    source_kind=PSourceKind.STATIC_CONTRACT,
                    identity=f"contract:{contract.contract_id}",
                    content=contract.content,
                    digest=contract.content_sha256,
                    allowed_placeholders=placeholders,
                )
            )
        if requirement.condition is PCondition.SUBAGENT_ENABLED:
            limit = self._inputs.subagent_max_count
            if type(limit) is not int or limit < 1:
                raise PSourceResolutionError("subagent_max_count must be a positive integer")
            atoms.append(PSourceAtom.create(
                source_kind=PSourceKind.STATIC_CONTRACT,
                identity="runtime-config:subagent_max_count",
                content=f"Current batch limit: runtime.subagent_max_count = {limit}.",
            ))
        return tuple(atoms)

    def _review_history(
        self,
        requirement: PSourceRequirement,
        request: PAssemblyRequest,
    ) -> tuple[PSourceAtom, ...]:
        del requirement
        return project_review_history(
            self._inputs.feedback_state,
            stage=(request.stage.value if request.stage is not None else
                   'direct_qa' if request.loop_kind is PLoopKind.DIRECT_CRITIC_REVIEWER else None),
            loop_index=request.stage_loop_index,
            run_dir=self._run_dir(),
        )

    def _user_instructions(
        self,
        requirement: PSourceRequirement,
        request: PAssemblyRequest,
    ) -> tuple[PSourceAtom, ...]:
        del requirement
        resolved = discover_instructions(
            project_root=_optional_path(request.bindings.project_workspace_root),
            app_home=_optional_path(request.bindings.app_home),
            current_working_directory=_optional_path(
                request.bindings.current_working_directory
            ),
        )
        return tuple(
            PSourceAtom(
                source_kind=PSourceKind.USER_INSTRUCTIONS,
                identity=f"instruction:{item.locator}",
                content=item.content,
                digest=item.digest,
            )
            for item in resolved
            if item.source_family != "runtime_builtin"
        )

    def _raw_user_message(
        self,
        requirement: PSourceRequirement,
        request: PAssemblyRequest,
    ) -> tuple[PSourceAtom, ...]:
        del requirement, request
        context = self._require_task_context()
        content = context.read_raw_user_message()
        ref = context.raw_user_message_ref
        return (
            PSourceAtom(
                source_kind=PSourceKind.RAW_USER_MESSAGE,
                identity=f"raw-user-message:{ref.path}",
                content=content,
                digest=ref.content_sha256,
            ),
        )

    def _task_spec(
        self,
        requirement: PSourceRequirement,
        request: PAssemblyRequest,
    ) -> tuple[PSourceAtom, ...]:
        del requirement, request
        context = self._inputs.task_context
        if context is None or context.task_spec_ref is None:
            return ()
        content = context.read_task_spec()
        ref = context.task_spec_ref
        return (
            PSourceAtom(
                source_kind=PSourceKind.TASK_SPEC,
                identity=f"task-spec:{ref.path}",
                content=content,
                digest=ref.content_sha256,
            ),
        )

    def _task_message(
        self,
        requirement: PSourceRequirement,
        request: PAssemblyRequest,
    ) -> tuple[PSourceAtom, ...]:
        del requirement
        content = self._inputs.task_message.strip()
        if not content:
            return ()
        return (PSourceAtom.create(
            source_kind=PSourceKind.TASK_MESSAGE,
            identity=('subagent-task-message' if request.loop_kind is PLoopKind.SUBAGENT
                      else 'direct-current-request'),
            content=content,
        ),)

    def _core_submission(self, requirement: PSourceRequirement, request: PAssemblyRequest) -> tuple[PSourceAtom, ...]:
        del requirement, request
        return (PSourceAtom.create(
            source_kind=PSourceKind.CORE_SUBMISSION,
            identity=self._inputs.core_submission_identity,
            content=self._inputs.core_submission,
        ),)

    def _direct_review_navigation(self, requirement: PSourceRequirement, request: PAssemblyRequest) -> tuple[PSourceAtom, ...]:
        del request
        name = requirement.source_kind.value
        content = getattr(self._inputs, name)
        if not content.strip():
            return ()
        return (PSourceAtom.create(
            source_kind=requirement.source_kind,
            identity=getattr(self._inputs, f'{name}_identity'), content=content,
        ),)

    def _review_context(self, requirement: PSourceRequirement, request: PAssemblyRequest) -> tuple[PSourceAtom, ...]:
        del requirement, request
        if not self._inputs.review_context.strip():
            return ()
        return (PSourceAtom.create(
            source_kind=PSourceKind.REVIEW_CONTEXT,
            identity=self._inputs.review_context_identity,
            content=self._inputs.review_context,
        ),)

    def _run_dir(self) -> Path | None:
        if self._inputs.run_dir is not None:
            return self._inputs.run_dir.resolve()
        if self._inputs.task_context is not None:
            return Path(self._inputs.task_context.raw_user_message_ref.path).parent
        return None

    def _skill_catalog(
        self,
        requirement: PSourceRequirement,
        request: PAssemblyRequest,
    ) -> tuple[PSourceAtom, ...]:
        del requirement
        snapshot = self._inputs.skill_catalog_snapshot
        if snapshot is None:
            return ()
        content = build_skill_catalog_instruction(
            snapshot.skills,
            context_window_tokens=request.bindings.context_window_tokens,
        )
        if not content:
            return ()
        return (
            PSourceAtom.create(
                source_kind=PSourceKind.SKILL_CATALOG,
                identity="skill-catalog:enabled",
                content=content,
            ),
        )

    def _tool_navigation(
        self,
        requirement: PSourceRequirement,
        request: PAssemblyRequest,
    ) -> tuple[PSourceAtom, ...]:
        del requirement, request
        router = self._inputs.tool_router
        if router is None:
            return ()
        content = render_tool_navigation(router)
        if not content:
            return ()
        return (
            PSourceAtom.create(
                source_kind=PSourceKind.TOOL_NAVIGATION,
                identity=f"tool-router:{router.fingerprint}",
                content=content,
            ),
        )

    def _memory_map(
        self,
        requirement: PSourceRequirement,
        request: PAssemblyRequest,
    ) -> tuple[PSourceAtom, ...]:
        del requirement, request
        memory_map = self._inputs.memory_map
        if memory_map is None:
            return ()
        return (
            PSourceAtom.create(
                source_kind=PSourceKind.MEMORY_MAP,
                identity=memory_map.map_id,
                content=render_memory_map(memory_map),
            ),
        )

    def _project_workspace(
        self,
        requirement: PSourceRequirement,
        request: PAssemblyRequest,
    ) -> tuple[PSourceAtom, ...]:
        del requirement
        value = request.bindings.project_workspace_root
        if not value:
            return ()
        canonical = canonical_project_workspace_root(value, label="P project workspace")
        return (
            PSourceAtom.create(
                source_kind=PSourceKind.PROJECT_WORKSPACE,
                identity=f"project-workspace:{canonical}",
                content=canonical,
            ),
        )

    def _task_images(
        self,
        requirement: PSourceRequirement,
        request: PAssemblyRequest,
    ) -> tuple[PSourceAtom, ...]:
        del requirement, request
        return project_task_images(self._inputs.task_image_records)

    def _revisit_assignment(
        self,
        requirement: PSourceRequirement,
        request: PAssemblyRequest,
    ) -> tuple[PSourceAtom, ...]:
        del requirement
        target_stage = request.stage.value if request.stage is not None else None
        return project_revisit_assignment(
            self._inputs.revisit_state,
            target_stage=target_stage,
        )

    def _feedback_handoff(
        self,
        requirement: PSourceRequirement,
        request: PAssemblyRequest,
    ) -> tuple[PSourceAtom, ...]:
        del requirement, request
        return project_feedback_handoff(
            self._inputs.feedback_state,
            run_dir=self._run_dir(),
        )

    def _hook_prefix(
        self,
        requirement: PSourceRequirement,
        request: PAssemblyRequest,
    ) -> tuple[PSourceAtom, ...]:
        del requirement, request
        atoms: list[PSourceAtom] = []
        for fragment in self._inputs.hook_prefix_fragments:
            if not is_eligible_hook_prefix(fragment):
                continue
            atoms.append(
                PSourceAtom.create(
                    source_kind=PSourceKind.HOOK_PREFIX,
                    identity=f"hook-prefix:{fragment.delivery_id}",
                    content=fragment.text,
            )
        )

        return tuple(atoms)

    def _durable_resources(
        self,
        requirement: PSourceRequirement,
        request: PAssemblyRequest,
    ) -> tuple[PSourceAtom, ...]:
        del requirement
        run_dir = self._run_dir()
        if run_dir is None:
            raise PSourceResolutionError('durable navigation requires run_dir or task context')
        resources = [
            (
                'research_state',
                run_dir / 'research_state.md',
                'The canonical durable record of research progress and runtime state.',
            ),
            (
                'execution_board',
                run_dir / 'execution_board.md',
                (
                    'A compact index of managed long-running executions. Read it with file_reader; '
                    'use execution_monitor for status and recent output, execution_logs for detailed '
                    'stdout/stderr, and execution_kill only to terminate an execution.'
                    ' Use write_stdin with execution_id to continue interacting after resume.'
                ),
            ),
        ]
        if request.level is not StageRuntimeProfile.RESEARCH_LEVEL:
            resources = [item for item in resources if item[0] != 'research_state']
        content = '\n'.join(
            f'- {kind}: {description}\n  path: {path}'
            for kind, path, description in resources
        )
        return (
            PSourceAtom.create(
                source_kind=PSourceKind.DURABLE_RESOURCES,
                identity=f'durable-resources:{run_dir}',
                content=content,
            ),
        ) if content else ()
    def _require_task_context(self) -> RuntimeTaskContext:
        if self._inputs.task_context is None:
            raise PSourceResolutionError("RuntimeTaskContext is unavailable")
        return self._inputs.task_context


def _optional_path(value: str) -> Path | None:
    normalized = str(value or "").strip()
    return Path(normalized) if normalized else None


__all__ = ["CanonicalPSourceAccess", "PSourceAccess"]
