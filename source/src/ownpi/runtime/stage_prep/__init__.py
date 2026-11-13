from typing import Any

__all__ = [
    'StagePrepHandoff',
    'StagePrepRecord',
    'StagePrepRequest',
    'StagePrepResult',
    'StagePrepService',
    'StagePrepState',
]


def __getattr__(name: str) -> Any:
    if name == 'StagePrepService':
        from ownpi.runtime.stage_prep.service import StagePrepService

        return StagePrepService
    if name in {
        'StagePrepHandoff',
        'StagePrepRecord',
        'StagePrepRequest',
        'StagePrepResult',
        'StagePrepState',
    }:
        from ownpi.runtime.stage_prep import models

        return getattr(models, name)
    raise AttributeError(name)
