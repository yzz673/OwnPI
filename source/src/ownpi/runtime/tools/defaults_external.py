from __future__ import annotations


from ownpi.runtime.tools.default_helpers import (
    _mcp_activity,
    _mcp_classifier_input,
    _mcp_result_summary,
    _validate_mcp_tool_invoke_args,
)
from ownpi.runtime.tools.definitions import ToolDefinition, ToolExposure
from ownpi.runtime.tools.effects import (
    resolve_image_effects,
    resolve_mcp_effects,
    resolve_mcp_resource_list_effects,
    resolve_mcp_template_effects,
    resolve_network_effects,
)
from ownpi.tools.view_image import run_view_image
from ownpi.tools.mcp_tools import (
    run_list_mcp_resource_templates,
    run_mcp_auth,
    run_mcp_resource_list,
    run_mcp_resource_read,
    run_mcp_tool_invoke,
    run_mcp_tool_list,
)
from ownpi.tools.paper_fetch import resolve_paper_fetch_effects, run_paper_fetch
from ownpi.tools.web_tools import run_web_fetch, run_web_search


def build_external_tool_definitions() -> tuple[ToolDefinition, ...]:
    return (
        ToolDefinition(
            exposure=ToolExposure.DIRECT,
            name='web_fetch',
            kind='web_fetch',
            description='Fetch and summarize a web page.',
            detail='Network-enabled fetch using httpx + BeautifulSoup.',


            read_only=True,
            concurrency_safe=True,
            permission_class='network',
            invocation_mode='agent_requested',
            result_family='web',
            detail_builder=lambda: {'default_input_examples': [{'url': 'https://example.com'}], 'result_highlights': ['final_url', 'title', 'headings', 'text_excerpt']},
            input_schema_builder=lambda: {'type': 'object', 'properties': {'url': {'type': 'string'}, 'timeout_seconds': {'type': 'integer'}, 'max_chars': {'type': 'integer'}}, 'required': ['url'], 'additionalProperties': False},
            handler=run_web_fetch,
            effect_builder=resolve_network_effects,
            observation_family='web_fetch',
            requires_network=True,
        ),
        ToolDefinition(
            exposure=ToolExposure.DIRECT,
            name='web_search',
            kind='web_search',
            description='Local fallback web search via DuckDuckGo and explicit arXiv backends.',
            detail='Provider-independent candidate search with bounded timeouts, backend provenance, and truthful degraded-route metadata.',


            read_only=True,
            concurrency_safe=True,
            permission_class='network',
            invocation_mode='agent_requested',
            result_family='web_search',
            detail_builder=lambda: {
                'default_input_examples': [
                    {'query': 'BCI public demo repo'},
                    {'query': 'EEG seizure detection baseline', 'timeout_seconds': 20},
                ],
                'result_highlights': ['query', 'results', 'source_backend', 'attempted_backends'],
            },
            input_schema_builder=lambda: {
                'type': 'object',
                'properties': {
                    'query': {'type': 'string'},
                    'limit': {'type': 'integer'},
                    'timeout_seconds': {'type': 'integer'},
                    'backend': {'type': 'string'},
                },
                'required': ['query'],
                'additionalProperties': False,
            },
            handler=run_web_search,
            effect_builder=resolve_network_effects,
            observation_family='web_search',
            requires_network=True,
        ),
        ToolDefinition(
            exposure=ToolExposure.HIDDEN,
            name='list_mcp_resource_templates',
            kind='mcp_read',
            description=(
                    'Lists resource templates provided by MCP servers. Parameterized resource '
                    'templates allow servers to share data that takes parameters and provides '
                    'context to language models, such as files, database schemas, or '
                    'application-specific information. Prefer resource templates over web search '
                    'when possible.'
                ),
            detail='Standard MCP resources/templates/list discovery for configured servers.',
            search_hint='Discover parameterized resource templates from configured MCP servers.',
            search_keywords=('mcp', 'resource', 'template', 'parameterized', 'list'),


            read_only=True,
            concurrency_safe=True,
            permission_class='mcp',
            invocation_mode='agent_requested',
            detail_builder=lambda: {
                'default_input_examples': [{}, {'server': 'zotero'}, {'server': 'zotero', 'cursor': 'next-page'}],
                'result_highlights': ['server', 'resourceTemplates', 'nextCursor'],
            },
            input_schema_builder=lambda: {
                'type': 'object',
                'properties': {
                    'server': {'type': 'string'},
                    'cursor': {'type': 'string'},
                },
                'additionalProperties': False,
            },
            handler=run_list_mcp_resource_templates,
            effect_builder=resolve_mcp_template_effects,
            activity_builder=lambda arguments: _mcp_activity(
                'list_mcp_resource_templates', arguments
            ),
            classifier_input_builder=lambda arguments: _mcp_classifier_input(
                'list_mcp_resource_templates', arguments
            ),
            summary_builder=_mcp_result_summary,
            observation_family='mcp',
            result_family='mcp',
            max_prompt_result_chars=6000,
            max_result_size_chars=12000,
        ),
        ToolDefinition(
            exposure=ToolExposure.HIDDEN,
            name='mcp_resource_list',
            kind='mcp_read',
            description='List configured MCP resources or cached MCP resource metadata.',
            detail='MCP-backed read capability; v1 reads configured server/cache state.',
            search_hint='List MCP resources from a configured stdio server or runtime cache.',
            search_keywords=('mcp', 'resource', 'list', 'server', 'cache'),


            read_only=True,
            concurrency_safe=True,
            permission_class='mcp',
            invocation_mode='agent_requested',
            result_family='mcp',
            detail_builder=lambda: {'default_input_examples': [{}, {'server_name': 'zotero'}], 'result_highlights': ['configured_servers', 'resources']},
            input_schema_builder=lambda: {'type': 'object', 'properties': {'server_name': {'type': 'string'}, 'refresh': {'type': 'boolean'}}, 'additionalProperties': False},
            handler=run_mcp_resource_list,
            effect_builder=resolve_mcp_resource_list_effects,
            activity_builder=lambda arguments: _mcp_activity('mcp_resource_list', arguments),
            classifier_input_builder=lambda arguments: _mcp_classifier_input('mcp_resource_list', arguments),
            summary_builder=_mcp_result_summary,
            observation_family='mcp',
        ),
        ToolDefinition(
            exposure=ToolExposure.HIDDEN,
            name='mcp_resource_read',
            kind='mcp_read',
            description='Read one MCP resource payload.',
            detail='MCP-backed read capability; v1 reads from runtime cache.',
            search_hint='Read one MCP resource payload from cache or a configured stdio server.',
            search_keywords=('mcp', 'resource', 'read', 'uri', 'payload'),


            read_only=True,
            concurrency_safe=True,
            permission_class='mcp',
            invocation_mode='agent_requested',
            result_family='mcp',
            detail_builder=lambda: {'default_input_examples': [{'resource_id': 'zotero:paper:123'}], 'result_highlights': ['resource']},
            input_schema_builder=lambda: {'type': 'object', 'properties': {'resource_id': {'type': 'string'}, 'server_name': {'type': 'string'}}, 'required': ['resource_id'], 'additionalProperties': False},
            handler=run_mcp_resource_read,
            effect_builder=resolve_mcp_effects,
            activity_builder=lambda arguments: _mcp_activity('mcp_resource_read', arguments),
            classifier_input_builder=lambda arguments: _mcp_classifier_input('mcp_resource_read', arguments),
            summary_builder=_mcp_result_summary,
            observation_family='mcp',
        ),
        ToolDefinition(
            exposure=ToolExposure.HIDDEN,
            name='mcp_tool_list',
            kind='mcp_tool',
            description='List tools exposed by a configured MCP server.',
            detail='MCP tool-plane discovery for stdio and supported remote transports.',
            search_hint='Discover tools exposed by a configured MCP server.',
            search_keywords=('mcp', 'tool', 'list', 'server', 'schema'),


            read_only=True,
            concurrency_safe=True,
            permission_class='mcp',
            invocation_mode='agent_requested',
            result_family='mcp',
            detail_builder=lambda: {
                'default_input_examples': [{'server_name': 'zotero'}],
                'result_highlights': ['server_name', 'tools'],
            },
            input_schema_builder=lambda: {
                'type': 'object',
                'properties': {'server_name': {'type': 'string'}},
                'additionalProperties': False,
            },
            handler=run_mcp_tool_list,
            effect_builder=resolve_mcp_effects,
            activity_builder=lambda arguments: _mcp_activity('mcp_tool_list', arguments),
            classifier_input_builder=lambda arguments: _mcp_classifier_input('mcp_tool_list', arguments),
            summary_builder=_mcp_result_summary,
            observation_family='mcp',
        ),
        ToolDefinition(
            exposure=ToolExposure.HIDDEN,
            name='mcp_tool_invoke',
            kind='mcp_tool',
            description='Invoke a tool exposed by a configured MCP server.',
            detail='MCP tool-plane execution for stdio and supported remote transports.',
            search_hint='Invoke one tool on a configured MCP server with structured input.',
            search_keywords=('mcp', 'tool', 'invoke', 'server', 'input'),


            read_only=True,
            concurrency_safe=False,
            permission_class='mcp',
            requires_network=True,
            backend_kind='mcp',
            environment_affinity='remote_required',
            execution_class='external_service',
            side_effect_level='remote_execution',
            isolation_requirement='host_network',
            invocation_mode='agent_requested',
            result_family='mcp',
            detail_builder=lambda: {
                'default_input_examples': [{'server_name': 'zotero', 'tool_name': 'search_papers', 'input': {'query': 'EEG'}}],
                'result_highlights': ['server_name', 'tool_name', 'content'],
            },
            input_schema_builder=lambda: {
                'type': 'object',
                'properties': {
                    'server_name': {'type': 'string'},
                    'tool_name': {'type': 'string'},
                    'input': {'type': 'object'},
                },
                'required': ['tool_name'],
                'additionalProperties': False,
            },
            input_validator=_validate_mcp_tool_invoke_args,
            handler=run_mcp_tool_invoke,
            effect_builder=resolve_mcp_effects,
            activity_builder=lambda arguments: _mcp_activity('mcp_tool_invoke', arguments),
            classifier_input_builder=lambda arguments: _mcp_classifier_input('mcp_tool_invoke', arguments),
            summary_builder=_mcp_result_summary,
            observation_family='mcp',
        ),
        ToolDefinition(
            exposure=ToolExposure.HIDDEN,
            name='mcp_auth',
            kind='mcp_auth',
            description='Authenticate a remote MCP server with an OAuth browser callback flow.',
            detail='Starts a local callback listener, opens the MCP OAuth authorization URL, and stores tokens outside workflow state.',
            search_hint='Authenticate a remote MCP server after it reports needs-auth.',
            search_keywords=('mcp', 'oauth', 'auth', 'remote', 'server'),


            read_only=False,
            concurrency_safe=False,
            permission_class='network',
            invocation_mode='agent_requested',
            result_family='mcp',
            detail_builder=lambda: {
                'default_input_examples': [{'server_name': 'asana'}],
                'result_highlights': ['server_name', 'status', 'token_file'],
            },
            input_schema_builder=lambda: {
                'type': 'object',
                'properties': {'server_name': {'type': 'string'}},
                'required': ['server_name'],
                'additionalProperties': False,
            },
            handler=run_mcp_auth,
            effect_builder=resolve_mcp_effects,
            activity_builder=lambda arguments: _mcp_activity('mcp_auth', arguments),
            classifier_input_builder=lambda arguments: _mcp_classifier_input('mcp_auth', arguments),
            summary_builder=_mcp_result_summary,
            observation_family='mcp',
            requires_network=True,
        ),
        ToolDefinition(
            exposure=ToolExposure.DEFERRED,
            name='paper_fetch',
            kind='research_external',
            description=('Retrieve bounded paper metadata, abstract and text from DOI, arXiv or URL sources. '
                         'Use identifiers supplied by the user or obtained from an identifiable source; '
                         'if uncertain, use web_search to locate them, never guess. '
                         'Use coverage.next_input to continue reading; content_available is not full-paper coverage.'),
            detail=('Reports identity, abstract/body access and coverage separately. '
                    'Use coverage.next_input to continue reading; content_available does not mean the entire paper was read.'),


            read_only=True,
            concurrency_safe=True,
            permission_class='network',
            invocation_mode='agent_requested',
            result_family='research',
            max_prompt_result_chars=28000,
            max_result_size_chars=28000,
            detail_builder=lambda: {
                'default_input_examples': [
                    {'identifier': '10.1038/s41592-026-03029-6'},
                    {'identifier': 'arXiv:2412.15224', 'max_chars': 12000},
                ],
                'result_highlights': [
                    'outcome',
                    'title',
                    'abstract',
                    'passages',
                    'selected_source',
                    'attempted_sources',
                    'open_pdf_url',
                    'identity_status',
                    'content_status',
                    'coverage',
                ],
            },
            input_schema_builder=lambda: {
                'type': 'object',
                'properties': {
                    'identifier': {'type': 'string'},
                    'url': {'type': 'string'},
                    'timeout_seconds': {'type': 'integer', 'minimum': 1, 'maximum': 120,
                                        'description': 'Total retrieval and extraction budget in seconds; default 30. DNS and process cleanup may add overhead.'},
                    'max_chars': {'type': 'integer', 'minimum': 1000, 'maximum': 16000},
                    'start_page': {'type': 'integer', 'minimum': 1, 'maximum': 100000,
                                   'description': 'One-based PDF page to read; default 1.'},
                    'page_offset': {'type': 'integer', 'minimum': 0, 'maximum': 25000000,
                                    'description': 'Character offset within the first requested page or structured HTML body.'},
                },
                'additionalProperties': False,
            },
            effect_builder=resolve_paper_fetch_effects,
            handler=run_paper_fetch,
            observation_family='research',
            requires_network=True,
        ),
        ToolDefinition(
            exposure=ToolExposure.DIRECT,
            name='view_image',
            kind='image_read',
            description='View a local image as provider-native image input.',
            detail=(
                    'Read-only vision helper. Use this when the model needs to inspect actual image content. '
                ),


            read_only=True,
            concurrency_safe=True,
            permission_class='document',
            invocation_mode='agent_requested',
            result_family='image',
            action_primitives=('read',),
            primary_intent='read',
            grounding_lane='grounded',
            input_contract_family='image_path_visual',
            selection_disambiguators=(
                    'Use when the model must inspect image pixels.',
                ),
            detail_builder=lambda: {
                'default_input_examples': [{'path': 'figures/demo.png'}, {'path': 'figures/detail.jpg', 'detail': 'original'}],
                'result_highlights': ['mime_type', 'width', 'height', 'detail'],
            },
            input_schema_builder=lambda: {
                'type': 'object',
                'properties': {
                    'path': {'type': 'string'},
                    'detail': {'type': 'string', 'enum': ['high', 'original']},
                },
                'required': ['path'],
                'additionalProperties': False,
            },
            handler=run_view_image,
            effect_builder=resolve_image_effects,
            observation_family='image',
        ),
    )
