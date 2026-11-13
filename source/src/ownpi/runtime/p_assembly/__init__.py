"""Canonical requirement and assembly owner for immutable provider prefixes."""

from ownpi.runtime.p_assembly.assembler import PAssembler
from ownpi.runtime.p_assembly.contracts import (
    PAssemblyBindings,
    PAssemblyError,
    PAssemblyRequest,
    PAssemblyResult,
    PLoopKind,
    PSourceAtom,
    PSourceCategory,
    PSourceKind,
)
from ownpi.runtime.p_assembly.requirements import RECIPES, resolve_p_recipe
from ownpi.runtime.p_assembly.service import assemble_native_loop_p
from ownpi.runtime.p_assembly.source_access import (
    CanonicalPSourceAccess,
    PSourceAccess,
)
from ownpi.runtime.p_assembly.source_inputs import PSourceInputs

__all__ = [
    "CanonicalPSourceAccess",
    "PAssemblyBindings",
    "PAssemblyError",
    "PAssemblyRequest",
    "PAssemblyResult",
    "PAssembler",
    "PLoopKind",
    "PSourceAtom",
    "PSourceCategory",
    "PSourceKind",
    "PSourceAccess",
    "PSourceInputs",
    "RECIPES",
    "resolve_p_recipe",
    "assemble_native_loop_p",
]
