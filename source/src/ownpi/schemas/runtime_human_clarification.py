from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field, replace
from typing import Any

from ownpi.stage_ids import canonical_stage_id


CONTROLLER_CLARIFICATION_HANDOFF_KEY = 'controller_clarification_handoff'
CONTROLLER_CLARIFICATION_HANDOFF_INSTRUCTION = (
    'Use this answer first. If it is still insufficient, ask only for the remaining '
    'missing field(s) and do not repeat the previous clarification verbatim.'
)
MAX_HUMAN_CLARIFICATION_QUESTIONS = 3
MAX_HUMAN_QUESTION_OPTIONS = 4
MIN_HUMAN_QUESTION_OPTIONS = 2

_CONCRETE_VALUE_TERMS = (
    ' path',
    '路径',
    'file',
    '文件',
    'dataset',
    '数据集',
    'pdf',
    'repo',
    'repository',
    'url',
    'uri',
    ' id',
    'id ',
    '_id',
    '-id',
    'identifier',
    'token',
    'credential',
    'api key',
    '凭证',
    '授权',
    'command',
    '命令',
    'value',
    '值',
    'snippet',
    '片段',
)
_CONCRETE_VALUE_ACTIONS = (
    'provide',
    'enter',
    'paste',
    'type',
    'specify',
    'input',
    '填写',
    '输入',
    '提供',
    '粘贴',
    '写入',
)
_NOTE_DEPENDENCY_TERMS = (
    'note',
    'notes',
    'optional note',
    'add details',
    'free text',
    '备注',
    '说明',
    '补充',
)
_INTERNAL_DECISION_TERMS = (
    'artifact',
    'hypothesis',
    'hypotheses',
    'secondary hypothesis',
    'secondary hypotheses',
    'decision signal',
    'falsification verdict',
    'falsification_verdict',
    'self-check',
    'self check',
    'reviewer',
    'validator',
    'gate',
    'stage output',
    'stage artifact',
    'prompt',
    'rubric',
)
_INTERNAL_DECISION_ACTIONS = (
    'should i',
    'should the agent',
    'should the hypothesis',
    'should the research_contract',
    'should the artifact',
    'should include',
    'should we include',
    'do you want me to include',
    'include explicit',
    'keep existing structure',
)


def build_human_question_authoring_schema(
    *,
    max_questions: int,
    require_question_id: bool,
) -> dict[str, Any]:
    """Build the shared human-question-shaped authoring schema.

    ``question_id`` remains required for ABI compatibility, but the durable
    answer key is canonicalized from visible header/question semantics when a
    known semantic field is present. This prevents arbitrary model-authored
    slugs from owning answer identity.
    """

    option_schema = {
        'type': 'object',
        'properties': {
            'label': {'type': 'string'},
            'description': {'type': 'string'},
        },
        'required': ['label', 'description'],
        'additionalProperties': False,
    }
    required_fields = ['header', 'question', 'required', 'multi_select', 'options']
    if require_question_id:
        required_fields.insert(0, 'question_id')
    question_schema = {
        'type': 'object',
        'properties': {
            'question_id': {'type': 'string'},
            'header': {'type': 'string'},
            'question': {'type': 'string'},
            'required': {'type': 'boolean'},
            'multi_select': {'type': 'boolean'},
            'options': {
                'type': 'array',
                'items': option_schema,
                'maxItems': MAX_HUMAN_QUESTION_OPTIONS,
            },
        },
        'required': required_fields,
        'additionalProperties': False,
    }
    return {
        'type': 'object',
        'properties': {
            'questions': {
                'type': 'array',
                'items': question_schema,
                'minItems': 1,
                'maxItems': int(max_questions or 1),
            }
        },
        'required': ['questions'],
        'additionalProperties': False,
    }


def slugify_question_identifier(value: str) -> str:
    """Return a stable identifier slug from model-authored text."""

    cleaned = ''.join(char if char.isalnum() else '_' for char in str(value).strip().lower())
    return cleaned.strip('_')


_GENERIC_MODEL_QUESTION_ID_RE = re.compile(r'^(?:clarification|missing_input|question|resource)(?:_\d+)?$')
_PLACEHOLDER_MODEL_QUESTION_IDS = {'foo', 'bar', 'baz', 'test', 'todo', 'tbd', 'input'}
_MAX_QUESTION_ID_CHARS = 80


def _is_generic_model_question_id(question_id_key: str) -> bool:
    return bool(_GENERIC_MODEL_QUESTION_ID_RE.fullmatch(question_id_key))


def _is_placeholder_model_question_id(question_id_key: str) -> bool:
    return question_id_key in _PLACEHOLDER_MODEL_QUESTION_IDS


def _is_numeric_model_question_id(question_id_key: str) -> bool:
    return bool(question_id_key) and question_id_key.replace('_', '').isdigit()


