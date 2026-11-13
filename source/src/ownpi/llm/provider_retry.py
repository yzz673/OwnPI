from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Literal, TypeVar

from ownpi.runtime.limits.recovery import (
    DEFAULT_REQUEST_MAX_RETRIES,
    DEFAULT_RETRY_BASE_DELAY_SECONDS,
    DEFAULT_STREAM_MAX_RETRIES,
    MAX_RETRY_DELAY_SECONDS,
    MAX_RETRY_JITTER_FACTOR,
    MIN_RETRY_JITTER_FACTOR,
)

RetryKind = Literal['request', 'stream']
RetryDelaySource = Literal['provider', 'backoff']
RetryOwner = Literal['inner_transport', 'outer_sampling']
RetryOutcomeKind = Literal['retry_exhausted']

@dataclass(frozen=True)
class RetryDelay:
    seconds: float
    source: RetryDelaySource
    jitter_factor: float | None = None

    def to_trace_metadata(self) -> dict[str, float | str]:
        metadata: dict[str, float | str] = {
            'retry_delay_seconds': self.seconds,
            'retry_delay_source': self.source,
        }
        if self.jitter_factor is not None:
            metadata['retry_jitter_factor'] = self.jitter_factor
        return metadata


@dataclass(frozen=True)
class ProviderRetryPolicy:
    request_max_retries: int = DEFAULT_REQUEST_MAX_RETRIES
    stream_max_retries: int = DEFAULT_STREAM_MAX_RETRIES
    base_delay_seconds: float = DEFAULT_RETRY_BASE_DELAY_SECONDS
    max_delay_seconds: float = MAX_RETRY_DELAY_SECONDS
    config_source: str = 'codex_defaults'

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            'request_max_retries',
            _nonnegative_retry_count(self.request_max_retries),
        )
        object.__setattr__(
            self,
            'stream_max_retries',
            _nonnegative_retry_count(self.stream_max_retries),
        )
        object.__setattr__(
            self,
            'base_delay_seconds',
            max(0.0, float(self.base_delay_seconds)),
        )
        object.__setattr__(
            self,
            'max_delay_seconds',
            max(0.0, float(self.max_delay_seconds)),
        )

    def retry_budget(self, kind: RetryKind) -> int:
        return (
            self.stream_max_retries
            if kind == 'stream'
            else self.request_max_retries
        )

    def total_attempts(self, kind: RetryKind) -> int:
        return self.retry_budget(kind) + 1

    def retry_delay(
        self,
        retry_number: int,
        *,
        retry_after_seconds: float | None = None,
        jitter: Callable[[], float],
    ) -> RetryDelay:
        if retry_after_seconds is not None:
            return RetryDelay(
                seconds=min(
                    self.max_delay_seconds,
                    max(0.0, float(retry_after_seconds)),
                ),
                source='provider',
            )
        bounded_retry_number = max(1, int(retry_number))
        exponent = min(bounded_retry_number - 1, 62)
        raw_delay = self.base_delay_seconds * float(2**exponent)
        jitter_factor = min(
            MAX_RETRY_JITTER_FACTOR,
            max(MIN_RETRY_JITTER_FACTOR, float(jitter())),
        )
        return RetryDelay(
            seconds=min(self.max_delay_seconds, raw_delay * jitter_factor),
            source='backoff',
            jitter_factor=jitter_factor,
        )

    @staticmethod
    def retry_http_status(status_code: int) -> bool:
        status = int(status_code)
        return status in {408, 429} or 500 <= status <= 599

@dataclass(frozen=True)
class ProviderRetryBudgetState:
    request_retries: int = 0
    stream_retries: int = 0
    sampling_retries: int = 0

    def consume(
        self,
        policy: ProviderRetryPolicy,
        kind: RetryKind,
    ) -> ProviderRetryBudgetState | None:
        current = self.retry_count(kind)
        if current >= policy.retry_budget(kind):
            return None
        if kind == 'stream':
            return ProviderRetryBudgetState(
                request_retries=self.request_retries,
                stream_retries=current + 1,
                sampling_retries=self.sampling_retries,
            )
        return ProviderRetryBudgetState(
            request_retries=current + 1,
            stream_retries=self.stream_retries,
            sampling_retries=self.sampling_retries,
        )

    def retry_count(self, kind: RetryKind) -> int:
        return self.stream_retries if kind == 'stream' else self.request_retries

    def owner_retry_count(self, owner: RetryOwner, kind: RetryKind = 'request') -> int:
        if owner == 'outer_sampling':
            return self.sampling_retries
        return self.retry_count(kind)

    def consume_owner(
        self,
        policy: ProviderRetryPolicy,
        owner: RetryOwner,
        kind: RetryKind = 'request',
    ) -> ProviderRetryBudgetState | None:
        if owner == 'inner_transport':
            return self.consume(policy, kind)
        current = self.sampling_retries
        if current >= policy.stream_max_retries:
            return None
        return ProviderRetryBudgetState(
            request_retries=self.request_retries,
            stream_retries=self.stream_retries,
            sampling_retries=current + 1,
        )


