from __future__ import annotations

__all__ = [
    'AgentExecutionRecord',
    'RuntimeControlResult',
    'RuntimeControlService',
    'WorkflowEngine',
    'WorkflowStateStore',
]


def __getattr__(name: str):
    if name == 'WorkflowEngine':
        from ownpi.controller.engine import WorkflowEngine

        return WorkflowEngine
    if name in {'RuntimeControlResult', 'RuntimeControlService'}:
        from ownpi.controller.runtime_control import (
            RuntimeControlResult,
            RuntimeControlService,
        )

        return {
            'RuntimeControlResult': RuntimeControlResult,
            'RuntimeControlService': RuntimeControlService,
        }[name]
    if name in {'WorkflowStateStore', 'AgentExecutionRecord'}:
        from ownpi.controller.state_store import AgentExecutionRecord, WorkflowStateStore

        return {'WorkflowStateStore': WorkflowStateStore, 'AgentExecutionRecord': AgentExecutionRecord}[name]
    raise AttributeError(name)