def _is_overlong_model_question_id(question_id_key: str) -> bool:
    return len(str(question_id_key or '')) > _MAX_QUESTION_ID_CHARS


def _validate_raw_human_question_authoring_item(raw_item: Any, *, question_index: int) -> str | None:
    if not isinstance(raw_item, dict):
        return f'questions[{question_index}] must be an object'
    allowed_question_fields = {'question_id', 'header', 'question', 'required', 'multi_select', 'options'}
    unexpected_question_fields = sorted(str(key) for key in raw_item.keys() if str(key) not in allowed_question_fields)
    if unexpected_question_fields:
        return (
            f'questions[{question_index}] has unsupported field: '
            f'{unexpected_question_fields[0]}'
        )
    for field_name in ('question_id', 'header', 'question'):
        if field_name not in raw_item or raw_item.get(field_name) is None:
            if field_name == 'question_id':
                return f'questions[{question_index}].question_id is required'
            return f'questions[{question_index}].{field_name} is required'
        if not isinstance(raw_item.get(field_name), str):
            if field_name == 'question_id':
                return f'questions[{question_index}].question_id must be a string semantic slug'
            return f'questions[{question_index}].{field_name} must be a string'
        if not str(raw_item.get(field_name) or '').strip():
            if field_name == 'question_id':
                return f'questions[{question_index}].question_id is required'
            return f'questions[{question_index}].{field_name} must be non-empty'
    for field_name in ('required', 'multi_select'):
        if field_name not in raw_item:
            return f'questions[{question_index}].{field_name} is required'
        if not isinstance(raw_item.get(field_name), bool):
            return f'questions[{question_index}].{field_name} must be a boolean'
    if 'options' not in raw_item:
        return f'questions[{question_index}].options is required'
    raw_options = raw_item.get('options')
    if not isinstance(raw_options, list):
        return f'questions[{question_index}].options must be a list'
    for option_index, raw_option in enumerate(raw_options, start=1):
        if not isinstance(raw_option, dict):
            return f'questions[{question_index}].options[{option_index}] must be an object'
        if 'preview' in raw_option:
            return f'questions[{question_index}].options[{option_index}] must not include preview'
        allowed_option_fields = {'label', 'description'}
        unexpected_option_fields = sorted(str(key) for key in raw_option.keys() if str(key) not in allowed_option_fields)
        if unexpected_option_fields:
            return (
                f'questions[{question_index}].options[{option_index}] has unsupported field: '
                f'{unexpected_option_fields[0]}'
            )
        for field_name in ('label', 'description'):
            if field_name not in raw_option or raw_option.get(field_name) is None:
                return f'questions[{question_index}].options[{option_index}].{field_name} is required'
            if not isinstance(raw_option.get(field_name), str):
                return f'questions[{question_index}].options[{option_index}].{field_name} must be a string'
            if not str(raw_option.get(field_name) or '').strip():
                return f'questions[{question_index}].options[{option_index}].{field_name} must be non-empty'
    return None


def normalize_model_authored_question_ids(
    questions: list['HumanClarificationQuestion'] | tuple['HumanClarificationQuestion', ...],
) -> list['HumanClarificationQuestion']:
    """Slug-normalize model-authored question IDs without inventing semantic IDs."""

    normalized: list[HumanClarificationQuestion] = []
    for question in questions:
        normalized.append(
            replace(
                question,
                question_id=slugify_question_identifier(question.question_id),
            )
        )
    return normalized


def infer_question_id_from_text(
    text: str,
    *,
    fallback_index: int,
    default_prefix: str = 'question',
) -> str:
    """Infer a semantic question id from visible question/header text."""

    semantic_id = infer_semantic_question_id_from_text(text)
    if semantic_id:
        return semantic_id
    return f'{slugify_question_identifier(default_prefix) or "question"}_{fallback_index}'


