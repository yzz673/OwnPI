from __future__ import annotations


from ownpi.runtime.tools.default_helpers import (
    _human_question_input_schema,
    _validate_human_question_arguments,
)
from ownpi.runtime.tools.definitions import ToolDefinition, ToolExposure
from ownpi.schemas.runtime_tooling import ToolCallBatchPolicy
from ownpi.tools.plan_tools import run_update_plan
from ownpi.tools.runtime_task_tools import (
    run_runtime_task_get,
    run_runtime_task_kill,
    run_runtime_task_list,
    run_runtime_task_poll,
)


def build_workflow_tool_definitions() -> tuple[ToolDefinition, ...]:
    return (
        ToolDefinition(
            exposure=ToolExposure.DIRECT,
            name='update_plan',
            kind='plan_update',
            description='Update the task TODO/checklist plan for the current runtime turn.',
            detail=(
                    'Updates the task plan. Provide an optional explanation and a list of plan items, '
                    'each with a step and status. At most one step can be in_progress at a time.'
                ),






            read_only=False,
            concurrency_safe=False,
            permission_class='task',
            invocation_mode='agent_requested',
            working_set_priority='high',
            toolset_family='control',
            backend_kind='synthetic',
            resume_strategy='stateful',
            detail_builder=lambda: {
                'default_input_examples': [
                    {
                        'explanation': 'I inspected the workflow tools and am adding the handler.',
                        'plan': [
                            {'step': 'Inspect existing tool registry', 'status': 'completed'},
                            {'step': 'Add update_plan handler', 'status': 'in_progress'},
                            {'step': 'Run focused tests', 'status': 'pending'},
                        ],
                    }
                ],
                'result_highlights': ['plan_item_count'],
            },
            input_schema_builder=lambda: {
                'type': 'object',
                'properties': {
                    'explanation': {'type': 'string', 'description': 'Optional explanation for this plan update.'},
                    'plan': {
                        'type': 'array',
                        'description': 'The list of steps',
                        'items': {
                            'type': 'object',
                            'properties': {
                                'step': {'type': 'string', 'description': 'Task step text.'},
                                'status': {
                                    'type': 'string',
                                    'enum': ['pending', 'in_progress', 'completed'],
                                    'description': 'Step status.',
                                },
                            },
                            'required': ['step', 'status'],
                            'additionalProperties': False,
                        },
                    },
                },
                'required': ['plan'],
                'additionalProperties': False,
            },
            handler=run_update_plan,
            observation_family='plan_update',
            result_family='plan_update',
            strict_schema=False,
        ),
        ToolDefinition(
            exposure=ToolExposure.DIRECT_MODEL_ONLY,
            name='human_question',
            kind='runtime_control',
            description='Ask the user a bounded set of clarifying questions and pause the current run.',
            detail=(
                    'Use this only when the attempt cannot continue without user input. '
                    'Valid uses are missing user-owned external inputs, permission/approval decisions, '
                    'or task-goal conflicts that the agent cannot resolve from available context. '
                    'Do not use human_question for agent-internal authoring choices, stage artifact '
                    'structure, hypothesis wording, self-check/reviewer strategy, or uncertainty that '
                    'the current stage is responsible for resolving. '
                    'Questions must be concise and directly answerable. '
                    'Every question must include a stable model-authored semantic question_id '
                    'such as dataset_path, credential, or download_policy. '
                    'Tool Contract v2 authoring rules: concrete values such as paths, URLs, IDs, '
                    'tokens, commands, or exact snippets must use options=[]. Decisions should use '
                    '2-4 options whose selected label is sufficient to continue. Notes are optional '
                    'rationale only and must never carry a required value. Use only option label and '
                    'description; preview fields are disabled.'
                ),




            read_only=True,
            concurrency_safe=False,
            batch_policy=ToolCallBatchPolicy.TURN_EXCLUSIVE,
            permission_class='task',
            invocation_mode='agent_requested',
            result_family='human_interaction',
            working_set_priority='high',
            interrupt_behavior='block',

            toolset_family='control',
            backend_kind='synthetic',
            resume_strategy='manual_resume_required',
            schema_cache_variant='tool_contract_v2:human_question:v3',
            detail_builder=lambda: {
                'default_input_examples': [
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
                    },
                    {
                        'questions': [
                            {
                                'question_id': 'download_policy',
                                'header': 'Dataset fix',
                                'question': 'How should I proceed if no local dataset path is available?',
                                'required': True,
                                'multi_select': False,
                                'options': [
                                    {
                                        'label': 'Stop and wait for a local dataset',
                                        'description': 'Pause the run until you have a concrete local dataset path.',
                                    },
                                    {
                                        'label': 'Continue without dataset validation',
                                        'description': 'Proceed only with steps that do not require binding a local dataset path.',
                                    },
                                ],
                            }
                        ]
                    },
                ],
                'result_highlights': ['questions', 'answers', 'interaction_request_id', 'question_key'],
            },
            input_schema_builder=_human_question_input_schema,
            input_validator=_validate_human_question_arguments,
            observation_family='human_question',
        ),
        ToolDefinition(
            exposure=ToolExposure.HIDDEN,
            name='runtime_task_list',
            kind='runtime_task',
            description='List real runtime task lifecycle state for commands and sessions.',
            detail='Runtime task state read capability.',


            read_only=True,
            concurrency_safe=True,
            permission_class='task',
            invocation_mode='agent_requested',
            result_family='runtime_task',
            detail_builder=lambda: {
                'default_input_examples': [{}, {'max_tasks': 12}],
                'result_highlights': ['summary', 'active_runtime_tasks'],
            },
            input_schema_builder=lambda: {
                'type': 'object',
                'properties': {
                    'run_id': {'type': 'string'},
                    'max_tasks': {'type': 'integer', 'minimum': 1, 'maximum': 32},
                },
                'additionalProperties': False,
            },
            handler=run_runtime_task_list,
            observation_family='runtime_task',
        ),
        ToolDefinition(
            exposure=ToolExposure.HIDDEN,
            name='runtime_task_get',
            kind='runtime_task',
            description='Read one real runtime task by task_id.',
            detail='Runtime task state read capability.',


            read_only=True,
            concurrency_safe=True,
            permission_class='task',
            invocation_mode='agent_requested',
            result_family='runtime_task',
            detail_builder=lambda: {
                'default_input_examples': [{'task_id': 'command:cmd-123'}],
                'result_highlights': ['task'],
            },
            input_schema_builder=lambda: {
                'type': 'object',
                'properties': {'task_id': {'type': 'string'}},
                'required': ['task_id'],
                'additionalProperties': False,
            },
            handler=run_runtime_task_get,
            observation_family='runtime_task',
        ),
        ToolDefinition(
            exposure=ToolExposure.HIDDEN,
            name='runtime_task_poll',
            kind='runtime_task',
            description='Read incremental output for a runtime task, optionally waiting for output readiness.',
            detail=(
                    'Runtime task output polling capability. With block=true, waits for a runtime task state '
                    'transition to terminal or stalled; it does not actively poll arbitrary external PIDs/providers.'
                ),


            read_only=False,
            concurrency_safe=False,
            permission_class='task',
            invocation_mode='agent_requested',
            result_family='runtime_task',
            detail_builder=lambda: {
                'default_input_examples': [
                    {'task_id': 'command:cmd-123', 'max_bytes': 4096},
                    {'task_id': 'command:cmd-123', 'block': True, 'timeout_ms': 30000},
                ],
                'result_highlights': ['content', 'next_offset', 'eof', 'timed_out', 'cancelled', 'wait_source'],
            },
            input_schema_builder=lambda: {
                'type': 'object',
                'properties': {
                    'task_id': {'type': 'string'},
                    'offset': {'type': 'integer', 'minimum': 0},
                    'max_bytes': {'type': 'integer', 'minimum': 1, 'maximum': 65536},
                    'advance_offset': {'type': 'boolean'},
                    'block': {'type': 'boolean'},
                    'timeout_ms': {'type': 'integer', 'minimum': 0, 'maximum': 300000},
                    'poll_interval_ms': {'type': 'integer', 'minimum': 10, 'maximum': 5000},
                    'prefer_clean_result': {'type': 'boolean'},
                },
                'required': ['task_id'],
                'additionalProperties': False,
            },
            handler=run_runtime_task_poll,
            observation_family='runtime_task',
        ),
        ToolDefinition(
            exposure=ToolExposure.HIDDEN,
            name='runtime_task_kill',
            kind='runtime_task',
            description='Kill a tracked runtime task process by task_id when it has a registered session_id.',
            detail='Runtime task process cleanup capability.',


            read_only=False,
            concurrency_safe=False,
            permission_class='execution',
            invocation_mode='agent_requested',
            result_family='runtime_task',
            destructive_capable=True,
            detail_builder=lambda: {
                'default_input_examples': [{'task_id': 'command:cmd-123'}],
                'result_highlights': ['killed_process_count', 'task'],
            },
            input_schema_builder=lambda: {
                'type': 'object',
                'properties': {'task_id': {'type': 'string'}, 'force': {'type': 'boolean'}},
                'required': ['task_id'],
                'additionalProperties': False,
            },
            handler=run_runtime_task_kill,
            observation_family='runtime_task',
        ),
    )
