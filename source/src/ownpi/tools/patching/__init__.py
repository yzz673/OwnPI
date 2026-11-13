"""Internal building blocks for the canonical patch tool."""

from ownpi.tools.patching.filesystem import (
    LocalPatchFileSystem,
    PatchExecutionAuthority,
    PatchFileSystem,
    PatchPolicyError,
    PathIdentity,
)
from ownpi.tools.patching.matcher import seek_sequence
from ownpi.tools.patching.models import (
    ChangeKind,
    PatchChange,
    PatchCommitReport,
    PatchError,
    PatchKind,
    PatchOperation,
    PatchOperationSummary,
    UpdateChunk,
)
from ownpi.tools.patching.parser import parse_patch, patch_environment_id
from ownpi.tools.patching.summary import summarize_operations

__all__ = [
    'ChangeKind',
    'LocalPatchFileSystem',
    'PatchExecutionAuthority',
    'PatchPolicyError',
    'PatchChange',
    'PatchCommitReport',
    'PatchError',
    'PatchKind',
    'PatchFileSystem',
    'PatchOperation',
    'PatchOperationSummary',
    'PathIdentity',
    'UpdateChunk',
    'parse_patch',
    'patch_environment_id',
    'seek_sequence',
    'summarize_operations',
]