def infer_semantic_question_id_from_text(text: str) -> str | None:
    """Infer a known semantic id from visible text without inventing a fallback id."""

    normalized = str(text).strip()
    lowered = normalized.casefold()
    if 'pdf path' in lowered or 'pdf路径' in normalized or 'pdf 路径' in normalized:
        return 'pdf_path'
    dataset_mentions_path = (
        'dataset path' in lowered
        or 'data path' in lowered
        or 'dataset location' in lowered
        or 'data location' in lowered
        or 'dataset directory' in lowered
        or 'data directory' in lowered
        or ('dataset' in lowered and any(term in lowered for term in ('path', 'location', 'filesystem', 'folder', 'directory')))
        or ('data' in lowered and any(term in lowered for term in ('path', 'location', 'filesystem', 'folder', 'directory')))
        or '数据集路径' in normalized
        or '数据路径' in normalized
        or ('数据集' in normalized and '路径' in normalized)
    )
    if dataset_mentions_path and (
        'backup' in lowered
        or 'secondary' in lowered
        or 'fallback' in lowered
        or '备用' in normalized
        or '备选' in normalized
    ):
        return 'backup_dataset_path'
    if dataset_mentions_path:
        return 'dataset_path'
    if (
        ('dataset' in lowered or '数据集' in normalized)
        and any(term in lowered for term in ('which', 'choose', 'select', 'use'))
    ):
        return 'dataset_choice'
    if (
        'repo path' in lowered
        or 'repository path' in lowered
        or '仓库路径' in normalized
        or '代码库路径' in normalized
    ):
        return 'repo_path'
    if (
        'credential' in lowered
        or 'api key' in lowered
        or 'token' in lowered
        or '凭证' in normalized
        or '授权' in normalized
    ):
        return 'credential'
    if 'corpus description' in lowered or '语料' in normalized:
        return 'corpus_description'
    if 'paper title' in lowered or '论文标题' in normalized:
        return 'paper_title'
    if (
        'missing file' in lowered
        or 'file path' in lowered
        or 'path does not exist' in lowered
        or '文件路径' in normalized
        or '路径不存在' in normalized
        or ('文件' in normalized and '路径' in normalized)
    ):
        return 'file_path'
    if (
        'remote artifact' in lowered
        or 'artifact root' in lowered
        or 'artifact path' in lowered
        or 'artifact retrieval' in lowered
        or ('remote' in lowered and 'retrieval command' in lowered)
        or ('remote' in lowered and 'artifact' in lowered)
    ):
        return 'remote_artifact_access'
    return None


def _canonical_model_question_id_from_wire(
    *,
    proposed_question_id: str,
    header: str,
    question: str,
) -> str:
    """Canonicalize a model-authored id against visible question semantics.

    The model still provides ``question_id`` for ABI compatibility, but it no
    longer has sole authority over the durable answer key when the visible
    header/question text clearly names a known semantic field.
    """

    proposed = slugify_question_identifier(proposed_question_id)
    if (
        not proposed
        or _is_numeric_model_question_id(proposed)
        or _is_generic_model_question_id(proposed)
        or _is_overlong_model_question_id(proposed)
    ):
        derived = infer_semantic_question_id_from_text(f'{header} {question}')
        if derived:
            return derived
        if proposed:
            return _stable_visible_question_id(header=header, question=question)
        return proposed
    derived = infer_semantic_question_id_from_text(f'{header} {question}')
    if derived:
        if _is_placeholder_model_question_id(proposed):
            return derived
        if _question_id_semantically_compatible(proposed, derived):
            return proposed
        return derived
    if not _is_placeholder_model_question_id(proposed):
        return proposed
    return _stable_visible_question_id(header=header, question=question)


def _stable_visible_question_id(*, header: str, question: str) -> str:
    visible_text = f'{str(header or "").strip()}\n{str(question or "").strip()}'.strip()
    digest = hashlib.sha256(visible_text.encode('utf-8')).hexdigest()[:10]
    prefix = slugify_question_identifier(str(header or '').strip()) or 'question'
    prefix_tokens = [token for token in prefix.split('_') if token and token not in {'question', 'clarification'}]
    stable_prefix = '_'.join(prefix_tokens[:2]) or 'question'
    return f'{stable_prefix}_{digest}'


def _question_id_semantically_compatible(proposed: str, derived: str) -> bool:
    proposed_tokens = {token for token in proposed.split('_') if token}
    derived_tokens = {token for token in derived.split('_') if token}
    compatibility_aliases = {
        'backup': {'backup', 'secondary', 'fallback', 'alternate', 'alternative'},
        'dataset': {'data', 'dataset', 'corpus'},
        'path': {'path', 'location', 'filepath', 'file', 'folder', 'directory'},
        'repo': {'repo', 'repository', 'codebase'},
        'credential': {'credential', 'credentials', 'token', 'auth', 'key', 'secret', 'api'},
        'pdf': {'pdf'},
        'file': {'file', 'path', 'filepath'},
        'paper': {'paper', 'title'},
        'title': {'title', 'paper'},
        'corpus': {'corpus', 'dataset', 'data'},
        'description': {'description', 'desc', 'summary', 'context'},
        'choice': {'choice', 'choose', 'selection', 'select', 'selected'},
    }
    for token in derived_tokens:
        accepted = {token}
        accepted.update(compatibility_aliases.get(token, set()))
        if not proposed_tokens & accepted:
            return False
    return True


def _read_continuation_index(data: dict[str, Any]) -> int:
    if data.get('continuation_index') is not None:
        return int(data.get('continuation_index', 0) or 0)
    return 0


