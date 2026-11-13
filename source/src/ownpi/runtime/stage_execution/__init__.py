from typing import Any

from ownpi.runtime.stage_execution.models import (
    CoreTrajectoryIdentity,
    CoreTrajectoryRef,
    RecordSequenceSpan,
)
__all__ = [
    'CoreTrajectoryIdentity',
    'CoreTrajectoryRef',
    'RecordSequenceSpan',
    'run_stage_critic_native_loop',
]


def __getattr__(name: str) -> Any:
    if name == 'run_stage_critic_native_loop':
        from ownpi.runtime.stage_execution.critic_loop_runner import (
            run_stage_critic_native_loop,
        )

        return {
            'run_stage_critic_native_loop': run_stage_critic_native_loop,
        }[name]
    raise AttributeError(name)
