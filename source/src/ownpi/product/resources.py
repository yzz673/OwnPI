from __future__ import annotations

from dataclasses import dataclass
from importlib import resources

from ownpi.runtime.instructions import (
    InstructionResolutionError,
    resolve_builtin_instructions,
)

from importlib.resources.abc import Traversable

_TEMPLATE_PACKAGE = "ownpi.resources.templates"
_STATIC_SEMANTIC_PACKAGE = "ownpi.static_semantic_contracts"
_BUNDLED_SKILLS_PACKAGE = "ownpi.skills.bundled.content"


@dataclass(frozen=True)
class ProductResourceItem:
    category: str
    name: str
    package: str
    path: str
    readable: bool
    error: str = ""

    def to_dict(self) -> dict[str, object]:
        return {
            "category": self.category,
            "name": self.name,
            "package": self.package,
            "path": self.path,
            "readable": self.readable,
            "error": self.error,
        }


@dataclass(frozen=True)
class ProductResourceInspection:
    ok: bool
    templates: list[ProductResourceItem]
    always_on_instructions: list[ProductResourceItem]
    bundled_skills: list[ProductResourceItem]

    def to_dict(self) -> dict[str, object]:
        return {
            "ok": self.ok,
            "templates": [item.to_dict() for item in self.templates],
            "always_on_instructions": [
                item.to_dict() for item in self.always_on_instructions
            ],
            "bundled_skills": [item.to_dict() for item in self.bundled_skills],
        }


def read_template_resource(name: str) -> str:
    requested = str(name or "").strip()
    if not requested or "/" in requested or requested.startswith("."):
        raise ValueError(f"Invalid template resource name: {name!r}")
    return _resource_root(_TEMPLATE_PACKAGE).joinpath(requested).read_text(encoding="utf-8")


def list_template_resources() -> list[ProductResourceItem]:
    return _list_package_files(
        category="template",
        package=_TEMPLATE_PACKAGE,
        suffixes=(".yaml", ".example"),
    )


def list_always_on_instruction_resources() -> list[ProductResourceItem]:
    try:
        resolved = resolve_builtin_instructions()
    except InstructionResolutionError as exc:
        return [
            ProductResourceItem(
                category="always_on_instruction",
                name="<canonical-common-contracts>",
                package=_STATIC_SEMANTIC_PACKAGE,
                path="content/common",
                readable=False,
                error=str(exc),
            )
        ]
    return [
        ProductResourceItem(
            category="always_on_instruction",
            name=item.relative_path,
            package=_STATIC_SEMANTIC_PACKAGE,
            path=item.locator,
            readable=True,
        )
        for item in resolved
    ]


def list_bundled_skill_resources() -> list[ProductResourceItem]:
    root = _resource_root(_BUNDLED_SKILLS_PACKAGE)
    items: list[ProductResourceItem] = []
    try:
        children = sorted(root.iterdir(), key=lambda item: item.name)
    except (FileNotFoundError, ModuleNotFoundError, NotADirectoryError) as exc:
        return [
            ProductResourceItem(
                category="bundled_skill",
                name="<package>",
                package=_BUNDLED_SKILLS_PACKAGE,
                path=_BUNDLED_SKILLS_PACKAGE,
                readable=False,
                error=str(exc),
            )
        ]
    for child in children:
        if not child.is_dir() or child.name.startswith("__"):
            continue
        skill_file = child.joinpath("SKILL.md")
        items.append(_inspect_resource("bundled_skill", _BUNDLED_SKILLS_PACKAGE, skill_file, name=child.name))
    return items


def inspect_product_resources() -> ProductResourceInspection:
    templates = list_template_resources()
    instructions = list_always_on_instruction_resources()
    skills = list_bundled_skill_resources()
    all_items = [*templates, *instructions, *skills]
    return ProductResourceInspection(
        ok=bool(templates)
        and bool(instructions)
        and bool(skills)
        and all(item.readable for item in all_items),
        templates=templates,
        always_on_instructions=instructions,
        bundled_skills=skills,
    )


def _resource_root(package: str) -> Traversable:
    return resources.files(package)


def _list_package_files(*, category: str, package: str, suffixes: tuple[str, ...]) -> list[ProductResourceItem]:
    root = _resource_root(package)
    try:
        children = sorted(root.iterdir(), key=lambda item: item.name)
    except (FileNotFoundError, ModuleNotFoundError, NotADirectoryError) as exc:
        return [
            ProductResourceItem(
                category=category,
                name="<package>",
                package=package,
                path=package,
                readable=False,
                error=str(exc),
            )
        ]
    return [
        _inspect_resource(category, package, child)
        for child in children
        if child.is_file() and child.name.endswith(suffixes)
    ]


def _inspect_resource(
    category: str,
    package: str,
    resource: Traversable,
    *,
    name: str | None = None,
) -> ProductResourceItem:
    try:
        resource.read_bytes()
        readable = True
        error = ""
    except (FileNotFoundError, OSError, UnicodeError) as exc:
        readable = False
        error = str(exc)
    return ProductResourceItem(
        category=category,
        name=name or resource.name,
        package=package,
        path=str(resource),
        readable=readable,
        error=error,
    )
