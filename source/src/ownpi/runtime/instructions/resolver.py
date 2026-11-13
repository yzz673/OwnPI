from __future__ import annotations

import hashlib
import logging
from pathlib import Path

from ownpi.runtime.instructions.contracts import (
    InstructionResolutionError,
    ResolvedInstruction,
)
from ownpi.static_semantic_contracts import get_static_semantic_contract
from ownpi.static_semantic_contracts.index import COMMON_CONTRACT_IDS


_STATIC_SEMANTIC_PACKAGE = "ownpi.static_semantic_contracts"


def discover_instructions(
    *,
    project_root: Path | None,
    app_home: Path | None = None,
    current_working_directory: Path | None = None,
) -> tuple[ResolvedInstruction, ...]:
    instructions = list(_resolve_builtin_common_contracts())
    order_key = len(instructions)
    if app_home is not None:
        user_global = app_home.expanduser().resolve() / "OWNPI.md"
        instruction = _read_user_instruction(
            user_global,
            source_family="user_global",
            scope="user_global",
            order_key=order_key,
        )
        if instruction is not None:
            instructions.append(instruction)
            order_key += 1

    for candidate in _project_instruction_paths(
        project_root=project_root,
        current_working_directory=current_working_directory,
    ):
        instruction = _read_user_instruction(
            candidate,
            source_family="project",
            scope="project",
            order_key=order_key,
            project_root=project_root,
        )
        if instruction is not None:
            instructions.append(instruction)
            order_key += 1
    return tuple(instructions)


def resolve_builtin_instructions() -> tuple[ResolvedInstruction, ...]:
    return _resolve_builtin_common_contracts()


def _resolve_builtin_common_contracts() -> tuple[ResolvedInstruction, ...]:
    ordered: list[ResolvedInstruction] = []
    for contract_id in COMMON_CONTRACT_IDS:
        try:
            contract = get_static_semantic_contract(contract_id)
        except (KeyError, FileNotFoundError, OSError, ValueError) as exc:
            raise InstructionResolutionError(
                f"unable to read built-in static semantic contract {contract_id}: {exc}"
            ) from exc
        relative_path = contract.resource_path.removeprefix("content/")
        ordered.append(
            ResolvedInstruction(
                source_family="runtime_builtin",
                scope="runtime_builtin",
                locator=(
                    f"package://{_STATIC_SEMANTIC_PACKAGE}/{contract.resource_path}"
                ),
                relative_path=relative_path,
                digest=contract.content_sha256,
                content=contract.content,
                order_key=len(ordered),
            )
        )
    return tuple(ordered)


def _project_instruction_paths(
    *,
    project_root: Path | None,
    current_working_directory: Path | None,
) -> tuple[Path, ...]:
    if project_root is None:
        cwd = (current_working_directory or Path.cwd()).expanduser().resolve()
        return (cwd / "OWNPI.md",)
    root = project_root.expanduser().resolve()
    cwd = (current_working_directory or Path.cwd()).expanduser().resolve()
    if not root.exists():
        return (cwd / "OWNPI.md",)
    try:
        relative = cwd.relative_to(root)
    except ValueError:
        return (root / "OWNPI.md",)
    directories = [root]
    cursor = root
    for part in relative.parts:
        cursor = cursor / part
        directories.append(cursor)
    return tuple(directory / "OWNPI.md" for directory in directories)


def _read_user_instruction(
    path: Path,
    *,
    source_family: str,
    scope: str,
    order_key: int,
    project_root: Path | None = None,
) -> ResolvedInstruction | None:
    try:
        if not path.is_file():
            return None
        content = path.read_bytes().decode("utf-8", errors="replace").strip()
    except FileNotFoundError:
        return None
    except OSError as exc:
        logging.getLogger(__name__).warning(
            "Unable to read %s instruction %s: %s", source_family, path, exc
        )
        return None
    if not content:
        return None
    resolved = path.resolve()
    relative_path = resolved.name
    if project_root is not None:
        try:
            relative_path = resolved.relative_to(project_root.resolve()).as_posix()
        except ValueError:
            pass
    return ResolvedInstruction(
        source_family=source_family,
        scope=scope,
        locator=str(resolved),
        relative_path=relative_path,
        digest=_digest(content),
        content=content,
        order_key=order_key,
    )


def _digest(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()
