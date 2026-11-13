from __future__ import annotations
from ownpi.runtime.paper_package.latex_build import resolve_latex_build_effects
from ownpi.runtime.paper_package.pdf_inspection import resolve_pdf_inspection_effects
from ownpi.runtime.tools.definitions import ToolDefinition, ToolExposure
from ownpi.tools.paper_latex import run_latex_build_tool, run_pdf_inspect_tool


def build_latex_tool_definitions() -> tuple[ToolDefinition, ...]:
    return (
        ToolDefinition(
            exposure=ToolExposure.DEFERRED,
            name='latex_build',
            kind='document_write',
            description='Build one LaTeX entry point once and return a hashed build receipt with bounded diagnostics.',
            detail=(
                'Uses fixed latexmk arguments, resolves the required bibliography backend, '
                'and preserves unique logs/receipts. workspace_path is based on the runtime '
                'workspace, project_dir is based on workspace_path, and entry_point is based '
                'on project_dir.'
            ),
            search_hint='Compile a manuscript or supplementary LaTeX entry point and diagnose failures.',
            search_keywords=('latex', 'compile', 'pdf', 'bibtex', 'bibliography', 'build'),




            read_only=False,
            concurrency_safe=False,
            permission_class='workspace_write',
            invocation_mode='agent_requested',
            result_family='latex_build',
            max_prompt_result_chars=12_000,
            max_result_size_chars=16_000,
            detail_builder=lambda: {
                'default_input_examples': [
                    {
                        'workspace_path': 'paper',
                        'project_dir': '.',
                        'entry_point': 'main.tex',
                        'engine': 'pdflatex',
                    },
                    {'project_dir': 'paper', 'entry_point': 'supplementary.tex', 'timeout_seconds': 180},
                ],
                'result_highlights': ['outcome', 'build_receipt'],
            },
            input_schema_builder=_latex_build_schema,
            effect_builder=resolve_latex_build_effects,
            handler=run_latex_build_tool,
            observation_family='latex_build',
            writes_workspace=True,
            produces_artifact_refs=True,
            result_identity_fields=('build_receipt.receipt_id',),
        ),
        ToolDefinition(
            exposure=ToolExposure.DEFERRED,
            name='pdf_inspect',
            kind='document_validation',
            description='Parse and render a PDF, checking pages, clipping, broken images, unresolved markers, and expected text.',
            detail=(
                'Returns bounded rendered-page refs for view_image; a second call can bind '
                'visual findings to exact page hashes. workspace_path is based on the runtime '
                'workspace and pdf_path is based on workspace_path.'
            ),
            search_hint='Inspect a compiled manuscript PDF mechanically and visually.',
            search_keywords=('pdf', 'inspect', 'render', 'layout', 'clipping', 'visual'),




            read_only=False,
            concurrency_safe=False,
            permission_class='workspace_write',
            invocation_mode='agent_requested',
            result_family='pdf_inspection',
            max_prompt_result_chars=12_000,
            max_result_size_chars=16_000,
            detail_builder=lambda: {
                'default_input_examples': [
                    {
                        'workspace_path': 'paper',
                        'pdf_path': 'main.pdf',
                        'build_receipt': {},
                        'expected_texts': ['References'],
                    },
                    {
                        'pdf_path': 'paper/main.pdf',
                        'visual_assessment': {
                            'status': 'passed',
                            'inspected_render_hashes': ['<page sha256>'],
                            'findings': [],
                        },
                    },
                ],
                'result_highlights': ['outcome', 'inspection_receipt'],
            },
            input_schema_builder=_pdf_inspect_schema,
            effect_builder=resolve_pdf_inspection_effects,
            handler=run_pdf_inspect_tool,
            observation_family='pdf_inspection',
            writes_workspace=True,
            produces_artifact_refs=True,
            result_identity_fields=('inspection_receipt.receipt_id',),
        ),
    )


def _latex_build_schema() -> dict[str, object]:
    return {
        'type': 'object',
        'properties': {
            'workspace_path': {
                'type': 'string',
                'description': 'Paper workspace path within the runtime workspace; defaults to .',
            },
            'project_dir': {
                'type': 'string',
                'description': 'LaTeX project path within workspace_path.',
            },
            'entry_point': {
                'type': 'string',
                'description': 'LaTeX entry file relative to project_dir.',
            },
            'engine': {'type': 'string', 'enum': ['pdflatex', 'xelatex', 'lualatex']},
            'timeout_seconds': {'type': 'integer'},
        },
        'required': ['project_dir', 'entry_point'],
        'additionalProperties': False,
    }


def _pdf_inspect_schema() -> dict[str, object]:
    return {
        'type': 'object',
        'properties': {
            'workspace_path': {
                'type': 'string',
                'description': 'Paper workspace path within the runtime workspace; defaults to .',
            },
            'pdf_path': {
                'type': 'string',
                'description': 'PDF path within workspace_path.',
            },
            'build_receipt': {'type': 'object'},
            'expected_texts': {'type': 'array', 'items': {'type': 'string'}},
            'visual_assessment': {
                'type': 'object',
                'properties': {
                    'status': {'type': 'string', 'enum': ['passed', 'failed']},
                    'inspected_render_hashes': {'type': 'array', 'items': {'type': 'string'}},
                    'findings': {'type': 'array', 'items': {'type': 'string'}},
                },
                'required': ['status', 'inspected_render_hashes', 'findings'],
                'additionalProperties': False,
            },
        },
        'required': ['pdf_path'],
        'additionalProperties': False,
    }
