from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ownpi.controller.run_context import PreparedRunContext
from ownpi.utils.io import ensure_dir, write_text_atomic


@dataclass(frozen=True)
class PreIntakeBootstrapResult:
    run_dir: Path


def prepare_pre_intake_bootstrap(
    *,
    run_context: PreparedRunContext,
    raw_user_message: str,
) -> PreIntakeBootstrapResult:
    run_dir = ensure_dir(run_context.run_dir)
    write_text_atomic(run_dir / "raw_user_message.txt", str(raw_user_message))
    return PreIntakeBootstrapResult(
        run_dir=run_dir,
    )


__all__ = ["PreIntakeBootstrapResult", "prepare_pre_intake_bootstrap"]
