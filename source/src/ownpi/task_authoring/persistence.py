from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path

from ownpi.runtime.canonical_documents import EXACT_TEXT_POLICY, prepare_canonical_text
from ownpi.task_authoring.contracts import TaskSpecArtifactRef
from ownpi.utils.io import ensure_dir, write_text_atomic


@dataclass(frozen=True)
class RunTaskArtifactLayout:
    run_dir: Path
    raw_message_path: Path
    task_spec_path: Path


def build_run_task_artifact_layout(run_dir: Path) -> RunTaskArtifactLayout:
    root = Path(run_dir).expanduser().resolve()
    return RunTaskArtifactLayout(
        run_dir=root,
        raw_message_path=root / "raw_user_message.txt",
        task_spec_path=root / "task_spec.md",
    )


def persist_task_spec_artifact(
    *,
    layout: RunTaskArtifactLayout,
    raw_user_message: str,
    task_spec_content: str,
    revision: int = 1,
) -> TaskSpecArtifactRef:
    if revision < 1:
        raise ValueError("TaskSpec artifact revision must be positive.")
    ensure_dir(layout.run_dir)
    raw_message = str(raw_user_message)
    task_spec = str(task_spec_content)
    prepared = prepare_canonical_text(task_spec, policy=EXACT_TEXT_POLICY)
    if not layout.raw_message_path.is_file():
        raise FileNotFoundError("canonical raw user message is missing")
    if layout.raw_message_path.read_text(encoding="utf-8") != raw_message:
        raise ValueError("canonical raw user message content mismatch")
    raw_hash = sha256(raw_message.encode("utf-8")).hexdigest()
    write_text_atomic(layout.task_spec_path, prepared.serialized_text)
    return TaskSpecArtifactRef(
        raw_message_path=str(layout.raw_message_path),
        task_spec_path=str(layout.task_spec_path),
        content_hash=prepared.content_sha256,
        raw_message_hash=raw_hash,
        revision=revision,
    )
