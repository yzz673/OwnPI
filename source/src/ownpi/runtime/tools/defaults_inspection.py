from __future__ import annotations

from typing import Any

from ownpi.runtime.tools.definitions import ToolDefinition, ToolExposure
from ownpi.runtime.permissions.action_compiler import OperationEffects
from ownpi.runtime.permissions.models import FileSystemAccessMode
from ownpi.runtime.permissions.orchestrator import FileSystemAccessRequest
from ownpi.runtime.permissions.policy import lexical_absolute_path
from ownpi.tools.file_reader import run_file_reader
from ownpi.runtime.tools.lsp import resolve_lsp_query_effects, run_lsp_query
from ownpi.tools.pdf_reader import resolve_pdf_reader_effects, run_pdf_reader


def build_inspection_tool_definitions() -> tuple[ToolDefinition, ...]:
    definitions = (
        ToolDefinition(
            exposure=ToolExposure.DIRECT,
            name='pdf_reader',
            kind='document',
            description='Extract text from PDF files with OCR fallback.',
            detail=(
                    'Use for literature_grounding when the task is literature-oriented and '
                    'dataset.path points to a PDF or a clarified PDF path is available. Returns extractor metadata, readable page counts, '
                    'and a text preview for grounding.'
                ),
            search_hint='Read and ground a local PDF when the task is literature-oriented.',
            search_keywords=('pdf', 'paper', 'document', 'literature', 'grounding'),


            read_only=True,
            concurrency_safe=True,
            permission_class='document',
            invocation_mode='agent_requested',
            action_primitives=('read',),
            primary_intent='read',
            grounding_lane='grounded',
            input_contract_family='pdf_path_pages',
            selection_disambiguators=(
                    'Use for PDF page extraction or OCR; use file_reader for ordinary text files.',
                ),
            detail_builder=lambda: {
                'supports_ocr_fallback': True,
                'input_notes': 'Provide file_path and optional pages when you need a bounded page subset.',
                'default_input_examples': [
                    {'file_path': '/abs/path/paper.pdf'},
                    {'file_path': '/abs/path/paper.pdf', 'pages': [1, 2, 3]},
                ],
                'result_highlights': ['extractor', 'page_count', 'readable_pages', 'pages', 'text_preview'],
            },
            input_schema_builder=lambda: {
                'type': 'object',
                'properties': {
                    'file_path': {'type': 'string'},
                    'pages': {
                        'type': 'array',
                        'items': {'type': 'integer'},
                    },
                },
                'required': ['file_path'],
                'additionalProperties': False,
            },
            effect_builder=resolve_pdf_reader_effects,
            handler=run_pdf_reader,
            observation_family='document_text',
        ),
        ToolDefinition(
            exposure=ToolExposure.DIRECT,
            name='file_reader',
            kind='file_read',
            description=(
                'Read an existing ordinary UTF-8 text file by line window, with column pagination '
                'for long lines; do not use for binary or specialized file formats; never creates '
                'or edits files.'
            ),
            detail=(
                    'Claude-Code-style Read capability for continuation loops. '
                    'Use after tool_search when you need exact file contents, '
                    'line windows, or config/script excerpts before execution. This is strictly read-only: '
                    'Use patch_apply or exec_command when a file must be changed.'
                ),
            aliases=('Read',),
            search_hint='Read exact existing file contents before editing or execution.',
            search_keywords=('read', 'file', 'source', 'lines', 'excerpt'),


            read_only=True,
            concurrency_safe=True,
            permission_class='inspection',
            invocation_mode='agent_requested',
            action_primitives=('read',),
            primary_intent='read',
            grounding_lane='grounded',
            input_contract_family='file_path_window',
            selection_disambiguators=(
                    'Use only for existing file content; never for creating or editing files.',
                    'Use patch_apply or exec_command for file changes.',
                ),
            detail_builder=lambda: {
                    'input_notes': 'Provide file_path, optional offset, column_offset, and limit.',
                'default_input_examples': [
                    {'file_path': 'README.md'},
                    {'file_path': 'configs/train.yaml', 'offset': 1, 'limit': 80},
                ],
                'result_highlights': ['file_path', 'offset', 'end_line', 'truncated', 'excerpt'],
            },
            input_schema_builder=lambda: {
                'type': 'object',
                'properties': {
                    'file_path': {'type': 'string'},
                    'offset': {'type': 'integer', 'minimum': 1},
                    'column_offset': {'type': 'integer', 'minimum': 0},
                    'limit': {'type': 'integer', 'minimum': 1, 'maximum': 300},
                },
                'required': ['file_path'],
                'additionalProperties': False,
            },
            handler=run_file_reader,
            effect_builder=_resolve_file_reader_effects,
            observation_family='file_excerpt',
        ),
        ToolDefinition(
            exposure=ToolExposure.DEFERRED,
            name='lsp_query',
            kind='code_intelligence',
            description='Read-only code intelligence query with LSP-style operations and bounded fallback.',
            detail=(
                    'Use for definition, references, hover, document_symbols, workspace_symbols, '
                    'or diagnostics. This is a per-call read-only tool; when no language server is '
                    'available it falls back to Python AST and bounded grep.'
                ),
            aliases=('LSP', 'code_intelligence'),
            search_hint='Ask for definitions, references, hover, symbols, or diagnostics in the workspace.',
            search_keywords=('lsp', 'definition', 'references', 'hover', 'symbols', 'diagnostics', 'code'),


            read_only=True,
            concurrency_safe=True,
            permission_class='inspection',
            backend_kind='local_exec',
            execution_class='command_exec',
            side_effect_level='process_spawn',
            isolation_requirement='subprocess',
            permission_matcher_kind='default',
            observable_input_fields=('operation', 'path', 'symbol', 'language', 'max_results'),
            invocation_mode='agent_requested',
            detail_builder=lambda: {
                'input_notes': (
                    'Provide operation plus path/line/character or symbol as needed. '
                    'Results are bounded and read-only.'
                ),
                'default_input_examples': [
                    {'operation': 'definition', 'symbol': 'WorkflowEngine'},
                    {'operation': 'references', 'path': 'src/ownpi/controller/engine.py', 'symbol': 'run'},
                    {'operation': 'diagnostics', 'path': 'src/ownpi/runtime/tool_runtime.py', 'language': 'python'},
                ],
                'result_highlights': ['operation', 'backend', 'fallback_mode', 'result_count', 'results'],
            },
            input_schema_builder=lambda: {
                'type': 'object',
                'properties': {
                    'operation': {
                        'type': 'string',
                        'enum': [
                            'definition',
                            'references',
                            'hover',
                            'document_symbols',
                            'workspace_symbols',
                            'diagnostics',
                        ],
                    },
                    'path': {'type': 'string'},
                    'line': {'type': 'integer'},
                    'character': {'type': 'integer'},
                    'symbol': {'type': 'string'},
                    'language': {'type': 'string'},
                    'max_results': {'type': 'integer'},
                },
                'required': ['operation'],
                'additionalProperties': False,
            },
            effect_builder=resolve_lsp_query_effects,
            handler=run_lsp_query,
            observation_family='code_intelligence',
            result_family='code_intelligence',
            max_result_size_chars=6000,
        ),
    )
    return definitions


def _resolve_file_reader_effects(context: Any, arguments: dict[str, Any]) -> OperationEffects:
    path = lexical_absolute_path(arguments.get('file_path') or '.', cwd=context.project_root)
    return OperationEffects.declared(
        file_system_accesses=(
            FileSystemAccessRequest(path, FileSystemAccessMode.READ),
        ),
        source='file_reader_declared_read',
    )
