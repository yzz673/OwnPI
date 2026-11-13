from __future__ import annotations

from typing import Any

from ownpi.runtime.contracts import ToolResult
from ownpi.runtime.tools.definitions import ToolExecutionContext
from ownpi.schemas.runtime_human_clarification import (
    HumanClarificationQuestion,
    MAX_HUMAN_CLARIFICATION_QUESTIONS,
    build_human_question_authoring_schema,
    validate_human_question_authoring_batch,
)


def _human_question_input_schema() -> dict[str, Any]:
    return build_human_question_authoring_schema(
        max_questions=MAX_HUMAN_CLARIFICATION_QUESTIONS,
        require_question_id=True,
    )


def _validate_human_question_arguments(
    context: ToolExecutionContext,
    arguments: dict[str, Any],
) -> dict[str, Any] | None:
    default_input_examples = [
        {
            'questions': [
                {
                    'question_id': 'dataset_path',
                    'header': 'Dataset',
                    'question': 'Please provide the dataset path for this run.',
                    'required': True,
                    'multi_select': False,
                    'options': [],
                }
            ]
        }
    ]
    raw_questions = arguments.get('questions')
    if not isinstance(raw_questions, list) or not raw_questions:
        return {
            'error': (
                'questions must contain 1-'
                f'{MAX_HUMAN_CLARIFICATION_QUESTIONS} items for human_question'
            ),
            'compact_hint': 'human_question requires a bounded non-empty questions list.',
            'default_input_examples': default_input_examples,
        }
    if len(raw_questions) > MAX_HUMAN_CLARIFICATION_QUESTIONS:
        return {
            'error': (
                'human_question supports at most '
                f'{MAX_HUMAN_CLARIFICATION_QUESTIONS} questions per invocation'
            ),
            'compact_hint': (
                f'Ask at most {MAX_HUMAN_CLARIFICATION_QUESTIONS} concise questions '
                'in one human_question call.'
            ),
        }
    parsed_questions: list[HumanClarificationQuestion] = []
    for index, item in enumerate(raw_questions, start=1):
        if not isinstance(item, dict):
            return {
                'error': f'questions[{index}] must be an object',
                'compact_hint': 'Each human_question item must be a structured question object.',
            }
        try:
            parsed_questions.append(
                HumanClarificationQuestion.from_wire_strict(
                    item,
                    question_index=index,
                    source=str(context.actor_kind or '').strip(),
                )
            )
        except ValueError as exc:
            return {
                'error': str(exc),
                'compact_hint': 'Each human_question item must match the strict shared authoring schema.',
            }
    validation_error = validate_human_question_authoring_batch(
        parsed_questions,
        max_questions=MAX_HUMAN_CLARIFICATION_QUESTIONS,
        context_label='human_question',
        raw_questions=raw_questions,
    )
    if validation_error is not None:
        return {
            'error': validation_error,
            'compact_hint': (
                'Tool Contract v2: every question needs a stable semantic question_id; '
                'concrete values use options=[]; options must be complete decisions, '
                'question text must be unique, and notes may only add optional rationale.'
            ),
        }
    return None


def _validate_mcp_tool_invoke_args(
    _context: ToolExecutionContext,
    arguments: dict[str, Any],
) -> dict[str, Any] | None:
    tool_name = str(arguments.get('tool_name', '') or '').strip()
    if not tool_name:
        return {
            'error': 'tool_name is required for mcp_tool_invoke',
            'compact_hint': 'mcp_tool_invoke requires a non-empty tool_name.',
            'default_input_examples': [
                {'server_name': 'zotero', 'tool_name': 'search_papers', 'input': {'query': 'EEG'}}
            ],
        }
    raw_arguments = arguments.get('input')
    if raw_arguments is not None and not isinstance(raw_arguments, dict):
        return {
            'error': 'input must be an object for mcp_tool_invoke',
            'compact_hint': 'mcp_tool_invoke input must be a JSON object.',
            'default_input_examples': [
                {'server_name': 'zotero', 'tool_name': 'search_papers', 'input': {'query': 'EEG'}}
            ],
        }
    return None


def _mcp_activity(tool_name: str, arguments: dict[str, Any]) -> str:
    server_name = str(arguments.get('server_name', '') or '').strip()
    invoked_tool_name = str(arguments.get('tool_name', '') or '').strip()
    pieces = [tool_name]
    if server_name:
        pieces.append(f'server={server_name}')
    if str(arguments.get('resource_id', '') or '').strip():
        pieces.append(f"resource={str(arguments.get('resource_id', '') or '').strip()}")
    if invoked_tool_name and tool_name == 'mcp_tool_invoke':
        pieces.append(f'tool={invoked_tool_name}')
    return ' '.join(piece for piece in pieces if piece)


def _mcp_classifier_input(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    payload = {
        'tool_name': tool_name,
        'server_name': str(arguments.get('server_name', '') or '').strip(),
    }
    if 'tool_name' in arguments:
        payload['mcp_tool_name'] = str(arguments.get('tool_name', '') or '').strip()
    if 'resource_id' in arguments:
        payload['resource_id'] = str(arguments.get('resource_id', '') or '').strip()
    if isinstance(arguments.get('input'), dict):
        payload['input_keys'] = sorted(str(key) for key in arguments['input'].keys())
    return payload


def _mcp_result_summary(result: ToolResult) -> str | None:
    data = result.data or {}
    if not isinstance(data, dict):
        return None
    if result.tool_name == 'mcp_tool_list':
        return f"mcp_tool_list server={data.get('server_name', '')} tools={len(data.get('tools', []))}".strip()
    if result.tool_name == 'mcp_tool_invoke':
        return f"mcp_tool_invoke server={data.get('server_name', '')} tool={data.get('tool_name', '')}".strip()
    if result.tool_name == 'mcp_resource_read':
        return f"mcp_resource_read resource_id={data.get('resource_id', '')}".strip()
    return f"mcp_resource_list server={data.get('server_name', '')} resources={len(data.get('resources', []))}".strip()