@dataclass(frozen=True)
class ProviderRetryDecision:
    state: ProviderRetryBudgetState
    kind: RetryKind
    retry_number: int
    delay: RetryDelay
    owner: RetryOwner = 'inner_transport'

    def to_trace_metadata(self) -> dict[str, float | str]:
        return {
            'retry_owner': self.owner,
            'retry_kind': self.kind,
            **self.delay.to_trace_metadata(),
        }


def plan_provider_retry(
    *,
    policy: ProviderRetryPolicy,
    state: ProviderRetryBudgetState,
    kind: RetryKind,
    retry_after_seconds: float | None,
    jitter: Callable[[], float],
    owner: RetryOwner = 'inner_transport',
) -> ProviderRetryDecision | None:
    next_state = state.consume_owner(policy, owner, kind)
    if next_state is None:
        return None
    retry_number = next_state.owner_retry_count(owner, kind)
    return ProviderRetryDecision(
        state=next_state,
        kind=kind,
        retry_number=retry_number,
        delay=policy.retry_delay(
            retry_number,
            retry_after_seconds=retry_after_seconds,
            jitter=jitter,
        ),
        owner=owner,
    )


class RetryCancelled(BaseException):
    """Raised when a retry wait or operation observes cooperative cancellation.

    Cancellation must not be swallowed by provider adapter transport handlers.
    """


class RetryableProviderOperation(Exception):
    """Adapter-neutral signal that an operation may be retried by an owner."""

    def __init__(
        self,
        *,
        owner: RetryOwner = 'inner_transport',
        kind: RetryKind,
        cause: BaseException,
        retry_after_seconds: float | None = None,
        trace_metadata: dict[str, Any] | None = None,
    ) -> None:
        self.owner = owner
        self.kind = kind
        self.cause = cause
        self.retry_after_seconds = retry_after_seconds
        self.trace_metadata = dict(trace_metadata or {})
        super().__init__(str(cause))


@dataclass(frozen=True)
class ProviderRetryOutcome:
    """Canonical, bounded projection of a retry owner's terminal exhaustion."""

    outcome_kind: RetryOutcomeKind
    terminal_owner: RetryOwner
    owner_chain: tuple[RetryOwner, ...]
    root_cause_kind: RetryKind
    root_cause_type: str
    root_cause_failure_kind: str
    inner_request_retries: int
    inner_stream_retries: int
    outer_sampling_retries: int
    payload_sha256: str = ''

    def to_trace_metadata(self) -> dict[str, Any]:
        return {
            'retry_outcome_kind': self.outcome_kind,
            'retry_terminal_owner': self.terminal_owner,
            'retry_owner_chain': list(self.owner_chain),
            'retry_root_cause_kind': self.root_cause_kind,
            'retry_root_cause_type': self.root_cause_type,
            'retry_root_cause_failure_kind': self.root_cause_failure_kind,
            'inner_request_retry_count': self.inner_request_retries,
            'inner_stream_retry_count': self.inner_stream_retries,
            'outer_sampling_retry_count': self.outer_sampling_retries,
            'retry_root_cause_payload_sha256': self.payload_sha256,
        }


class RetryExhausted(Exception):
    """Typed exhaustion boundary between a retry owner and its caller."""

    def __init__(
        self,
        *,
        owner: RetryOwner,
        state: ProviderRetryBudgetState,
        last_failure: RetryableProviderOperation,
    ) -> None:
        self.owner = owner
        self.state = state
        self.last_failure = last_failure
        self.outcome = project_retry_exhaustion(self)
        super().__init__(
            f'{owner} retry budget exhausted after '
            f'{state.owner_retry_count(owner, last_failure.kind)} retry(s)'
        )


