from __future__ import annotations

import re
from typing import Iterable

from ownpi.schemas.runtime_human_clarification import HumanClarificationQuestion, HumanClarificationSession, human_answer_text


_PATH_QUESTION_IDS = {
    'pdf_path',
    'dataset_path',
    'file_path',
    'repo_path',
    'confirm_or_provide_demo_dataset',
    'dataset_path_or_retarget',
}
_PATH_HINT_TOKENS = ('path', '路径', '目录', 'dataset', '数据集', 'file', 'pdf', 'repo')
_QUOTED_PATH_PATTERN = re.compile(r"[`'\"](?P<path>(?:~|\.{1,2}|/|[A-Za-z0-9._-]+/)[^`'\"]+)[`'\"]")
_PLAIN_PATH_PATTERN = re.compile(r"(?P<path>(?:~|\.{1,2}|/|[A-Za-z0-9._-]+/)[^\s,;]+)")


def resolve_clarified_path_for_scope(
    *,
    pending_session: HumanClarificationSession | None,
    history: Iterable[HumanClarificationSession],
    stage: str,
    attempt: int,
    fallback_path: str,
    candidate_question_ids: tuple[str, ...] = (),
    allow_previous_attempt: bool = False,
) -> str:
    exact_ids = tuple(str(item).strip() for item in candidate_question_ids if str(item).strip())
    sessions: list[HumanClarificationSession] = []
    if pending_session is not None:
        sessions.append(pending_session)
    sessions.extend(reversed(list(history)))
    for question_id in exact_ids:
        for session in sessions:
            if session.stage != stage or int(session.attempt or 0) != attempt:
                continue
            answer = _answer_for_question_id(session=session, question_id=question_id)
            if answer is not None:
                return answer
    for session in sessions:
        if session.stage != stage or int(session.attempt or 0) != attempt:
            continue
        answer = _generic_path_answer(session)
        if answer is not None:
            return answer
    if allow_previous_attempt:
        for question_id in exact_ids:
            for session in sessions:
                if session.stage != stage or int(session.attempt or 0) > attempt:
                    continue
                answer = _answer_for_question_id(session=session, question_id=question_id)
                if answer is not None:
                    return answer
        for session in sessions:
            if session.stage != stage or int(session.attempt or 0) > attempt:
                continue
            answer = _generic_path_answer(session)
            if answer is not None:
                return answer
    return fallback_path


def _answer_for_question_id(
    *,
    session: HumanClarificationSession,
    question_id: str,
) -> str | None:
    raw_answer = human_answer_text(session.answers.get(question_id))
    if not raw_answer:
        return None
    return extract_path_answer(raw_answer)


def _generic_path_answer(session: HumanClarificationSession) -> str | None:
    for question in session.questions:
        if not _question_can_supply_path(question):
            continue
        raw_answer = human_answer_text(session.answers.get(question.question_id))
        if not raw_answer:
            continue
        answer = extract_path_answer(raw_answer)
        if answer is not None:
            return answer
    return None


def _question_can_supply_path(question: HumanClarificationQuestion) -> bool:
    question_id = str(question.question_id or '').strip().casefold()
    question_text = str(question.question or '').strip().casefold()
    if question_id in _PATH_QUESTION_IDS:
        return True
    return any(token in question_id for token in _PATH_HINT_TOKENS) or any(
        token in question_text for token in _PATH_HINT_TOKENS
    )


def extract_path_answer(answer: str) -> str | None:
    normalized = str(answer).strip()
    if not normalized:
        return None
    if re.match(r'^[A-Za-z][A-Za-z0-9+.-]*://', normalized):
        return None
    normalized = re.sub(r'^[A-Ca-c]\s*[:：]\s*', '', normalized).strip()
    quoted_match = _QUOTED_PATH_PATTERN.search(normalized)
    if quoted_match is not None:
        candidate = _clean_path_candidate(quoted_match.group('path'))
        if _looks_like_path_candidate(candidate):
            return candidate
    plain_match = _PLAIN_PATH_PATTERN.search(normalized)
    if plain_match is not None:
        candidate = _clean_path_candidate(plain_match.group('path'))
        if _looks_like_path_candidate(candidate):
            return candidate
    stripped = _clean_path_candidate(normalized)
    if _looks_like_path_candidate(stripped):
        return stripped
    return None


def _clean_path_candidate(value: str) -> str:
    cleaned = str(value or '').strip().strip("`'\"").rstrip('.,;)')
    if '://' in cleaned:
        return ''
    return cleaned


def _looks_like_path_candidate(value: str) -> bool:
    candidate = str(value or '').strip()
    if not candidate:
        return False
    if '://' in candidate or candidate.startswith('//'):
        return False
    if candidate.startswith(('/', './', '../', '~/')):
        return True
    if '/' not in candidate:
        return False
    parts = [part for part in candidate.split('/') if part and part != '.']
    if len(parts) < 2:
        return False
    prefix = parts[0].casefold()
    if prefix in {
        'data',
        'dataset',
        'datasets',
        'repo',
        'repos',
        'src',
        'scripts',
        'configs',
        'config',
        'results',
        'result',
        'outputs',
        'output',
        'runs',
        'artifacts',
        'logs',
        'models',
        'checkpoints',
    }:
        return True
    joined = '/'.join(parts)
    if len(parts) >= 3:
        return True
    if any(char in joined for char in ('_', '-', '.')):
        return True
    if any(any(char.isdigit() for char in part) for part in parts):
        return True
    return False
