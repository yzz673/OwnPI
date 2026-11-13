"""Canonical stage contract resolution for staged runtime entry."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from importlib import resources

from ownpi.controller.stages import ACTIVE_STAGE_ORDER, Stage
from ownpi.schemas.workflow_plan import StageRuntimeProfile
from ownpi.static_semantic_contracts import get_static_semantic_contract
from ownpi.static_semantic_contracts.store import StaticSemanticContract
from ownpi.static_semantic_contracts.taxonomy import stage_contract_id


SUPPORTED_STAGES: tuple[Stage, ...] = (*ACTIVE_STAGE_ORDER, Stage.OUTPUT_SYNTHESIS)


@dataclass(frozen=True)
class StageContractBundle:
    stage: Stage
    level: StageRuntimeProfile
    stage_contract_ref: str
    stage_contract: str
    stage_contract_sha256: str
    loop_contracts: tuple["StageLoopContract", ...]

    @property
    def manifest_sha256(self) -> str:
        payload = "\n".join(
            f"{item.loop_index}|{item.substage_ref}|{item.substage_sha256}|{item.contract_sha256}"
            for item in self.loop_contracts
        )
        return sha256(payload.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class StageLoopContract:
    loop_index: int
    substage_ref: str
    substage_sha256: str
    contract_ref: str
    contract: str
    contract_sha256: str


def _substage_resources(contract_id: str, directory_path: str) -> tuple[StaticSemanticContract, ...]:
    directory = resources.files("ownpi.static_semantic_contracts").joinpath(
        directory_path
    )
    entries: list[tuple[int, StaticSemanticContract]] = []
    for node in directory.iterdir():
        name = node.name
        if not node.is_file() or not name.startswith("substage-") or not name.endswith(".md"):
            continue
        number = name.removeprefix("substage-").removesuffix(".md")
        if len(number) != 3 or not number.isdigit() or int(number) < 1:
            raise ValueError(f"invalid substage contract filename: {name}")
        raw = node.read_bytes()
        content = raw.decode("utf-8").strip()
        if not content:
            raise ValueError(f"substage contract is empty: {name}")
        entries.append((int(number), StaticSemanticContract(
            contract_id=f"{contract_id}.substage-{number}",
            resource_path=f"{directory_path}/{name}",
            content=content,
            content_sha256=sha256(content.encode("utf-8")).hexdigest(),
        )))
    entries.sort(key=lambda item: item[0])
    if entries and [number for number, _ in entries] != list(range(1, len(entries) + 1)):
        raise ValueError("substage contract numbering must be contiguous from 001")
    return tuple(resource for _, resource in entries)


def resolve_stage_contract(
    stage: Stage,
    level: StageRuntimeProfile,
) -> StageContractBundle:
    if not isinstance(stage, Stage):
        raise TypeError("stage must be a Stage")
    if stage not in SUPPORTED_STAGES:
        raise ValueError(f"Unsupported stage: {stage.value}")
    if not isinstance(level, StageRuntimeProfile):
        raise TypeError("level must be a StageRuntimeProfile")
    if level is not StageRuntimeProfile.RESEARCH_LEVEL:
        raise ValueError("Task Level uses direct execution; staged contracts are inactive")
    level_dir = "research_level"
    contract_id = stage_contract_id(stage.value, level_dir)
    substages = _substage_resources(
        contract_id, f"content/stages/{level_dir}/{stage.value}"
    )
    stage_resource = None if substages else get_static_semantic_contract(contract_id)
    loop_resources = substages or (stage_resource,)
    loops: list[StageLoopContract] = []
    for index, resource in enumerate(loop_resources, start=1):
        assert resource is not None
        substage_ref = resource.resource_path if substages else ""
        substage_digest = resource.content_sha256 if substages else ""
        content = resource.content
        loops.append(StageLoopContract(
            loop_index=index,
            substage_ref=substage_ref,
            substage_sha256=substage_digest,
            contract_ref=resource.resource_path,
            contract=content,
            contract_sha256=sha256(content.encode("utf-8")).hexdigest(),
        ))
    return StageContractBundle(
        stage=stage,
        level=level,
        stage_contract_ref=stage_resource.resource_path if stage_resource else "",
        stage_contract=stage_resource.content if stage_resource else "",
        stage_contract_sha256=stage_resource.content_sha256 if stage_resource else "",
        loop_contracts=tuple(loops),
    )


__all__ = ["SUPPORTED_STAGES", "StageContractBundle", "StageLoopContract", "resolve_stage_contract"]
