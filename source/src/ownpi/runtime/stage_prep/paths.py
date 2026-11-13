from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ownpi.controller.stages import Stage


@dataclass(frozen=True)
class StagePrepLayout:
    run_dir: Path
    stage_dir: Path


def resolve_stage_prep_layout(
    *,
    run_dir: Path,
    stage: Stage,
    attempt: int,
) -> StagePrepLayout:
    if not isinstance(stage, Stage):
        raise TypeError('stage must be a Stage')
    if attempt < 1:
        raise ValueError('attempt must be at least 1')
    root = Path(run_dir).expanduser().resolve()
    stage_dir = (root / 'stages' / stage.value / f'attempt-{attempt:03d}').resolve()
    if not stage_dir.is_relative_to(root):
        raise ValueError('stage prep directory must remain inside run_dir')
    return StagePrepLayout(
        run_dir=root,
        stage_dir=stage_dir,
    )


__all__ = ['StagePrepLayout', 'resolve_stage_prep_layout']