def project_retry_exhaustion(exhaustion: RetryExhausted) -> ProviderRetryOutcome:
    """Project nested owner state without exposing an unbounded exception chain."""
    owner_chain: list[RetryOwner] = []
    current = exhaustion
    terminal_failure = exhaustion.last_failure
    while isinstance(current, RetryExhausted):
        owner_chain.append(current.owner)
        terminal_failure = current.last_failure
        cause = current.last_failure.cause
        if not isinstance(cause, RetryExhausted):
            break
        current = cause
    trace_metadata = terminal_failure.trace_metadata
    return ProviderRetryOutcome(
        outcome_kind='retry_exhausted',
        terminal_owner=exhaustion.owner,
        owner_chain=tuple(owner_chain),
        root_cause_kind=terminal_failure.kind,
        root_cause_type=type(terminal_failure.cause).__name__,
        root_cause_failure_kind=str(trace_metadata.get('failure_kind') or ''),
        inner_request_retries=exhaustion.state.request_retries,
        inner_stream_retries=exhaustion.state.stream_retries,
        outer_sampling_retries=exhaustion.state.sampling_retries,
        payload_sha256=str(
            trace_metadata.get('payload_sha256')
            or trace_metadata.get('http_error_body_sha256')
            or ''
        )[:128],
    )


_RetryResult = TypeVar('_RetryResult')


