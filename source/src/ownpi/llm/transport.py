from __future__ import annotations

import http.client
import socket
import ssl
import urllib.error
from dataclasses import asdict, dataclass

import httpx

from ownpi.llm.failure_diagnostics import diagnostic_message

_RETRIABLE_TRANSPORT_ERRORS = (
    TimeoutError,
    socket.timeout,
    ssl.SSLEOFError,
    ssl.SSLWantReadError,
    http.client.RemoteDisconnected,
    http.client.IncompleteRead,
    ConnectionResetError,
    BrokenPipeError,
    httpx.TimeoutException,
    httpx.NetworkError,
    httpx.RemoteProtocolError,
)


@dataclass(frozen=True)
class TransportErrorInfo:
    phase: str = 'unknown'
    kind: str = 'unknown'
    wrapper_type: str = 'unknown'
    reason_type: str = 'unknown'
    reason_message: str = ''
    retriable: bool = False
    failure_reason: str = 'unknown'
    failure_origin: str = 'unknown'

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def unwrap_transport_error(exc: BaseException) -> BaseException:
    if isinstance(exc, urllib.error.URLError) and exc.reason is not None and isinstance(exc.reason, BaseException):
        return exc.reason
    return exc


def classify_transport_error(exc: BaseException) -> TransportErrorInfo:
    root_exc = unwrap_transport_error(exc)
    seen: set[int] = set()
    cause = root_exc
    while cause is not None and id(cause) not in seen and len(seen) < 8:
        seen.add(id(cause))
        if isinstance(cause, (ssl.SSLCertVerificationError, socket.gaierror)):
            root_exc = cause
            break
        cause = cause.__cause__ or cause.__context__
    phase = 'unknown'
    kind = type(root_exc).__name__
    retriable = isinstance(root_exc, _RETRIABLE_TRANSPORT_ERRORS)

    if isinstance(root_exc, httpx.ConnectTimeout):
        phase = 'connect'
    elif isinstance(root_exc, httpx.ConnectError):
        phase = 'connect'
    elif isinstance(
        root_exc,
        (httpx.ReadTimeout, httpx.WriteTimeout, httpx.PoolTimeout, httpx.RemoteProtocolError),
    ):
        phase = 'read'
    elif isinstance(root_exc, ssl.SSLEOFError):
        phase = 'handshake'
    elif isinstance(root_exc, ssl.SSLWantReadError):
        phase = 'read'
    elif isinstance(root_exc, (TimeoutError, socket.timeout, http.client.IncompleteRead, http.client.RemoteDisconnected)):
        phase = 'read'
    elif isinstance(root_exc, (ConnectionResetError, BrokenPipeError)):
        phase = 'read'
    elif isinstance(root_exc, ConnectionRefusedError):
        phase = 'connect'
        retriable = True
    elif isinstance(root_exc, socket.gaierror):
        phase = 'connect'
        retriable = root_exc.errno == socket.EAI_AGAIN
    elif isinstance(root_exc, ssl.SSLError):
        reason_text = str(root_exc).lower().replace('_', ' ')
        if 'certificate' in reason_text or 'verify failed' in reason_text:
            phase = 'handshake'
            retriable = False
        elif 'do_handshake' in reason_text or 'handshake' in reason_text or 'unexpected eof while reading' in reason_text:
            phase = 'handshake'
            retriable = (
                'unexpected eof while reading' in reason_text
                or 'eof occurred in violation of protocol' in reason_text
            )
        else:
            phase = 'read'
    elif isinstance(exc, urllib.error.URLError):
        reason_text = (
            str(getattr(exc, 'reason', '') or '').lower().replace('_', ' ')
        )
        if 'certificate' in reason_text:
            phase = 'handshake'
            retriable = False
        elif 'unexpected eof while reading' in reason_text or 'eof occurred in violation of protocol' in reason_text:
            phase = 'handshake'
            retriable = True
        elif 'timed out' in reason_text:
            phase = 'read'
            retriable = True
        elif 'refused' in reason_text:
            phase = 'connect'
            retriable = True
        elif 'name or service not known' in reason_text or 'nodename nor servname' in reason_text:
            phase = 'connect'
            retriable = False

    reason = 'connection_interrupted' if retriable else 'unknown'
    origin = 'transport' if isinstance(root_exc, (OSError, httpx.TransportError)) else 'local'
    if isinstance(root_exc, httpx.PoolTimeout):
        phase, reason, origin, retriable = 'pool', 'local_resource_exhausted', 'local', False
    elif isinstance(root_exc, (TimeoutError, httpx.TimeoutException)):
        reason = 'transport_timeout'
        if isinstance(root_exc, httpx.WriteTimeout):
            phase = 'write'
    elif isinstance(root_exc, socket.gaierror):
        reason = 'dns_failure'
    elif isinstance(root_exc, ssl.SSLCertVerificationError) or (
        isinstance(root_exc, ssl.SSLError) and not retriable
        and ('certificate' in str(root_exc).lower() or 'verify' in str(root_exc).lower())
    ):
        reason = 'tls_verification_failed'
    elif origin == 'local':
        reason = 'local_processing_error'
    return TransportErrorInfo(
        phase=phase,
        kind=kind,
        wrapper_type=type(exc).__name__,
        reason_type=type(root_exc).__name__,
        reason_message=diagnostic_message(root_exc),
        retriable=retriable,
        failure_reason=reason,
        failure_origin=origin,
    )


def build_transport_error_message(*, attempts: int, exc: BaseException, timeout_hint: str) -> str:
    details = classify_transport_error(exc)
    return (
        f"LLM request failed with a transient transport error after {attempts} attempt(s): "
        f"phase={details.phase}; kind={details.kind}; detail={details.reason_message or details.kind}. "
        f"{timeout_hint}"
    )


def build_transport_trace_attempt(*, attempt: int, elapsed_ms: float, exc: BaseException) -> dict[str, object]:
    details = classify_transport_error(exc)
    return {
        'attempt': attempt,
        'status': 'transport_error',
        'elapsed_ms': round(elapsed_ms, 2),
        'error_type': type(exc).__name__,
        'transport_phase': details.phase,
        'transport_kind': details.kind,
        'transport_reason_type': details.reason_type,
        'transport_reason': details.reason_message[:240],
        'retryable': details.retriable,
    }
