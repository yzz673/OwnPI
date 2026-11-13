"""Production entry point for canonical native-loop immutable prefixes."""

from __future__ import annotations

from ownpi.runtime.p_assembly.assembler import PAssembler
from ownpi.runtime.p_assembly.contracts import PAssemblyRequest, PAssemblyResult
from ownpi.runtime.p_assembly.source_access import CanonicalPSourceAccess
from ownpi.runtime.p_assembly.source_inputs import PSourceInputs


def assemble_native_loop_p(
    *,
    request: PAssemblyRequest,
    source_inputs: PSourceInputs,
) -> PAssemblyResult:
    """Resolve and assemble one loop-stable canonical P snapshot."""
    return PAssembler(CanonicalPSourceAccess(source_inputs)).assemble(request)


__all__ = ["assemble_native_loop_p"]
