from __future__ import annotations

from contextlib import ExitStack, contextmanager
from dataclasses import dataclass, field
from typing import Any, Iterator, Mapping

import httpx

from ownpi.llm.cancellation import run_provider_io
from ownpi.llm.provider_request_context import ProviderTurnSession


@dataclass(frozen=True)
class ResponsesHttpRequest:
    url: str
    data: bytes
    headers: Mapping[str, str]
    method: str = "POST"
    turn_session: ProviderTurnSession | None = field(
        default=None,
        repr=False,
        compare=False,
    )

    @property
    def full_url(self) -> str:
        return self.url

    def header_items(self) -> list[tuple[str, str]]:
        return list(self.headers.items())


class ResponsesHttpStatusError(RuntimeError):
    def __init__(
        self,
        *,
        status_code: int,
        body: bytes,
        headers: Mapping[str, str] | None = None,
    ) -> None:
        self.code = int(status_code)
        self._body = bytes(body)
        self.headers = dict(headers or {})
        super().__init__(f"HTTP {self.code}")

    def read(self) -> bytes:
        return self._body


class ResponsesHttpResponse:
    def __init__(self, response: Any) -> None:
        self._response = response
        self.headers = response.headers
        self._line_iterator: Iterator[str | bytes] | None = None

    def read(self) -> bytes:
        return bytes(run_provider_io(self._response.read))

    def readline(self) -> bytes:
        if self._line_iterator is None:
            self._line_iterator = iter(self._response.iter_lines())
        try:
            line = run_provider_io(next, self._line_iterator)
        except StopIteration:
            return b""
        encoded = line if isinstance(line, bytes) else str(line).encode("utf-8")
        return encoded + b"\n"


class ResponsesHttpTransport:
    def __init__(self, *, client: Any | None = None) -> None:
        self._client = client if client is not None else httpx.Client()
        self._closed = False

    @property
    def client_instance_id(self) -> int:
        return id(self._client)

    @contextmanager
    def open(
        self,
        request: ResponsesHttpRequest,
        *,
        timeout: float,
        read_timeout: float | None = None,
    ) -> Iterator[ResponsesHttpResponse]:
        if self._closed:
            raise RuntimeError("Responses HTTP transport is closed")
        with ExitStack() as stack:
            response = run_provider_io(
                stack.enter_context,
                self._client.stream(
                    request.method,
                    request.url,
                    content=request.data,
                    headers=dict(request.headers),
                    timeout=httpx.Timeout(
                        connect=float(timeout),
                        read=float(read_timeout if read_timeout is not None else timeout),
                        write=float(timeout),
                        pool=float(timeout),
                    ),
                ),
            )
            status_code = int(getattr(response, "status_code", 200) or 200)
            if status_code >= 400:
                raise ResponsesHttpStatusError(
                    status_code=status_code,
                    body=bytes(run_provider_io(response.read)),
                    headers=dict(response.headers),
                )
            yield ResponsesHttpResponse(response)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._client.close()