class ProviderRetryKernel:
    """Codex-shaped retry ownership shared by provider wire adapters.

    The kernel owns bounded counters and delay only. Provider adapters translate
    wire failures into ``RetryableProviderOperation`` and keep parsing/publication
    outside this class.
    """

    def __init__(
        self,
        policy: ProviderRetryPolicy,
        *,
        jitter: Callable[[], float] | None = None,
        sleeper: Callable[[float], None] | None = None,
        is_cancelled: Callable[[], bool] | None = None,
    ) -> None:
        self.policy = policy
        self._jitter = jitter or (lambda: 1.0)
        self._sleeper = sleeper or (lambda seconds: None)
        self._is_cancelled = is_cancelled or (lambda: False)

    def next_decision(
        self,
        *,
        state: ProviderRetryBudgetState,
        owner: RetryOwner,
        kind: RetryKind = 'request',
        retry_after_seconds: float | None = None,
    ) -> ProviderRetryDecision | None:
        return plan_provider_retry(
            policy=self.policy,
            state=state,
            kind=kind,
            retry_after_seconds=retry_after_seconds,
            jitter=self._jitter,
            owner=owner,
        )

    def wait(self, decision: ProviderRetryDecision) -> None:
        if self._is_cancelled():
            raise RetryCancelled('retry cancelled before backoff')
        if decision.delay.seconds <= 0:
            return
        self._sleeper(decision.delay.seconds)
        if self._is_cancelled():
            raise RetryCancelled('retry cancelled after backoff')

    def run_inner(
        self,
        operation: Callable[[], _RetryResult],
        *,
        kind: RetryKind = 'request',
        state: ProviderRetryBudgetState | None = None,
        on_retry: Callable[[ProviderRetryDecision, RetryableProviderOperation], None]
        | None = None,
    ) -> tuple[_RetryResult, ProviderRetryBudgetState]:
        current_state = state or ProviderRetryBudgetState()
        while True:
            if self._is_cancelled():
                raise RetryCancelled('retry cancelled before operation')
            try:
                return operation(), current_state
            except RetryableProviderOperation as failure:
                if failure.owner != 'inner_transport':
                    raise
                decision = self.next_decision(
                    state=current_state,
                    owner='inner_transport',
                    kind=failure.kind or kind,
                    retry_after_seconds=failure.retry_after_seconds,
                )
                if decision is None:
                    raise RetryExhausted(
                        owner='inner_transport',
                        state=current_state,
                        last_failure=failure,
                    ) from failure.cause
                record_provider_retry(decision, failure)
                if on_retry is not None:
                    on_retry(decision, failure)
                self.wait(decision)
                current_state = decision.state

    def run_nested(
        self,
        operation: Callable[[int], _RetryResult],
        *,
        kind: RetryKind = 'request',
        state: ProviderRetryBudgetState | None = None,
        on_retry: Callable[[ProviderRetryDecision, RetryableProviderOperation], None]
        | None = None,
    ) -> tuple[_RetryResult, ProviderRetryBudgetState]:
        """Run one logical sampling turn with separate inner and outer owners."""
        current_state = state or ProviderRetryBudgetState()
        sampling_attempt = 1
        while True:
            inner_state = ProviderRetryBudgetState(
                sampling_retries=current_state.sampling_retries,
            )

            def on_inner_retry(
                decision: ProviderRetryDecision,
                failure: RetryableProviderOperation,
            ) -> None:
                nonlocal inner_state
                inner_state = decision.state
                if on_retry is not None:
                    on_retry(decision, failure)

            outer_failure: RetryableProviderOperation
            try:
                result, inner_state = self.run_inner(
                    lambda: operation(sampling_attempt),
                    kind=kind,
                    state=inner_state,
                    on_retry=on_inner_retry,
                )
                return result, self._merge_inner_attempt_state(
                    current_state,
                    inner_state,
                )
            except RetryExhausted as exhausted:
                current_state = self._merge_inner_attempt_state(
                    current_state,
                    exhausted.state,
                )
                outer_failure = RetryableProviderOperation(
                    owner='outer_sampling',
                    kind=exhausted.last_failure.kind,
                    cause=exhausted,
                    retry_after_seconds=exhausted.last_failure.retry_after_seconds,
                    trace_metadata=exhausted.last_failure.trace_metadata,
                )
            except RetryableProviderOperation as failure:
                if failure.owner != 'outer_sampling':
                    raise
                current_state = self._merge_inner_attempt_state(
                    current_state,
                    inner_state,
                )
                outer_failure = failure
            decision = self.next_decision(
                state=current_state,
                owner='outer_sampling',
                kind=outer_failure.kind,
                retry_after_seconds=outer_failure.retry_after_seconds,
            )
            if decision is None:
                raise RetryExhausted(
                    owner='outer_sampling',
                    state=current_state,
                    last_failure=outer_failure,
                ) from outer_failure.cause
            record_provider_retry(decision, outer_failure)
            if on_retry is not None:
                on_retry(decision, outer_failure)
            self.wait(decision)
            current_state = decision.state
            sampling_attempt += 1

    @staticmethod
    def _merge_inner_attempt_state(
        aggregate: ProviderRetryBudgetState,
        inner_attempt: ProviderRetryBudgetState,
    ) -> ProviderRetryBudgetState:
        """Accumulate telemetry while resetting inner budgets per sampling attempt."""
        return ProviderRetryBudgetState(
            request_retries=aggregate.request_retries
            + inner_attempt.request_retries,
            stream_retries=aggregate.stream_retries
            + inner_attempt.stream_retries,
            sampling_retries=aggregate.sampling_retries,
        )

    def run_sampling(
        self,
        operation: Callable[[int], _RetryResult],
        *,
        state: ProviderRetryBudgetState | None = None,
        on_retry: Callable[[ProviderRetryDecision, RetryableProviderOperation], None]
        | None = None,
    ) -> tuple[_RetryResult, ProviderRetryBudgetState]:
        current_state = state or ProviderRetryBudgetState()
        sampling_attempt = 1
        while True:
            if self._is_cancelled():
                raise RetryCancelled('sampling retry cancelled before operation')
            try:
                return operation(sampling_attempt), current_state
            except RetryableProviderOperation as failure:
                decision = self.next_decision(
                    state=current_state,
                    owner='outer_sampling',
                    kind=failure.kind,
                    retry_after_seconds=failure.retry_after_seconds,
                )
                if decision is None:
                    raise RetryExhausted(
                        owner='outer_sampling',
                        state=current_state,
                        last_failure=failure,
                    ) from failure.cause
                record_provider_retry(decision, failure)
                if on_retry is not None:
                    on_retry(decision, failure)
                self.wait(decision)
                current_state = decision.state
                sampling_attempt += 1


def record_provider_retry(
    decision: ProviderRetryDecision,
    failure: RetryableProviderOperation | None = None,
    *,
    trace_metadata: dict[str, Any] | None = None,
) -> None:
    from ownpi.diagnostics import record_diagnostic
    from ownpi.llm.telemetry import current_llm_trace_scope

    try:
        metadata = dict(trace_metadata or {})
        if failure is not None:
            metadata.update(failure.trace_metadata)
            metadata.update(error_type=type(failure.cause).__name__, error_message=str(failure.cause))
        record_diagnostic({
            **current_llm_trace_scope(),
            **metadata,
            **decision.to_trace_metadata(),
            'event_type': 'provider_retry',
            'retry_number': decision.retry_number,
        })
    except Exception:
        pass


def _nonnegative_retry_count(value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError('provider retry count must be a non-negative integer')
    return value
