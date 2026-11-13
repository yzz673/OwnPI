from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from ownpi.stage_ids import canonical_stage_id


FEEDBACK_AUTHOR_ROLES = frozenset({'reviewer', 'analysis'})
CORE_FEEDBACK_INTENTS = frozenset({'request_review', 'complete_feedback'})


def _required_text(value: Any, field_name: str) -> str:
    text = str(value or '').strip()
    if not text:
        raise ValueError(f'{field_name} is required')
    return text


def _sha256(value: Any, field_name: str) -> str:
    digest = _required_text(value, field_name)
    if len(digest) != 64:
        raise ValueError(f'{field_name} must be a SHA-256 hex digest')
    try:
        int(digest, 16)
    except ValueError as exc:
        raise ValueError(f'{field_name} must be a SHA-256 hex digest') from exc
    return digest


@dataclass(frozen=True)
class WorkspaceReviewTarget:
    run_id: str
    stage: str
    attempt: int
    loop_index: int
    contract_sha256: str
    request_id: str

    def __post_init__(self) -> None:
        for name in ("run_id", "stage", "request_id", "contract_sha256"):
            if not isinstance(getattr(self, name), str):
                raise ValueError(f"{name} must be text")
            _required_text(getattr(self, name), name)
        for name in ("attempt", "loop_index"):
            value = getattr(self, name)
            if type(value) is not int or value < 1:
                raise ValueError(f"review {name} must be a positive integer")
        _sha256(self.contract_sha256, "contract_sha256")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> WorkspaceReviewTarget:
        fields = {"run_id", "stage", "attempt", "loop_index", "contract_sha256", "request_id"}
        if not isinstance(data, dict) or set(data) != fields:
            raise ValueError("workspace review target fields do not match the protocol")
        return cls(**data)

    @property
    def identity(self) -> str:
        import json
        from hashlib import sha256

        payload = json.dumps(self.to_dict(), sort_keys=True, separators=(",", ":"))
        return "workspace-review:" + sha256(payload.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class DirectReviewTarget:
    run_id: str
    stage: str
    attempt: int
    loop_index: int
    request_id: str

    def __post_init__(self) -> None:
        for name in ('run_id', 'request_id'):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f'{name} must be non-empty text')
        if self.stage != 'direct_qa':
            raise ValueError('direct review requires the direct_qa scope')
        for name in ('attempt', 'loop_index'):
            if type(getattr(self, name)) is not int or getattr(self, name) < 1:
                raise ValueError(f'review {name} must be a positive integer')

    def to_dict(self) -> dict[str, Any]:
        return {'kind': 'direct', **asdict(self)}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> DirectReviewTarget:
        fields = {'kind', 'run_id', 'stage', 'attempt', 'loop_index', 'request_id'}
        if not isinstance(data, dict) or set(data) != fields or data.get('kind') != 'direct':
            raise ValueError('direct review target fields do not match the protocol')
        return cls(**{key: value for key, value in data.items() if key != 'kind'})

    @property
    def identity(self) -> str:
        import json
        from hashlib import sha256

        payload = json.dumps(self.to_dict(), sort_keys=True, separators=(',', ':'))
        return 'direct-review:' + sha256(payload.encode('utf-8')).hexdigest()


ReviewTarget = WorkspaceReviewTarget | DirectReviewTarget


def decode_review_target(data: dict[str, Any]) -> ReviewTarget:
    if 'kind' in data:
        return DirectReviewTarget.from_dict(data)
    return WorkspaceReviewTarget.from_dict(data)