def _read_query_chain_id(data: dict[str, Any]) -> str:
    if data.get('query_chain_id') is not None:
        return str(data.get('query_chain_id', '') or '').strip()
    return ''


def _read_query_chain_depth(data: dict[str, Any]) -> int:
    if data.get('query_chain_depth') is not None:
        return int(data.get('query_chain_depth', 0) or 0)
    return 0


def _coerce_string_list(value: Any) -> list[str]:
    if not isinstance(value, (list, tuple)):
        return []
    items: list[str] = []
    seen: set[str] = set()
    for item in value:
        normalized = str(item).strip()
        if not normalized:
            continue
        lowered = normalized.casefold()
        if lowered in seen:
            continue
        seen.add(lowered)
        items.append(normalized)
    return items


def _contains_any(text: str, terms: tuple[str, ...]) -> bool:
    lowered = text.casefold()
    return any(term.casefold() in lowered for term in terms)


def _starts_with_action(text: str) -> bool:
    stripped = text.strip().casefold()
    return any(stripped.startswith(action.casefold()) for action in _CONCRETE_VALUE_ACTIONS)


def human_question_contract_v2_violation(
    question: 'HumanClarificationQuestion',
    *,
    question_index: int,
) -> str | None:
    """Return a Tool Contract v2 authoring violation message, if any.

    The wire shape stays unchanged. This helper only tightens the authoring
    semantics shared by tool-origin ``human_question`` and controller-origin
    ``human_clarification``:
    concrete values use free text, decisions use complete options, notes are
    optional rationale only, and previews are disabled.
    """

    combined_question = f'{question.header} {question.question} {question.question_id}'.casefold()
    option_text = ' '.join(f'{option.label} {option.description}' for option in question.options).casefold()
    combined = f'{combined_question} {option_text}'
    if _contains_any(combined, _INTERNAL_DECISION_TERMS) and _contains_any(combined, _INTERNAL_DECISION_ACTIONS):
        return (
            f'questions[{question_index}] asks the user to decide an agent-internal authoring, '
            'review, or artifact-structure choice; human_question is only for missing '
            'user-owned external inputs, permissions, or task-goal conflicts'
        )
    for option_index, option in enumerate(question.options, start=1):
        if str(option.preview or '').strip():
            return (
                f'questions[{question_index}].options[{option_index}] must not include preview; '
                'preview fields are disabled for human_question'
            )
        label = str(option.label or '').strip()
        description = str(option.description or '').strip()
        combined = f'{question.question} {label} {description}'
        if not _contains_any(combined, _CONCRETE_VALUE_TERMS):
            continue
        label_requests_value = _starts_with_action(label) or (
            _contains_any(label, _CONCRETE_VALUE_ACTIONS)
            and _contains_any(label, _CONCRETE_VALUE_TERMS)
        )
        description_depends_on_note = (
            _contains_any(description, _NOTE_DEPENDENCY_TERMS)
            and _contains_any(description, _CONCRETE_VALUE_TERMS)
        )
        if label_requests_value or description_depends_on_note:
            return (
                f'questions[{question_index}].options[{option_index}] encodes a required concrete value; '
                'use options=[] for free-text values and reserve options for complete decisions'
            )
    return None


def validate_human_question_authoring_question(
    question: 'HumanClarificationQuestion',
    *,
    question_index: int,
) -> str | None:
    """Validate one human-question-shaped item against Tool Contract v2."""

    if not str(question.header).strip() or not str(question.question).strip():
        return f'questions[{question_index}] must include non-empty header and question fields'
    option_count = len(question.options)
    if option_count != 0 and not (
        MIN_HUMAN_QUESTION_OPTIONS
        <= option_count
        <= MAX_HUMAN_QUESTION_OPTIONS
    ):
        return (
            f'questions[{question_index}].options must contain either 0 or '
            f'{MIN_HUMAN_QUESTION_OPTIONS}-{MAX_HUMAN_QUESTION_OPTIONS} options'
        )
    seen_labels: set[str] = set()
    for option_index, option in enumerate(question.options, start=1):
        label = str(option.label).strip()
        description = str(option.description).strip()
        if not label or not description:
            return f'questions[{question_index}].options[{option_index}] requires non-empty label and description'
        label_key = label.casefold()
        if label_key in seen_labels:
            return f'duplicate option label in question {question.question_id}: {label}'
        seen_labels.add(label_key)
    return human_question_contract_v2_violation(question, question_index=question_index)


