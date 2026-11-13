from __future__ import annotations
from ownpi.runtime.tools.definitions import ToolExposure

from ownpi.runtime.tools.definitions import ToolDefinition
from ownpi.tools.paper_citation import (
    run_citation_package_validate,
    run_citation_resolve,
    resolve_citation_effects,
)


def build_paper_tool_definitions() -> tuple[ToolDefinition, ...]:
    return (
        ToolDefinition(
            exposure=ToolExposure.DEFERRED,
            name='citation_resolve',
            kind='research_external',
            description='Resolve one DOI, arXiv identifier, or exact title into a provenance-bearing citation receipt and BibTeX.',
            detail='Metadata-only lookup; does not fetch full text. Preserves a source snapshot and returns editable starting BibTeX. Verified identity does not certify every metadata field. Preserve the receipt unchanged; support final bibliography corrections with source evidence.',
            search_hint='Resolve and materialize a verified citation before citing a scholarly source.',
            search_keywords=('citation', 'doi', 'arxiv', 'bibtex', 'crossref', 'reference'),


            read_only=True,
            concurrency_safe=True,
            permission_class='network',
            invocation_mode='agent_requested',
            result_family='citation_receipt',
            max_prompt_result_chars=12_000,
            max_result_size_chars=16_000,
            detail_builder=lambda: {
                'default_input_examples': [
                    {'identifier': '10.1038/s41592-026-03029-6'},
                    {'identifier': 'arXiv:2412.15224v2'},
                    {'title': 'A verified paper title', 'author': 'Smith', 'year': '2025'},
                ],
                'result_highlights': ['outcome', 'canonical_id', 'receipt.content_access_status', 'diagnostics', 'attempted_sources'],
            },
            input_schema_builder=_citation_resolve_schema,
            handler=run_citation_resolve,
            effect_builder=resolve_citation_effects,
            observation_family='citation_receipt',
            requires_network=True,
            result_identity_fields=('canonical_id', 'receipt.receipt_id'),
        ),
        ToolDefinition(
            exposure=ToolExposure.DEFERRED,
            name='citation_package_validate',
            kind='research_validation',
            description='Validate manuscript citation keys against BibTeX entries and intact citation receipts.',
            detail='Checks intact receipts, citation keys and DOI/arXiv identities, not verbatim BibTeX. Metadata differences are warnings for source-based review, not automatic identity failures. Passed means mechanical closure only; justified corrections need not remove warnings. Does not control Runtime stages.',
            search_hint='Check citation closure before packaging a manuscript.',
            search_keywords=('citation', 'bibtex', 'manuscript', 'package', 'validate'),


            read_only=True,
            concurrency_safe=True,
            permission_class='read',
            invocation_mode='agent_requested',
            result_family='citation_validation',
            max_prompt_result_chars=8_000,
            max_result_size_chars=12_000,
            detail_builder=lambda: {
                'default_input_examples': [{
                    'manuscript_text': r'Prior work\\cite{smith2025paper}.',
                    'bibliography_text': '@article{smith2025paper, ...}',
                    'citation_receipts': [],
                }],
                'result_highlights': [
                    'outcome',
                    'validation',
                ],
            },
            input_schema_builder=_citation_package_schema,
            handler=run_citation_package_validate,
            observation_family='citation_validation',
        ),
    )


def _citation_resolve_schema() -> dict[str, object]:
    return {
        'type': 'object',
        'properties': {
            'identifier': {'type': 'string'},
            'title': {'type': 'string'},
            'author': {'type': 'string'},
            'year': {'type': 'string'},
            'timeout_seconds': {'type': 'integer'},
            'max_chars': {'type': 'integer', 'description': 'Retained for input compatibility; metadata-only lookup does not fetch or paginate body text.'},
        },
        'additionalProperties': False,
    }

def _citation_package_schema() -> dict[str, object]:
    return {
        'type': 'object',
        'properties': {
            'manuscript_text': {'type': 'string'},
            'bibliography_text': {'type': 'string'},
            'citation_receipts': {'type': 'array', 'items': {'type': 'object'}},
        },
        'required': ['manuscript_text', 'bibliography_text', 'citation_receipts'],
        'additionalProperties': False,
    }