@dataclass(frozen=True)
class FeedbackRecord:
    """Immutable metadata for one canonical feedback Markdown file."""

    record_id: str
    author_role: str
    source_stage: str
    attempt: int
    round: int
    review_target: str
    provider_operation_id: str
    feedback_path: str
    content_hash: str
    content_chars: int
    created_at: str
    stage_loop_index: int = 1

    def __post_init__(self) -> None:
        for field_name in (
            'record_id',
            'source_stage',
            'review_target',
            'provider_operation_id',
            'feedback_path',
            'created_at',
        ):
            _required_text(getattr(self, field_name), field_name)
        if self.author_role not in FEEDBACK_AUTHOR_ROLES:
            raise ValueError(f'invalid feedback author role: {self.author_role}')
        if isinstance(self.attempt, bool) or self.attempt < 1:
            raise ValueError('feedback attempt must be a positive integer')
        if isinstance(self.round, bool) or self.round < 1:
            raise ValueError('feedback round must be a positive integer')
        if self.stage_loop_index < 1:
            raise ValueError('feedback stage loop index must be positive')
        _sha256(self.content_hash, 'content_hash')
        if (
            isinstance(self.content_chars, bool)
            or self.content_chars < 1
        ):
            raise ValueError('feedback character count is invalid')

    @classmethod
    def from_dict(cls, data: Any) -> FeedbackRecord:
        if not isinstance(data, dict):
            raise ValueError('feedback record must be a mapping')
        allowed = {
            'record_id',
            'author_role',
            'source_stage',
            'attempt',
            'round',
            'review_target',
            'provider_operation_id',
            'feedback_path',
            'content_hash',
            'content_chars',
            'created_at',
            'stage_loop_index',
        }
        unknown = sorted(str(key) for key in data if str(key) not in allowed)
        if unknown:
            raise ValueError(
                f'feedback record contains unsupported fields: {", ".join(unknown)}'
            )
        return cls(
            record_id=str(data.get('record_id', '') or '').strip(),
            author_role=str(data.get('author_role', '') or '').strip(),
            source_stage=canonical_stage_id(data.get('source_stage', '') or ''),
            attempt=int(data.get('attempt', 0) or 0),
            round=int(data.get('round', 0) or 0),
            review_target=str(data.get('review_target', '') or '').strip(),
            provider_operation_id=str(data.get('provider_operation_id', '') or '').strip(),
            feedback_path=str(data.get('feedback_path', '') or '').strip(),
            content_hash=str(data.get('content_hash', '') or '').strip(),
            content_chars=int(data.get('content_chars', 0) or 0),
            created_at=str(data.get('created_at', '') or '').strip(),
            stage_loop_index=int(data.get('stage_loop_index', 1) or 1),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class FeedbackRecordRef:
    """In-memory binding for canonical feedback text.

    The binding is carried by runtime state and rollout facts.  It is not a
    reference to a second metadata file beside the canonical Markdown.
    """

    record: FeedbackRecord

    def __post_init__(self) -> None:
        if not isinstance(self.record, FeedbackRecord):
            raise ValueError('feedback record ref must contain a FeedbackRecord')

    @property
    def record_id(self) -> str:
        return self.record.record_id

    @property
    def feedback_path(self) -> str:
        return self.record.feedback_path

    @classmethod
    def from_dict(cls, data: Any) -> FeedbackRecordRef:
        if not isinstance(data, dict):
            raise ValueError('feedback record ref must be a mapping')
        payload = data.get('record', data)
        return cls(record=FeedbackRecord.from_dict(payload))

    def to_dict(self) -> dict[str, Any]:
        return self.record.to_dict()


@dataclass(frozen=True)
class CoreFeedbackIntentReceipt:
    intent: str
    source_stage: str
    attempt: int
    critic_role: str
    round: int
    feedback_record_id: str
    tool_call_id: str
    created_at: str

    def __post_init__(self) -> None:
        if self.intent not in CORE_FEEDBACK_INTENTS:
            raise ValueError(f'invalid Core feedback intent: {self.intent}')
        if self.critic_role not in {'reviewer'}:
            raise ValueError(f'invalid critic role: {self.critic_role}')
        for field_name in ('source_stage', 'tool_call_id', 'created_at'):
            _required_text(getattr(self, field_name), field_name)
        if isinstance(self.attempt, bool) or self.attempt < 1:
            raise ValueError('Core feedback intent attempt must be positive')
        if isinstance(self.round, bool) or self.round < 0:
            raise ValueError('Core feedback intent round must be nonnegative')
        if self.round == 0:
            if self.intent != 'request_review' or self.feedback_record_id:
                raise ValueError('initial review requires request_review without feedback')
        else:
            _required_text(self.feedback_record_id, 'feedback_record_id')

    @classmethod
    def from_dict(cls, data: Any) -> CoreFeedbackIntentReceipt:
        if not isinstance(data, dict):
            raise ValueError('Core feedback intent receipt must be a mapping')
        allowed = {
            'intent',
            'source_stage',
            'attempt',
            'critic_role',
            'round',
            'feedback_record_id',
            'tool_call_id',
            'created_at',
        }
        unknown = sorted(str(key) for key in data if str(key) not in allowed)
        if unknown:
            raise ValueError(
                f'Core feedback intent receipt contains unsupported fields: {", ".join(unknown)}'
            )
        return cls(
            intent=str(data.get('intent', '') or '').strip(),
            source_stage=canonical_stage_id(data.get('source_stage', '') or ''),
            attempt=int(data.get('attempt', 0) or 0),
            critic_role=str(data.get('critic_role', '') or '').strip(),
            round=int(data.get('round', 0) or 0),
            feedback_record_id=str(data.get('feedback_record_id', '') or '').strip(),
            tool_call_id=str(data.get('tool_call_id', '') or '').strip(),
            created_at=str(data.get('created_at', '') or '').strip(),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class NextStageReceipt:
    source_stage: str
    attempt: int
    feedback_record_id: str
    target_stage: str
    tool_call_id: str
    created_at: str

    def __post_init__(self) -> None:
        for field_name in (
            'source_stage',
            'feedback_record_id',
            'target_stage',
            'tool_call_id',
            'created_at',
        ):
            _required_text(getattr(self, field_name), field_name)
        if isinstance(self.attempt, bool) or self.attempt < 1:
            raise ValueError('next-stage receipt attempt must be positive')

    @classmethod
    def from_dict(cls, data: Any) -> NextStageReceipt:
        if not isinstance(data, dict):
            raise ValueError('next-stage receipt must be a mapping')
        allowed = {
            'source_stage',
            'attempt',
            'feedback_record_id',
            'target_stage',
            'tool_call_id',
            'created_at',
        }
        unknown = sorted(str(key) for key in data if str(key) not in allowed)
        if unknown:
            raise ValueError(
                f'next-stage receipt contains unsupported fields: {", ".join(unknown)}'
            )
        target = canonical_stage_id(data.get('target_stage', '') or '')
        return cls(
            source_stage=canonical_stage_id(data.get('source_stage', '') or ''),
            attempt=int(data.get('attempt', 0) or 0),
            feedback_record_id=str(data.get('feedback_record_id', '') or '').strip(),
            target_stage=target,
            tool_call_id=str(data.get('tool_call_id', '') or '').strip(),
            created_at=str(data.get('created_at', '') or '').strip(),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


__all__ = [
    'CORE_FEEDBACK_INTENTS',
    'FEEDBACK_AUTHOR_ROLES',
    'CoreFeedbackIntentReceipt',
    'FeedbackRecord',
    'FeedbackRecordRef',
    'NextStageReceipt',
]
