"""Keep a failed writer close reachable without retaining its history owner."""

from __future__ import annotations

import atexit
import logging
from threading import RLock

from ownpi.runtime.rollout.recorder import RolloutRecorder


class RecorderCloseOwner:
    def __init__(self, recorder: RolloutRecorder, *, durable: bool = False) -> None:
        self.recorder = recorder
        self.durable = durable
        self._lock = RLock()
        self.closed = False
        self.retry_registered = False
        self.diagnostic = ''

    def close(self) -> bool:
        with self._lock:
            if self.closed:
                return True
            try:
                if self.durable:
                    self.recorder.sync()
                self.recorder.shutdown()
            except OSError as error:
                self.diagnostic = str(error)
                if not self.retry_registered:
                    atexit.register(self.close)
                    self.retry_registered = True
                logging.getLogger(__name__).warning('Rollout shutdown unconfirmed: %s', str(error))
                return False
            self.closed = True
            self.diagnostic = ''
            if self.retry_registered:
                atexit.unregister(self.close)
                self.retry_registered = False
            return True

    def finalize(self) -> None:
        try:
            self.close()
        except Exception:
            logging.getLogger(__name__).exception('Rollout finalizer could not close a failed writer')
