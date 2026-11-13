from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from ownpi.schemas.runtime_human_clarification import (
    build_question_key,
    normalize_human_answer_payload,
)


@dataclass(frozen=True)
class NormalizedHumanAnswers:
    answers_by_question_id: dict[str, dict[str, Any]]


def question_key_for_item(
    question: Mapping[str, Any],
    *,
    interaction_request_id: str = '',
) -> str:
    explicit = str(question.get('question_key', '') or '').strip()
    if explicit:
        return explicit
    question_id = str(question.get('question_id', '') or '').strip()
    return build_question_key(interaction_request_id, question_id) if interaction_request_id else question_id


def clarification_prefix_candidates(
    questions: list[dict[str, Any]],
    *,
    interaction_request_id: str = '',
) -> list[tuple[str, dict[str, Any], str]]:
    candidates: list[tuple[str, dict[str, Any], str]] = []
    for index, question in enumerate(questions, start=1):
        question_id = str(question.get('question_id', '') or '').strip()
        if not question_id:
            continue
        question_key = question_key_for_item(question, interaction_request_id=interaction_request_id)
        if question_key:
            candidates.append((f'{question_key}:', question, question_id))
        candidates.append((f'{question_id}:', question, question_id))
        candidates.append((f'{index}:', question, question_id))
    return sorted(candidates, key=lambda item: len(item[0]), reverse=True)


def match_clarification_prefix(
    value: str,
    *,
    questions: list[dict[str, Any]],
    interaction_request_id: str = '',
) -> tuple[dict[str, Any], str, str] | None:
    normalized = str(value or '').strip()
    for prefix, question, question_id in clarification_prefix_candidates(
        questions,
        interaction_request_id=interaction_request_id,
    ):
        if normalized.startswith(prefix):
            return question, question_id, normalized[len(prefix) :].strip()
    return None


def parse_human_answer_value(question: Mapping[str, Any], raw_answer: Any) -> dict[str, Any]:
    if isinstance(raw_answer, dict):
        return normalize_human_answer_payload(raw_answer)

    answer_text = str(raw_answer or '').strip()
    options = [option for option in question.get('options', []) if isinstance(option, dict)]
    if not options:
        return {'selected_labels': [], 'free_text': answer_text or None}

    valid_labels = {
        str(option.get('label', '')).strip()
        for option in options
        if str(option.get('label', '')).strip()
    }
    multi_select = bool(question.get('multi_select', False))
    tokens = (
        [answer_text]
        if not multi_select
        else [item.strip() for item in answer_text.split(',') if item.strip()]
    )
    if tokens and all(token in valid_labels for token in tokens):
        return {'selected_labels': tokens, 'free_text': None}
    return {'selected_labels': [], 'free_text': answer_text or None}


def normalize_human_answer_mapping(
    raw_answers: Mapping[str, Any],
    *,
    pending: Mapping[str, Any],
) -> dict[str, dict[str, Any]]:
    questions = [item for item in pending.get('questions', []) if isinstance(item, dict)]
    if not questions:
        return {
            str(key): normalize_human_answer_payload(value)
            for key, value in raw_answers.items()
            if str(key).strip()
        }

    interaction_request_id = str(
        pending.get('interaction_request_id') or pending.get('session_id') or ''
    ).strip()
    by_question_id = {
        str(item.get('question_id', '') or '').strip(): item
        for item in questions
        if str(item.get('question_id', '') or '').strip()
    }
    by_question_key = {
        question_key_for_item(item, interaction_request_id=interaction_request_id): item
        for item in questions
        if str(item.get('question_id', '') or '').strip()
    }
    normalized: dict[str, dict[str, Any]] = {}
    for raw_key, raw_value in raw_answers.items():
        key = str(raw_key or '').strip()
        if not key:
            continue
        question: dict[str, Any] | None = None
        if key in by_question_id:
            question = by_question_id[key]
        elif key in by_question_key:
            question = by_question_key[key]
        elif key.isdigit():
            index = int(key) - 1
            if 0 <= index < len(questions):
                question = questions[index]
        if question is None:
            raise ValueError(
                f'Unknown clarification answer key: {key}. '
                'Use question_id, question_key, or 1-based index.'
            )
        question_id = str(question.get('question_id', '') or '').strip()
        if not question_id:
            raise ValueError(f'Pending clarification question is missing question_id for key: {key}')
        normalized[question_id] = parse_human_answer_value(question, raw_value)
    return normalized


def parse_human_answers_text(
    message: str,
    *,
    pending: Mapping[str, Any],
) -> dict[str, dict[str, Any]]:
    questions = [item for item in pending.get('questions', []) if isinstance(item, dict)]
    if not questions:
        raise ValueError('No pending clarification questions found.')

    interaction_request_id = str(
        pending.get('interaction_request_id') or pending.get('session_id') or ''
    ).strip()
    normalized_message = str(message or '').strip()
    lines = [line.strip() for line in normalized_message.splitlines() if line.strip()]
    if len(questions) == 1:
        question = questions[0]
        question_id = str(question.get('question_id', '') or '').strip()
        matched = match_clarification_prefix(
            normalized_message,
            questions=questions,
            interaction_request_id=interaction_request_id,
        )
        if matched is None:
            return {question_id: parse_human_answer_value(question, normalized_message)}
        matched_question, matched_question_id, answer = matched
        return {matched_question_id: parse_human_answer_value(matched_question, answer)}

    answers: dict[str, dict[str, Any]] = {}
    for line in lines:
        matched = match_clarification_prefix(
            line,
            questions=questions,
            interaction_request_id=interaction_request_id,
        )
        if matched is None:
            raise ValueError(
                'Failed to parse clarification answers. Use `1: ...`, `question_id: ...`, '
                'or `question_key: ...` when there are multiple questions.'
            )
        question, question_id, answer = matched
        if not answer:
            continue
        answers[question_id] = parse_human_answer_value(question, answer)
    if not answers:
        raise ValueError(
            'Failed to parse clarification answers. Use `1: ...`, `question_id: ...`, '
            'or `question_key: ...` when there are multiple questions.'
        )
    return answers
