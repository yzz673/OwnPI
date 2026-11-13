from __future__ import annotations

from hashlib import sha256
from pathlib import Path

from ownpi.runtime.canonical_documents import (
    EXACT_TEXT_POLICY,
    prepare_canonical_text,
)
from ownpi.utils.io import write_text_atomic
from ownpi.schemas.feedback import FeedbackRecord, FeedbackRecordRef


def feedback_path(
    *,
    run_dir: Path,
    author_role: str,
    source_stage: str,
    attempt: int,
    round_index: int,
    stage_loop_index: int = 1,
    review_target: str = "",
) -> Path:
    if author_role not in {"reviewer", "analysis"}:
        raise ValueError(f"invalid feedback author role: {author_role}")
    if attempt < 1 or round_index < 1:
        raise ValueError("feedback attempt and round must be positive")
    root = run_dir.expanduser().resolve()
    directory = (
        root
        / "feedback"
        / source_stage
        / f"attempt-{attempt:03d}"
        / f"loop-{stage_loop_index:03d}"
    )
    if review_target:
        directory = directory / ("submission-" + sha256(review_target.encode("utf-8")).hexdigest())
    return directory / f"{author_role}-round-{round_index:03d}.md"


def persist_feedback_record(
    *,
    path: Path,
    content: str,
    record: FeedbackRecord,
) -> tuple[FeedbackRecordRef, bool]:
    canonical_path = path.expanduser().resolve()
    if Path(record.feedback_path).expanduser().resolve() != canonical_path:
        raise ValueError("feedback record path does not match canonical feedback path")
    prepared = prepare_canonical_text(content, policy=EXACT_TEXT_POLICY)
    if prepared.content_sha256 != record.content_hash:
        raise ValueError("feedback content conflicts with the record hash")
    if prepared.content_chars != record.content_chars:
        raise ValueError("feedback content conflicts with the record character count")
    if canonical_path.exists():
        if not canonical_path.is_file():
            raise ValueError(f"feedback target is not a file: {canonical_path}")
        existing_content = canonical_path.read_text(encoding="utf-8")
        _verify_content(record, existing_content)
        return FeedbackRecordRef(record=record), True
    write_text_atomic(canonical_path, prepared.serialized_text)
    verified_content = canonical_path.read_text(encoding="utf-8")
    if verified_content != prepared.serialized_text:
        raise ValueError("feedback persistence changed canonical content")
    return FeedbackRecordRef(record=record), False


def load_feedback_metadata(ref: FeedbackRecordRef) -> FeedbackRecord:
    """Return the in-memory record binding without reading a sidecar file."""
    return ref.record


def load_feedback_record(ref: FeedbackRecordRef, *, run_dir: Path | None = None) -> tuple[FeedbackRecord, str]:
    record = load_feedback_metadata(ref)
    path = Path(record.feedback_path)
    if not path.is_file():
        if run_dir is None or not _verify_feedback_submission(
            run_dir=run_dir,
            request_id=record.provider_operation_id.removeprefix('native_tool:'),
            record=record,
        ):
            raise FileNotFoundError(f"feedback text is missing: {path}")
    content = path.read_text(encoding="utf-8")
    _verify_content(record, content)
    return record, content


def verify_feedback_terminal_receipt(
    *,
    record: FeedbackRecord,
    content: str,
    run_dir: Path,
) -> None:
    request_id = record.provider_operation_id.removeprefix("native_tool:")
    if not request_id or request_id == record.provider_operation_id:
        raise ValueError("feedback provider operation id is invalid")
    _verify_content(record, content)
    if not _verify_feedback_submission(run_dir=run_dir, request_id=request_id, record=record):
        raise ValueError("feedback terminal receipt is missing")


def _verify_content(record: FeedbackRecord, content: str) -> None:
    prepared = prepare_canonical_text(content, policy=EXACT_TEXT_POLICY)
    if prepared.content_chars != record.content_chars:
        raise ValueError("feedback character count mismatch")
    if prepared.content_sha256 != record.content_hash:
        raise ValueError("feedback content hash mismatch")


def _verify_feedback_submission(
    *,
    run_dir: Path,
    request_id: str,
    record: FeedbackRecord,
) -> bool:
    from ownpi.runtime.rollout.business_reader import read_business_events
    from ownpi.runtime.rollout.feedback_projection import project_feedback_record
    from ownpi.runtime.rollout.run_events import run_events_path

    path = run_events_path(run_dir)
    if not path.is_file():
        return False
    facts = read_business_events(path)
    saved = project_feedback_record(facts.events, run_root=run_dir, run_id=facts.run_id,
                                    record_id=record.record_id, expected_record=record)
    if saved is None:
        return False
    # A committed submission survives a crash before its tool result is recorded.
    if saved.provider_operation_id != f'native_tool:{request_id}':
        raise ValueError('Feedback submission has a different call identity')
    return True


__all__ = [
    "feedback_path",
    "load_feedback_record",
    "load_feedback_metadata",
    "persist_feedback_record",
    "verify_feedback_terminal_receipt",
]
