"""Own one live history across segments, independent of business scheduling."""

from __future__ import annotations

from dataclasses import dataclass
import sys
from typing import Callable, TypeVar

from ownpi.runtime.native_tools.history_items import HistoryEntry
from ownpi.runtime.native_tools.loop_engine.contracts import NativeLoopInvocation, NativeLoopResult
from ownpi.runtime.native_tools.loop_engine.session import NativeLoopSession


ResultT = TypeVar('ResultT')


def native_turn_outcome(result: NativeLoopResult) -> dict[str, str]:
    return {'status': result.status, 'reason': result.terminal_reason or result.failure_class}


@dataclass
class NativeLoopContinuation:
    session: NativeLoopSession
    execute: Callable[[NativeLoopInvocation | None], NativeLoopResult] | None = None
    closed: bool = False

    def append_input(self, entries: tuple[HistoryEntry, ...]) -> None:
        self.session.pending_input.extend(entries)

    def run_segment(
        self, execute: Callable[[], ResultT],
        outcome: Callable[[ResultT], dict[str, str]],
        *, prepare_input: Callable[[], None] | None = None,
    ) -> ResultT:
        with self.session.history.turn(close=False) as turn:
            if prepare_input is not None:
                prepare_input()
            self.session.record_pending_input()
            result = execute()
            turn.update(outcome(result))
            return result

    def run(self, invocation: NativeLoopInvocation | None = None) -> NativeLoopResult:
        assert self.execute is not None
        return self.run_segment(lambda: self.execute(invocation), native_turn_outcome)

    def close(self) -> bool:
        if not self.closed:
            primary = sys.exc_info()[1]
            try:
                self.closed = self.session.history.close()
            except Exception as error:
                if primary is None:
                    raise
                primary.add_note(f'Continuation close failed: {error!r}')
        return self.closed
