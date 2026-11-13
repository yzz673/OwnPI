from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ownpi.schemas.runtime_human_clarification import human_answer_has_value


@dataclass
class HumanAnswerDraft:
    selected_labels: list[str] = field(default_factory=list)
    free_text: str | None = None
    touched: bool = False

    def to_payload(self) -> dict[str, Any]:
        return {
            'selected_labels': [str(item).strip() for item in self.selected_labels if str(item).strip()],
            'free_text': str(self.free_text or '').strip() or None,
        }


@dataclass
class HumanQuestionDraftState:
    current_question_index: int = 0
    answers_by_question_id: dict[str, HumanAnswerDraft] = field(default_factory=dict)
    validation_errors: dict[str, str] = field(default_factory=dict)
    is_review_step: bool = False


class HumanQuestionSession:
    def __init__(
        self,
        *,
        pending: dict[str, Any],
        initial_answers: dict[str, Any] | None = None,
    ) -> None:
        self.pending = pending
        self.questions = [item for item in pending.get('questions', []) if isinstance(item, dict)]
        answers_by_question_id: dict[str, HumanAnswerDraft] = {}
        for question in self.questions:
            question_id = str(question.get('question_id', '') or '').strip()
            if not question_id:
                continue
            seed = initial_answers.get(question_id) if isinstance(initial_answers, dict) else None
            if isinstance(seed, dict):
                answers_by_question_id[question_id] = HumanAnswerDraft(
                    selected_labels=[
                        str(item).strip()
                        for item in seed.get('selected_labels', [])
                        if str(item).strip()
                    ],
                    free_text=(str(seed.get('free_text', '') or '').strip() or None),
                    touched=human_answer_has_value(seed),
                )
            else:
                answers_by_question_id[question_id] = HumanAnswerDraft()
        self.state = HumanQuestionDraftState(answers_by_question_id=answers_by_question_id)

    def draft_for(self, question_id: str) -> HumanAnswerDraft:
        normalized = str(question_id or '').strip()
        if normalized not in self.state.answers_by_question_id:
            self.state.answers_by_question_id[normalized] = HumanAnswerDraft()
        return self.state.answers_by_question_id[normalized]

    def answer_payload_for(self, question_id: str) -> dict[str, Any]:
        return self.draft_for(question_id).to_payload()

    def update_single_select(self, question_id: str, label: str | None) -> None:
        draft = self.draft_for(question_id)
        normalized = str(label or '').strip()
        draft.selected_labels = [normalized] if normalized else []
        draft.touched = True
        self.clear_validation_error(question_id)

    def update_multi_select(self, question_id: str, labels: list[str]) -> None:
        draft = self.draft_for(question_id)
        seen: set[str] = set()
        cleaned: list[str] = []
        for item in labels:
            normalized = str(item or '').strip()
            if not normalized or normalized.casefold() in seen:
                continue
            seen.add(normalized.casefold())
            cleaned.append(normalized)
        draft.selected_labels = cleaned
        draft.touched = True
        self.clear_validation_error(question_id)

    def update_free_text(self, question_id: str, text: str | None) -> None:
        draft = self.draft_for(question_id)
        draft.free_text = str(text or '').strip() or None
        draft.touched = True
        self.clear_validation_error(question_id)

    def set_current_question_index(self, index: int) -> None:
        if not self.questions:
            self.state.current_question_index = 0
            return
        self.state.current_question_index = max(0, min(index, len(self.questions) - 1))

    def enter_review(self) -> None:
        self.state.is_review_step = True
        self.validate_all()

    def leave_review(self) -> None:
        self.state.is_review_step = False

    def validate_question(self, question_id: str) -> str | None:
        question = next(
            (
                item
                for item in self.questions
                if str(item.get('question_id', '') or '').strip() == str(question_id or '').strip()
            ),
            None,
        )
        if question is None:
            return None
        if not bool(question.get('required', True)):
            return None
        payload = self.answer_payload_for(question_id)
        if human_answer_has_value(payload):
            self.clear_validation_error(question_id)
            return None
        header = str(question.get('header', '') or '').strip() or str(question_id or '').strip() or 'Question'
        index = self.questions.index(question) + 1
        return f'Question {index} ({header}) requires an answer.'

    def question_error(self, question_id: str) -> str | None:
        return self.state.validation_errors.get(str(question_id or '').strip()) or None

    def clear_validation_error(self, question_id: str) -> None:
        self.state.validation_errors.pop(str(question_id or '').strip(), None)

    def validate_all(self) -> dict[str, str]:
        errors: dict[str, str] = {}
        for question in self.questions:
            question_id = str(question.get('question_id', '') or '').strip()
            if not question_id:
                continue
            error = self.validate_question(question_id)
            if error:
                errors[question_id] = error
        self.state.validation_errors = errors
        return dict(errors)

    def can_submit(self) -> bool:
        return not self.validate_all()

    def canonical_answers(self) -> dict[str, dict[str, Any]]:
        return {
            str(question.get('question_id', '') or '').strip(): self.answer_payload_for(
                str(question.get('question_id', '') or '').strip()
            )
            for question in self.questions
            if str(question.get('question_id', '') or '').strip()
        }