def validate_human_question_authoring_batch(
    questions: list['HumanClarificationQuestion'] | tuple['HumanClarificationQuestion', ...],
    *,
    max_questions: int,
    context_label: str,
    raw_questions: list[Any] | tuple[Any, ...] | None = None,
) -> str | None:
    """Validate human-question-shaped batches shared by tool and controller paths."""

    if not questions:
        return f'{context_label} must include non-empty questions'
    if len(questions) > int(max_questions or 1):
        return f'{context_label} supports at most {int(max_questions or 1)} questions'
    seen_question_ids: set[str] = set()
    seen_question_texts: set[str] = set()
    for index, question in enumerate(questions, start=1):
        raw_item = raw_questions[index - 1] if raw_questions is not None and index <= len(raw_questions) else None
        if raw_questions is not None:
            raw_error = _validate_raw_human_question_authoring_item(raw_item, question_index=index)
            if raw_error is not None:
                return raw_error
            raw_question_id = str(raw_item.get('question_id', '')).strip() if isinstance(raw_item, dict) else ''
            raw_question_id_key = slugify_question_identifier(raw_question_id).casefold()
            if _is_numeric_model_question_id(raw_question_id_key):
                return f'questions[{index}].question_id must be a semantic slug: {raw_question_id}'
            if _is_generic_model_question_id(raw_question_id_key):
                return f'questions[{index}].question_id is too generic: {raw_question_id}'
        question_id = str(question.question_id).strip()
        if not question_id:
            return f'questions[{index}].question_id is required'
        question_id_key = slugify_question_identifier(question_id).casefold()
        if not question_id_key:
            return f'questions[{index}].question_id is required'
        if _is_numeric_model_question_id(question_id_key):
            return f'questions[{index}].question_id must be a semantic slug: {question_id}'
        if _is_generic_model_question_id(question_id_key):
            return f'questions[{index}].question_id is too generic: {question_id}'
        if _is_overlong_model_question_id(question_id_key):
            return (
                f'questions[{index}].question_id is too long: '
                f'{len(question_id_key)} chars, max {_MAX_QUESTION_ID_CHARS}'
            )
        question_text = str(question.question).strip()
        question_text_key = question_text.casefold()
        if question_text_key and question_text_key in seen_question_texts:
            return f'duplicate question text in {context_label}: {question_text}'
        if question_text_key:
            seen_question_texts.add(question_text_key)
        if question_id_key in seen_question_ids:
            return f'duplicate question_id in {context_label}: {question_id}'
        seen_question_ids.add(question_id_key)
        validation_error = validate_human_question_authoring_question(
            question,
            question_index=index,
        )
        if validation_error is not None:
            return validation_error
    return None


