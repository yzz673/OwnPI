from __future__ import annotations

from functools import lru_cache

from ownpi.runtime.tools.definitions import ToolDefinition
from ownpi.runtime.tools.defaults_external import build_external_tool_definitions
from ownpi.runtime.tools.defaults_file_mutation import build_file_mutation_tool_definitions
from ownpi.runtime.tools.defaults_inspection import build_inspection_tool_definitions
from ownpi.runtime.tools.defaults_latex import build_latex_tool_definitions
from ownpi.runtime.tools.defaults_local_process import build_local_process_tool_definitions
from ownpi.runtime.tools.defaults_paper import build_paper_tool_definitions
from ownpi.runtime.tools.defaults_remote import build_remote_tool_definitions
from ownpi.runtime.tools.defaults_resources import build_resource_tool_definitions
from ownpi.runtime.tools.defaults_workflow import build_workflow_tool_definitions

_DEFAULT_TOOL_ORDER = (
    'pdf_reader',
    'file_reader',
    'lsp_query',
    'patch_apply',
    'skills_list',
    'skills_read',
    'contract_read',
    'update_plan',
    'human_question',
    'runtime_task_list',
    'runtime_task_get',
    'runtime_task_poll',
    'runtime_task_kill',
    'web_fetch',
    'web_search',
    'list_mcp_resource_templates',
    'mcp_resource_list',
    'mcp_resource_read',
    'mcp_tool_list',
    'mcp_tool_invoke',
    'mcp_auth',
    'paper_fetch',
    'citation_resolve',
    'citation_package_validate',
    'latex_build',
    'pdf_inspect',
    'model_snapshot_materialize',
    'repository_materialize',
    'view_image',
    'remote_profile_list',
    'remote_profile_show',
    'remote_check',
    'remote_resources',
    'remote_deploy',
    'remote_run',
    'remote_monitor',
    'remote_logs',
    'remote_pull',
    'exec_command',
    'write_stdin',
    'execution_monitor',
    'execution_logs',
    'execution_kill',
)

_DEFAULT_TOOL_ORDER_INDEX = {name: index for index, name in enumerate(_DEFAULT_TOOL_ORDER)}


@lru_cache(maxsize=1)
def build_default_tool_definitions() -> tuple[ToolDefinition, ...]:
    definitions = (
        build_inspection_tool_definitions()
        + build_file_mutation_tool_definitions()
        + build_workflow_tool_definitions()
        + build_external_tool_definitions()
        + build_paper_tool_definitions()
        + build_latex_tool_definitions()
        + build_resource_tool_definitions()
        + build_remote_tool_definitions()
        + build_local_process_tool_definitions()
    )
    names = tuple(definition.name for definition in definitions)
    duplicate_names = sorted({name for name in names if names.count(name) > 1})
    if duplicate_names:
        raise RuntimeError(f'duplicate default tool definitions: {duplicate_names}')
    missing_names = [name for name in _DEFAULT_TOOL_ORDER if name not in names]
    extra_names = sorted(name for name in names if name not in _DEFAULT_TOOL_ORDER_INDEX)
    if missing_names or extra_names:
        raise RuntimeError(
            'default tool definition family mismatch: '
            f'missing={missing_names} extra={extra_names}'
        )
    return tuple(sorted(definitions, key=lambda definition: _DEFAULT_TOOL_ORDER_INDEX[definition.name]))
