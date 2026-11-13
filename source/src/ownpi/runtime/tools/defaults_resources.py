from __future__ import annotations

from ownpi.runtime.limits.projection import MAX_SKILL_RESOURCE_PAGE_LINES
from ownpi.runtime.tools.definitions import ToolDefinition, ToolExposure
from ownpi.tools.resource_materialization import (
    model_snapshot_materialization_effects,
    repository_materialization_effects,
    run_model_snapshot_materialize,
    run_repository_materialize,
)
from ownpi.skills.resource_tools import run_skills_list, run_skills_read
from ownpi.static_semantic_contracts.resource_tools import run_contract_read


def build_resource_tool_definitions() -> tuple[ToolDefinition, ...]:
    return (
        ToolDefinition(
            exposure=ToolExposure.DIRECT,
            name='skills_list',
            kind='skill_resource_list',
            description=(
                'List enabled Skill packages and their model-facing locators when the catalog '
                'does not provide a known package.'
            ),
            detail=(
                'Skill discovery. Use when a package locator is unknown or omitted from the catalog; '
                'the role-visible Skill catalog is the primary discovery surface.'
            ),
            read_only=True,
            concurrency_safe=True,
            permission_class='inspection',
            invocation_mode='agent_requested',
            result_family='skill_catalog',
            toolset_family='resources',
            backend_kind='local_read',
            side_effect_level='read',
            input_schema_builder=lambda: {
                'type': 'object',
                'properties': {
                    'cursor': {'type': 'integer', 'minimum': 0},
                    'limit': {'type': 'integer', 'minimum': 1, 'maximum': 50},
                },
                'additionalProperties': False,
            },
            handler=run_skills_list,
            observation_family='skill_catalog',
            max_prompt_result_chars=8000,
            max_result_size_chars=16000,
        ),
        ToolDefinition(
            exposure=ToolExposure.DIRECT,
            name='skills_read',
            kind='skill_resource_read',
            description=(
                'Read one page of instructions or text resources from an enabled Skill. Omit resource to read '
                'SKILL.md; a directory resource lists its immediate children (use . for the package root).'
            ),
            detail=(
                'Use the package identifier from the catalog for Skills from any source. '
                'Referenced resources and listed child resource paths are relative to that Skill root. Results are paged with cursor '
                'and optional column_offset when a line exceeds the current model budget.'
            ),
            read_only=True,
            concurrency_safe=True,
            permission_class='inspection',
            invocation_mode='agent_requested',
            result_family='skill_resource',
            toolset_family='resources',
            backend_kind='local_read',
            side_effect_level='read',
            input_schema_builder=lambda: {
                'type': 'object',
                'properties': {
                    'package': {'type': 'string'},
                    'resource': {'type': 'string'},
                    'cursor': {'type': 'integer', 'minimum': 1},
                    'column_offset': {'type': 'integer', 'minimum': 0},
                    'limit': {
                        'type': 'integer',
                        'minimum': 1,
                        'maximum': MAX_SKILL_RESOURCE_PAGE_LINES,
                    },
                },
                'required': ['package'],
                'additionalProperties': False,
            },
            handler=run_skills_read,
            observation_family='skill_resource',
            max_prompt_result_chars=24000,
            max_result_size_chars=48000,
        ),
        ToolDefinition(
            exposure=ToolExposure.DIRECT,
            name='contract_read',
            kind='static_semantic_contract_read',
            description=(
                'Read one page from a registered canonical static semantic Contract by its exact ID.'
            ),
            detail=(
                'Use the contract_id supplied inline by a Role Contract. This reader does not '
                'list contracts, accept paths, or browse package resources. Results are paged '
                'with a 1-based line offset (default 1) and optional 0-based character '
                'column_offset. Continue with the returned next_offset and next_column_offset.'
            ),
            read_only=True,
            concurrency_safe=True,
            permission_class='inspection',
            invocation_mode='agent_requested',
            result_family='static_semantic_contract',
            toolset_family='resources',
            backend_kind='local_read',
            side_effect_level='read',
            input_schema_builder=lambda: {
                'type': 'object',
                'properties': {
                    'contract_id': {'type': 'string'},
                    'offset': {
                        'type': 'integer', 'minimum': 1,
                        'description': '1-based starting line number; defaults to 1.',
                    },
                    'column_offset': {'type': 'integer', 'minimum': 0},
                    'limit': {
                        'type': 'integer',
                        'minimum': 1,
                        'maximum': MAX_SKILL_RESOURCE_PAGE_LINES,
                    },
                },
                'required': ['contract_id'],
                'additionalProperties': False,
            },
            handler=run_contract_read,
            observation_family='static_semantic_contract',
            max_prompt_result_chars=24000,
            max_result_size_chars=48000,
        ),
        ToolDefinition(
            exposure=ToolExposure.DEFERRED,
            name='model_snapshot_materialize',
            kind='resource_materialization',
            description='Materialize and verify an immutable Hugging Face model snapshot for offline local use.',
            detail='Resolves a model revision, transfers through configured runtime routes, verifies files, and returns an authoritative local path.',
            search_hint='Use for Hugging Face models, checkpoints, tokenizers, processors, or immutable offline snapshots.',
            search_keywords=('hugging face', 'model', 'checkpoint', 'tokenizer', 'processor', 'snapshot', 'offline'),
            detail_builder=lambda: {
                'default_input_examples': [{'repo_id': 'owner/model', 'revision': 'main', 'allow_patterns': ['*.json', '*.safetensors']}],
                'result_highlights': ['resolved_revision', 'local_path', 'verified_files', 'verified_bytes', 'receipt_ref'],
            },
            input_schema_builder=lambda: {
                'type': 'object',
                'properties': {
                    'repo_id': {'type': 'string'},
                    'revision': {'type': 'string'},
                    'allow_patterns': {'type': 'array', 'items': {'type': 'string'}, 'maxItems': 32},
                },
                'required': ['repo_id'],
                'additionalProperties': False,
            },
            handler=run_model_snapshot_materialize,
            effect_builder=model_snapshot_materialization_effects,
            observation_family='resource_materialized',
            result_family='model_snapshot',
            requires_network=True,
            writes_workspace=True,
            max_prompt_result_chars=4000,
            max_result_size_chars=8000,
            result_identity_fields=('repo_id', 'revision'),
        ),
        ToolDefinition(
            exposure=ToolExposure.DEFERRED,
            name='repository_materialize',
            kind='resource_materialization',
            description='Materialize and verify an immutable GitHub source snapshot or SSH Git worktree.',
            detail='Uses configured API/codeload or SSH routes and returns an authoritative local repository path pinned to a commit.',
            search_hint='Use for GitHub repository source, archive, clone, worktree, or revision materialization.',
            search_keywords=('github', 'repository', 'source', 'archive', 'clone', 'worktree', 'revision'),
            detail_builder=lambda: {
                'default_input_examples': [{'repository': 'owner/repository', 'revision': 'main', 'content_mode': 'source_snapshot'}],
                'result_highlights': ['resolved_revision', 'local_path', 'content_mode', 'route_class', 'receipt_ref'],
            },
            input_schema_builder=lambda: {
                'type': 'object',
                'properties': {
                    'repository': {'type': 'string'},
                    'revision': {'type': 'string'},
                    'content_mode': {'type': 'string', 'enum': ['source_snapshot', 'git_worktree']},
                },
                'required': ['repository'],
                'additionalProperties': False,
            },
            handler=run_repository_materialize,
            effect_builder=repository_materialization_effects,
            observation_family='resource_materialized',
            result_family='repository_snapshot',
            requires_network=True,
            writes_workspace=True,
            max_prompt_result_chars=4000,
            max_result_size_chars=8000,
            result_identity_fields=('repository', 'revision', 'content_mode'),
        ),
    )