@dataclass(frozen=True)
class HumanQuestionOption:
    label: str
    description: str
    preview: str | None = None

    @classmethod
    def from_wire_strict(
        cls,
        data: dict[str, Any],
        *,
        question_index: int,
        option_index: int,
    ) -> 'HumanQuestionOption':
        if not isinstance(data, dict):
            raise ValueError(f'questions[{question_index}].options[{option_index}] must be an object')
        if 'preview' in data:
            raise ValueError(f'questions[{question_index}].options[{option_index}] must not include preview')
        allowed_fields = {'label', 'description'}
        unexpected_fields = sorted(str(key) for key in data.keys() if str(key) not in allowed_fields)
        if unexpected_fields:
            raise ValueError(
                f'questions[{question_index}].options[{option_index}] has unsupported field: '
                f'{unexpected_fields[0]}'
            )
        for field_name in ('label', 'description'):
            if field_name not in data or data.get(field_name) is None:
                raise ValueError(f'questions[{question_index}].options[{option_index}].{field_name} is required')
            if not isinstance(data.get(field_name), str):
                raise ValueError(f'questions[{question_index}].options[{option_index}].{field_name} must be a string')
            if not str(data.get(field_name) or '').strip():
                raise ValueError(f'questions[{question_index}].options[{option_index}].{field_name} must be non-empty')
        return cls(
            label=str(data.get('label', '')).strip(),
            description=str(data.get('description', '')).strip(),
        )

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> 'HumanQuestionOption':
        if not isinstance(data, dict):
            raise ValueError('human clarification option must be an object')
        allowed_fields = {'label', 'description', 'preview'}
        unexpected_fields = sorted(
            str(key) for key in data if str(key) not in allowed_fields
        )
        if unexpected_fields:
            raise ValueError(
                'human clarification option has unsupported field: '
                f'{unexpected_fields[0]}'
            )
        for field_name in ('label', 'description'):
            value = data.get(field_name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(
                    f'human clarification option {field_name} must be a non-empty string'
                )
        preview = data.get('preview')
        if preview is not None and (
            not isinstance(preview, str) or not preview.strip()
        ):
            raise ValueError(
                'human clarification option preview must be a non-empty string when set'
            )
        return cls(
            label=data['label'].strip(),
            description=data['description'].strip(),
            preview=preview.strip() if isinstance(preview, str) else None,
        )

    def to_dict(self) -> dict[str, Any]:
        payload = {
            'label': self.label,
            'description': self.description,
        }
        preview = str(self.preview or '').strip()
        if preview:
            payload['preview'] = preview
        return payload


@dataclass(frozen=True)
class HumanClarificationQuestion:
    question_id: str
    header: str
    question: str
    required: bool = True
    multi_select: bool = False
    options: tuple[HumanQuestionOption, ...] = ()
    source: str = 'controller'
    fallback_generated: bool = False

    @property
    def option_labels(self) -> tuple[str, ...]:
        return tuple(item.label for item in self.options if str(item.label).strip())

    @classmethod
    def from_wire_strict(
        cls,
        data: dict[str, Any],
        *,
        question_index: int,
        source: str = 'controller',
    ) -> 'HumanClarificationQuestion':
        raw_error = _validate_raw_human_question_authoring_item(data, question_index=question_index)
        if raw_error is not None:
            raise ValueError(raw_error)
        options = tuple(
            HumanQuestionOption.from_wire_strict(
                item,
                question_index=question_index,
                option_index=option_index,
            )
            for option_index, item in enumerate(data.get('options', []), start=1)
        )
        return cls(
            question_id=_canonical_model_question_id_from_wire(
                proposed_question_id=str(data.get('question_id', '')).strip(),
                header=str(data.get('header', '')).strip(),
                question=str(data.get('question', '')).strip(),
            ),
            header=str(data.get('header', '')).strip(),
            question=str(data.get('question', '')).strip(),
            required=bool(data.get('required')),
            multi_select=bool(data.get('multi_select')),
            options=options,
            source=source,
            fallback_generated=False,
        )

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> 'HumanClarificationQuestion':
        if not isinstance(data, dict):
            raise ValueError('human clarification question must be an object')
        allowed_fields = {
            'question_id',
            'header',
            'question',
            'required',
            'multi_select',
            'options',
            'source',
            'fallback_generated',
            'question_key',
        }
        unexpected_fields = sorted(
            str(key) for key in data if str(key) not in allowed_fields
        )
        if unexpected_fields:
            raise ValueError(
                'human clarification question has unsupported field: '
                f'{unexpected_fields[0]}'
            )
        for field_name in ('question_id', 'header', 'question', 'source'):
            value = data.get(field_name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(
                    f'human clarification question {field_name} must be a non-empty string'
                )
        for field_name in ('required', 'multi_select'):
            if not isinstance(data.get(field_name), bool):
                raise ValueError(
                    f'human clarification question {field_name} must be a boolean'
                )
        fallback_generated = data.get('fallback_generated', False)
        if not isinstance(fallback_generated, bool):
            raise ValueError(
                'human clarification question fallback_generated must be a boolean'
            )
        raw_options = data.get('options')
        if not isinstance(raw_options, list):
            raise ValueError('human clarification question options must be an array')
        question_key = data.get('question_key')
        if question_key is not None and (
            not isinstance(question_key, str) or not question_key.strip()
        ):
            raise ValueError(
                'human clarification question question_key must be a non-empty string'
            )
        return cls(
            question_id=data['question_id'].strip(),
            header=data['header'].strip(),
            question=data['question'].strip(),
            required=data['required'],
            multi_select=data['multi_select'],
            options=tuple(HumanQuestionOption.from_dict(item) for item in raw_options),
            source=data['source'].strip(),
            fallback_generated=fallback_generated,
        )

    def to_dict(self) -> dict[str, Any]:
        payload = {
            'question_id': self.question_id,
            'header': self.header,
            'question': self.question,
            'required': self.required,
            'multi_select': self.multi_select,
            'options': [item.to_dict() for item in self.options],
            'source': self.source,
        }
        if self.fallback_generated:
            payload['fallback_generated'] = True
        return payload


@dataclass(frozen=True)
class HumanClarificationSession:
    session_id: str
    stage: str
    attempt: int
    query_chain_id: str = ''
    query_chain_depth: int = 0
    continuation_index: int = 0
    actor_kind: str = 'core'
    actor_id: str | None = None
    conversation_lane: str = ''
    critic_kind: str = 'none'
    critic_round: int = 0
    critic_phase: str = 'idle'
    continuation_kind: str = 'stage_attempt_reproduce'
    status: str = 'pending'
    questions: tuple[HumanClarificationQuestion, ...] = ()
    answers: dict[str, Any] = field(default_factory=dict)
    reason: str = ''
    source: str = 'controller'
    origin_kind: str = 'controller'
    interaction_request_id: str = ''
    tool_name: str | None = None
    tool_request_id: str | None = None
    created_at: str | None = None
    answered_at: str | None = None
    resumed_at: str | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> 'HumanClarificationSession':
        interaction_request_id = str(data.get('interaction_request_id', '') or '').strip()
        tool_request_id = (
            str(data['tool_request_id']).strip()
            if data.get('tool_request_id') is not None
            else None
        )
        session_id = str(data.get('session_id', '')).strip()
        origin_kind = str(data.get('origin_kind', 'controller') or 'controller')
        if not interaction_request_id:
            interaction_request_id = tool_request_id or session_id
        raw_questions = data.get('questions')
        if not isinstance(raw_questions, list):
            raise ValueError('human clarification session questions must be an array')
        questions = tuple(
            HumanClarificationQuestion.from_dict(item) for item in raw_questions
        )
        for raw_question, question in zip(raw_questions, questions, strict=True):
            question_key = raw_question.get('question_key')
            if question_key is None:
                continue
            expected_question_key = build_question_key(
                interaction_request_id,
                question.question_id,
            )
            if question_key != expected_question_key:
                raise ValueError(
                    'human clarification question_key does not match current identity'
                )
        return cls(
            session_id=session_id,
            stage=canonical_stage_id(data.get('stage', '')).strip(),
            attempt=int(data.get('attempt', 0) or 0),
            query_chain_id=_read_query_chain_id(data),
            query_chain_depth=_read_query_chain_depth(data),
            continuation_index=_read_continuation_index(data),
            actor_kind=str(data.get('actor_kind', 'core') or 'core'),
            actor_id=(str(data['actor_id']) if data.get('actor_id') is not None else None),
            conversation_lane=str(data.get('conversation_lane', '') or ''),
            critic_kind=str(data.get('critic_kind', 'none') or 'none'),
            critic_round=int(data.get('critic_round', 0) or 0),
            critic_phase=str(data.get('critic_phase', 'idle') or 'idle'),
            continuation_kind=str(data.get('continuation_kind', 'stage_attempt_reproduce') or 'stage_attempt_reproduce'),
            status=str(data.get('status', 'pending') or 'pending'),
            questions=questions,
            answers=normalize_human_answers_mapping(data.get('answers')),
            reason=str(data.get('reason', '') or ''),
            source=str(data.get('source', 'controller') or 'controller'),
            origin_kind=origin_kind,
            interaction_request_id=interaction_request_id,
            tool_name=(str(data['tool_name']) if data.get('tool_name') is not None else None),
            tool_request_id=tool_request_id,
            created_at=(str(data['created_at']) if data.get('created_at') is not None else None),
            answered_at=(str(data['answered_at']) if data.get('answered_at') is not None else None),
            resumed_at=(str(data['resumed_at']) if data.get('resumed_at') is not None else None),
        )

    def to_dict(self) -> dict[str, Any]:
        interaction_request_id = self.interaction_request_id or self.tool_request_id or self.session_id
        return {
            'session_id': self.session_id,
            'stage': self.stage,
            'attempt': self.attempt,
            'query_chain_id': self.query_chain_id,
            'query_chain_depth': self.query_chain_depth,
            'continuation_index': self.continuation_index,
            'actor_kind': self.actor_kind,
            'actor_id': self.actor_id,
            'conversation_lane': self.conversation_lane,
            'critic_kind': self.critic_kind,
            'critic_round': self.critic_round,
            'critic_phase': self.critic_phase,
            'continuation_kind': self.continuation_kind,
            'status': self.status,
            'questions': [
                {
                    **item.to_dict(),
                    'question_key': build_question_key(interaction_request_id, item.question_id),
                }
                for item in self.questions
            ],
            'answers': normalize_human_answers_mapping(self.answers),
            'reason': self.reason,
            'source': self.source,
            'origin_kind': self.origin_kind,
            'interaction_request_id': interaction_request_id,
            'tool_name': self.tool_name,
            'tool_request_id': self.tool_request_id,
            'created_at': self.created_at,
            'answered_at': self.answered_at,
            'resumed_at': self.resumed_at,
        }


def normalize_human_answer_payload(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        selected_labels = _coerce_string_list(value.get('selected_labels'))
        free_text = value.get('free_text')
        normalized_free_text = str(free_text).strip() if free_text is not None else ''
        payload = {
            'selected_labels': selected_labels,
            'free_text': normalized_free_text or None,
        }
        for key, item in value.items():
            normalized_key = str(key).strip()
            if normalized_key in {'selected_labels', 'free_text'} or not normalized_key:
                continue
            payload[normalized_key] = item
        return payload
    raw = str(value).strip()
    return {
        'selected_labels': [],
        'free_text': raw or None,
    }


def normalize_human_answers_mapping(value: Any) -> dict[str, dict[str, Any]]:
    if not isinstance(value, dict):
        return {}
    return {
        str(key): normalize_human_answer_payload(item)
        for key, item in value.items()
        if str(key).strip()
    }


def human_answer_has_value(value: Any) -> bool:
    payload = normalize_human_answer_payload(value)
    return bool(payload['selected_labels'] or payload['free_text'])


def human_answer_text(value: Any) -> str:
    payload = normalize_human_answer_payload(value)
    free_text = str(payload.get('free_text') or '').strip()
    labels = [str(item).strip() for item in payload.get('selected_labels', []) if str(item).strip()]
    if labels and free_text:
        return f"{', '.join(labels)} — {free_text}"
    if free_text:
        return free_text
    return ', '.join(labels)


def build_question_key(interaction_request_id: str, question_id: str) -> str:
    return f"{str(interaction_request_id or '').strip()}:{str(question_id or '').strip()}"


def selected_preview_for_answer(
    *,
    question: HumanClarificationQuestion,
    answer_payload: dict[str, Any] | Any,
) -> str | None:
    payload = normalize_human_answer_payload(answer_payload)
    selected = [str(item).strip() for item in payload.get('selected_labels', []) if str(item).strip()]
    if len(selected) != 1:
        return None
    target = selected[0].casefold()
    for option in question.options:
        if str(option.label).strip().casefold() == target:
            preview = str(option.preview or '').strip()
            return preview or None
    return None


def normalize_clarification_text_key(value: Any) -> str:
    return ' '.join(str(value or '').strip().split()).casefold()


def human_clarification_question_signature(
    questions: tuple[HumanClarificationQuestion, ...] | list[HumanClarificationQuestion] | tuple[dict[str, Any], ...] | list[dict[str, Any]],
) -> tuple[str, ...]:
    normalized: list[str] = []
    for item in questions:
        question = ''
        if isinstance(item, HumanClarificationQuestion):
            question = item.question
        elif isinstance(item, dict):
            question = str(item.get('question', '') or '')
        key = normalize_clarification_text_key(question)
        if key:
            normalized.append(key)
    return tuple(normalized)


def human_clarification_answer_summary(
    session: HumanClarificationSession | dict[str, Any],
) -> str:
    if isinstance(session, HumanClarificationSession):
        questions = session.questions
        answers = session.answers
    else:
        answers = session.get('answers', {}) if isinstance(session.get('answers'), dict) else {}
        questions = [
            HumanClarificationQuestion.from_dict(item)
            for item in session.get('questions', [])
            if isinstance(item, dict)
        ]
    parts: list[str] = []
    for question in questions:
        answer_text = human_answer_text(answers.get(question.question_id))
        if not answer_text:
            continue
        parts.append(f'{question.question} => {answer_text}')
    return '; '.join(parts)


def build_controller_clarification_handoff(
    session: HumanClarificationSession,
    *,
    answer_artifact_path: str | None = None,
) -> dict[str, Any]:
    question_answer_pairs: list[dict[str, Any]] = []
    for question in session.questions:
        answer_text = human_answer_text(session.answers.get(question.question_id))
        if not answer_text:
            continue
        question_answer_pairs.append(
            {
                'question_id': question.question_id,
                'question': question.question,
                'answer_text': answer_text,
            }
        )
    return {
        'status': 'active',
        'session_id': session.session_id,
        'stage': session.stage,
        'attempt': int(session.attempt or 0),
        'query_chain_id': session.query_chain_id,
        'query_chain_depth': int(session.query_chain_depth or 0),
        'continuation_index': int(session.continuation_index or 0),
        'continuation_kind': session.continuation_kind,
        'origin_kind': session.origin_kind,
        'reason': session.reason,
        'answered_at': session.answered_at,
        'same_boundary': True,
        'instruction': CONTROLLER_CLARIFICATION_HANDOFF_INSTRUCTION,
        'answer_text': human_clarification_answer_summary(session),
        'question_answer_pairs': question_answer_pairs,
        'answer_artifact_path': answer_artifact_path,
        'session': session.to_dict(),
    }


def active_controller_clarification_handoff(
    payload: Any,
    *,
    stage: str | None = None,
    attempt: int | None = None,
) -> dict[str, Any] | None:
    if not isinstance(payload, dict):
        return None
    if str(payload.get('status', 'active') or 'active') != 'active':
        return None
    if str(payload.get('origin_kind', 'controller') or 'controller') != 'controller':
        return None
    if stage is not None and str(payload.get('stage', '') or '') != str(stage):
        return None
    if attempt is not None and int(payload.get('attempt', 0) or 0) != int(attempt):
        return None
    return dict(payload)


def controller_clarification_handoff_session(payload: Any) -> HumanClarificationSession | None:
    active = active_controller_clarification_handoff(payload)
    if active is None:
        return None
    session_payload = active.get('session')
    if not isinstance(session_payload, dict):
        return None
    return HumanClarificationSession.from_dict(session_payload)
