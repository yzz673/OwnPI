from __future__ import annotations

import asyncio
import inspect
import logging
import threading
from typing import Any, Awaitable, Callable
from dataclasses import dataclass
import uuid

from ownpi.runtime.process_owner.bindings import (
    ProcessOwnerBinding,
    ProcessOwnerBindingRegistry,
)
from ownpi.runtime.process_owner.client import ProcessOwnerClient, complete_on_cancel
from ownpi.runtime.process_owner.protocol import ProcessOwnerProtocolError
from ownpi.runtime.process_owner.remote_transport import (
    RemoteProcessOwnerTransport,
    connect_remote_owner_async,
)
from ownpi.runtime.limits.resources import (
    PROCESS_OWNER_DAEMON_IDLE_GRACE_SECONDS,
)


OwnerNotificationHandler = Callable[[str, dict[str, Any]], None]
ResourceCallback = Callable[[], Awaitable[None] | None]


@dataclass
class ProcessOwnerSessionView:
    public_session_id: int
    owner_id: str
    process_id: str
    run_id: str
    actor_id: str
    cursor: int = 0
    released: bool = False
    cwd: str = ""
    stdout_ref: str = ""
    stderr_ref: str = ""
    timeout_seconds: int = 0
    sandbox_backend: str = "unknown"
    command_display: str = ""


class ProcessOwnerManager:
    """Engine-owned transient access to process-owner clients."""

    def __init__(
        self,
        *,
        bindings: ProcessOwnerBindingRegistry,
        owner_daemon_idle_grace_seconds: float = PROCESS_OWNER_DAEMON_IDLE_GRACE_SECONDS,
        notification_handler: OwnerNotificationHandler | None = None,
    ) -> None:
        self._bindings = bindings
        self._owner_daemon_idle_grace_seconds = max(
            0.0,
            float(owner_daemon_idle_grace_seconds),
        )
        self._notification_handler = notification_handler
        self._clients: dict[
            str,
            ProcessOwnerClient | RemoteProcessOwnerTransport,
        ] = {}
        self._sessions: dict[int, ProcessOwnerSessionView] = {}
        self._resource_callbacks: dict[tuple[str, str], ResourceCallback] = {}
        self._notification_sequences: dict[tuple[str, str], int] = {}
        self._next_public_session_id = uuid.uuid4().int % (2**52) + 1
        self._lock = threading.RLock()
        self._connecting: set[str] = set()
        self._launch_owners: dict[str, str] = {}

    @property
    def bindings(self) -> ProcessOwnerBindingRegistry:
        return self._bindings

    @property
    def owner_daemon_idle_grace_seconds(self) -> float:
        return self._owner_daemon_idle_grace_seconds

    async def client_for_async(
        self,
        owner_id: str,
    ) -> ProcessOwnerClient | RemoteProcessOwnerTransport:
        normalized = _require_owner_id(owner_id)
        while True:
            with self._lock:
                current = self._clients.get(normalized)
                if isinstance(current, ProcessOwnerClient) and current.closed:
                    self._clients.pop(normalized)
                    current = None
                if current is not None:
                    return current
                if normalized not in self._connecting:
                    self._connecting.add(normalized)
                    break
            await asyncio.sleep(0.01)
        try:
            return await self._connect_local_async(normalized)
        finally:
            with self._lock:
                self._connecting.discard(normalized)

    async def _connect_local_async(self, normalized: str) -> ProcessOwnerClient | RemoteProcessOwnerTransport:
        with self._lock:
            binding = self._bindings.get(normalized)
        if binding is None:
            from ownpi.runtime.process_owner.local_transport import launch_local_owner

            client, endpoint = await launch_local_owner(
                idle_grace=self._owner_daemon_idle_grace_seconds,
                notification_handler=lambda method, params: self._notify(normalized, method, params),
            )
            binding = ProcessOwnerBinding(normalized, "local_unix", endpoint, client.session_id)
        elif binding.transport_kind == "remote_ssh_unix":
            from ownpi.runtime.process_owner.remote import RemoteEndpointRef
            from ownpi.tools.backends.remote_workflow.profiles import load_profile, resolve_config_dir

            profile = load_profile(resolve_config_dir(), RemoteEndpointRef.parse(binding.endpoint_ref).profile_name)
            return await self.restore_remote_binding_async(binding=binding, profile=profile)
        else:
            client = await self._connect_async(binding)
        with self._lock:
            self._clients[normalized] = client
            self._bindings.upsert(binding)
            return client

    def has_client(self, owner_id: str) -> bool:
        normalized = _require_owner_id(owner_id)
        with self._lock:
            return normalized in self._clients

    async def bind_remote_async(
        self,
        *,
        owner_id: str,
        profile: Any,
        endpoint_ref: str,
        resume_session_id: str = "",
    ) -> RemoteProcessOwnerTransport:
        normalized = _require_owner_id(owner_id)
        binding = ProcessOwnerBinding(
            owner_id=normalized,
            transport_kind="remote_ssh_unix",
            endpoint_ref=str(endpoint_ref).strip(),
            resume_session_id=str(resume_session_id or "").strip(),
        )
        with self._lock:
            current = self._clients.get(normalized)
            current_binding = self._bindings.get(normalized)
            if (
                isinstance(current, RemoteProcessOwnerTransport)
                and current_binding is not None
                and current_binding.endpoint_ref == binding.endpoint_ref
                and (
                    not binding.resume_session_id
                    or current.session_id == binding.resume_session_id
                )
            ):
                return current
            expected_current = current
        client = await self._connect_async(binding, remote_profile=profile)
        if not isinstance(client, RemoteProcessOwnerTransport):
            await client.close_async()
            raise RuntimeError("remote process owner transport was not created")
        with self._lock:
            changed = self._clients.get(normalized) is not expected_current
            if not changed:
                self._clients[normalized] = client
                self._bindings.upsert(
                    ProcessOwnerBinding(
                        owner_id=normalized,
                        transport_kind="remote_ssh_unix",
                        endpoint_ref=binding.endpoint_ref,
                        resume_session_id=client.session_id,
                    )
                )
        if changed:
            await complete_on_cancel(client.close_async())
            raise RuntimeError(f"remote process owner binding changed concurrently: {normalized}")
        if expected_current is not None:
            await complete_on_cancel(expected_current.close_async())
        return client

    async def restore_remote_binding_async(
        self,
        *,
        binding: ProcessOwnerBinding,
        profile: Any,
    ) -> RemoteProcessOwnerTransport:
        if binding.transport_kind != "remote_ssh_unix":
            raise ValueError("restore_remote_binding requires a remote binding")
        if not binding.resume_session_id:
            raise ProcessOwnerProtocolError(
                f"remote process owner binding is missing resume_session_id: {binding.owner_id}"
            )
        return await self.bind_remote_async(
            owner_id=binding.owner_id,
            profile=profile,
            endpoint_ref=binding.endpoint_ref,
            resume_session_id=binding.resume_session_id,
        )

    async def start_process_async(
        self,
        *,
        owner_id: str,
        argv: list[str],
        cwd: str,
        env: dict[str, str],
        tty: bool = False,
        pipe_stdin: bool = False,
        pty_columns: int | None = None,
        pty_rows: int | None = None,
        process_id: str = "",
        timeout_seconds: int = 0,
        stdout_path: str = "",
        stderr_path: str = "",
        sandbox_backend: str = "unknown",
    ) -> str:
        reserved_process_id = process_id or f"process-{uuid.uuid4().hex}"
        await (await self.client_for_async(owner_id)).start_async(
            process_id=reserved_process_id,
            argv=argv,
            cwd=cwd,
            env=env,
            tty=tty,
            pipe_stdin=pipe_stdin,
            pty_columns=pty_columns,
            pty_rows=pty_rows,
            timeout_seconds=max(0, int(timeout_seconds)),
            stdout_path=str(stdout_path or ""),
            stderr_path=str(stderr_path or ""),
            sandbox_backend=sandbox_backend,
        )
        return reserved_process_id

    async def start_session_async(
        self,
        *,
        acquisition_failure_callback: Callable[[ProcessOwnerSessionView], None] | None = None,
        **kwargs: Any,
    ) -> ProcessOwnerSessionView:
        owner_id = kwargs['owner_id']
        with self._lock:
            binding = self.bindings.get(owner_id)
            if binding is not None and binding.transport_kind == 'local_unix' and not self.has_client(owner_id):
                # New work gets its own owner; historical handles still address their original session.
                kwargs['owner_id'] = self._launch_owners.setdefault(owner_id, f'{owner_id}-{uuid.uuid4().hex}')
        await self.client_for_async(kwargs['owner_id'])
        kwargs['process_id'] = kwargs.get('process_id') or f'process-{uuid.uuid4().hex}'
        # Start acknowledgements are bounded RPCs. Do not abandon a process
        # between its remote creation and registration in this owner.
        try:
            return await complete_on_cancel(self._acquire_session_async(**kwargs))
        except (Exception, asyncio.CancelledError):
            if acquisition_failure_callback is not None:
                with self._lock:
                    view = next((item for item in self._sessions.values() if (
                        item.owner_id == kwargs['owner_id']
                        and item.process_id == kwargs['process_id']
                        and item.run_id == kwargs['run_id']
                        and item.actor_id == kwargs['actor_id']
                    )), None)
                if view is not None:
                    try:
                        acquisition_failure_callback(view)
                    except (Exception, asyncio.CancelledError):
                        logging.getLogger(__name__).exception(
                            'Acquired process failure handoff failed: owner=%s process=%s',
                            view.owner_id, view.process_id,
                        )
            raise

    async def _acquire_session_async(
        self,
        *,
        owner_id: str,
        run_id: str,
        actor_id: str,
        argv: list[str],
        cwd: str,
        env: dict[str, str],
        tty: bool,
        pipe_stdin: bool,
        pty_columns: int | None = None,
        pty_rows: int | None = None,
        process_id: str = "",
        timeout_seconds: int = 0,
        stdout_path: str = "",
        stderr_path: str = "",
        sandbox_backend: str = "unknown",
        command_display: str = "",
    ) -> ProcessOwnerSessionView:
        process_id = process_id or f'process-{uuid.uuid4().hex}'
        with self._lock:
            public_id = self._next_public_session_id
            self._next_public_session_id += 1
            view = ProcessOwnerSessionView(
                public_session_id=public_id,
                owner_id=owner_id,
                process_id=process_id,
                run_id=run_id,
                actor_id=actor_id,
                cwd=cwd,
                stdout_ref=str(stdout_path or ""),
                stderr_ref=str(stderr_path or ""),
                timeout_seconds=max(0, int(timeout_seconds)),
                sandbox_backend=sandbox_backend,
                command_display=str(command_display or ''),
            )
            self._sessions[public_id] = view
            callback = self._resource_callbacks.get((run_id, actor_id))
        try:
            await self.start_process_async(
                owner_id=owner_id,
                argv=argv,
                cwd=cwd,
                env=env,
                tty=tty,
                pipe_stdin=pipe_stdin,
                pty_columns=pty_columns,
                pty_rows=pty_rows,
                process_id=process_id,
                timeout_seconds=timeout_seconds,
                stdout_path=stdout_path,
                stderr_path=stderr_path,
                sandbox_backend=sandbox_backend,
            )
        except ProcessOwnerProtocolError as exc:
            if exc.start_outcome == 'not_created':
                with self._lock:
                    self._sessions.pop(public_id, None)
                    view.released = True
                callback = None
            raise
        finally:
            if callback is not None:
                try:
                    pending = callback()
                    if inspect.isawaitable(pending):
                        await pending
                except Exception:
                    logging.getLogger(__name__).exception(
                        'Process resource handoff failed: owner=%s process=%s actor=%s',
                        owner_id, process_id, actor_id,
                    )
        return view

    def bind_actor_resource_callback(
        self, *, run_id: str, actor_id: str, callback: ResourceCallback | None,
    ) -> None:
        """Attach the actor owner's checkpoint at process acquisition, before waits."""
        with self._lock:
            key = (run_id, actor_id)
            if callback is None:
                self._resource_callbacks.pop(key, None)
            else:
                self._resource_callbacks[key] = callback

    def session_for(
        self,
        public_session_id: int,
        *,
        run_id: str,
        actor_id: str = "",
    ) -> ProcessOwnerSessionView:
        with self._lock:
            view = self._sessions.get(int(public_session_id))
        if view is None or view.released:
            raise KeyError(f"unknown process owner session: {public_session_id}")
        if view.run_id and view.run_id != str(run_id or ""):
            raise PermissionError("process owner session belongs to a different run")
        if view.actor_id and actor_id and view.actor_id != actor_id:
            raise PermissionError("process owner session belongs to a different actor")
        return view

    def execution_view(self, handle: Any, *, run_id: str, actor_id: str) -> ProcessOwnerSessionView:
        with self._lock:
            for view in self._sessions.values():
                if (view.owner_id, view.process_id, view.actor_id) == (handle.owner_id, handle.process_id, actor_id):
                    return view
            identity = self._next_public_session_id
            self._next_public_session_id += 1
            view = ProcessOwnerSessionView(
                identity, handle.owner_id, handle.process_id, run_id, actor_id,
                cwd=handle.local_workdir, stdout_ref=handle.stdout_ref,
                stderr_ref=handle.stderr_ref, timeout_seconds=handle.timeout_seconds,
                sandbox_backend=handle.sandbox_backend, command_display=handle.command_display,
            )
            self._sessions[identity] = view
            return view

    async def read_session_async(
        self,
        view: ProcessOwnerSessionView,
        *,
        wait_ms: int,
        max_bytes: int,
        cancel_checker: Callable[[], bool] | None = None,
    ) -> dict[str, Any]:
        response = await (await self.client_for_async(view.owner_id)).read_async(
            process_id=view.process_id,
            after_seq=view.cursor,
            max_bytes=max_bytes,
            wait_ms=wait_ms,
            **({'cancel_checker': cancel_checker} if cancel_checker is not None else {}),
        )
        chunks = response.get("chunks", [])
        if isinstance(chunks, list):
            for item in chunks:
                if isinstance(item, dict):
                    view.cursor = max(view.cursor, int(item.get("seq", 0) or 0))
        return response

    async def write_session_async(
        self,
        view: ProcessOwnerSessionView,
        *,
        chunk: str,
        write_id: str,
    ) -> dict[str, Any]:
        if not self.has_client(view.owner_id) and self.bindings.get(view.owner_id) is None:
            raise ProcessOwnerProtocolError('original process owner is unavailable')
        return await (await self.client_for_async(view.owner_id)).write_async(
            process_id=view.process_id,
            chunk=chunk,
            write_id=write_id,
        )

    async def terminate_session_async(self, view: ProcessOwnerSessionView) -> None:
        try:
            await (await self.client_for_async(view.owner_id)).terminate_async(process_id=view.process_id)
        except Exception:
            pass

    async def read_process_async(
        self,
        *,
        owner_id: str,
        process_id: str,
        after_seq: int = 0,
        wait_ms: int = 0,
        max_bytes: int = 65536,
    ) -> dict[str, Any]:
        if not self.has_client(owner_id) and self.bindings.get(owner_id) is None:
            raise ProcessOwnerProtocolError(f"original process owner is unavailable: {owner_id}")
        return await (await self.client_for_async(owner_id)).read_async(
            process_id=process_id,
            after_seq=max(0, int(after_seq)),
            wait_ms=max(0, int(wait_ms)),
            max_bytes=max(1, int(max_bytes)),
        )

    async def terminate_process_async(
        self,
        *,
        owner_id: str,
        process_id: str,
    ) -> None:
        if not self.has_client(owner_id) and self.bindings.get(owner_id) is None:
            raise ProcessOwnerProtocolError('original process owner is unavailable')
        await (await self.client_for_async(owner_id)).terminate_async(process_id=process_id)

    async def release_process_async(
        self,
        *,
        owner_id: str,
        process_id: str,
        final_seq: int,
    ) -> dict[str, Any]:
        result = await (await self.client_for_async(owner_id)).release_async(
            process_id=process_id,
            final_seq=max(0, int(final_seq)),
        )
        with self._lock:
            matching_ids = [
                session_id
                for session_id, view in self._sessions.items()
                if view.owner_id == owner_id and view.process_id == process_id
            ]
            for session_id in matching_ids:
                view = self._sessions.pop(session_id)
                view.released = True
        return result

    async def release_session_async(self, view: ProcessOwnerSessionView, final_seq: int) -> None:
        await complete_on_cancel(self._release_session_async(view, final_seq))

    async def _release_session_async(self, view: ProcessOwnerSessionView, final_seq: int) -> None:
        if final_seq > 0:
            await (await self.client_for_async(view.owner_id)).release_async(
                process_id=view.process_id,
                final_seq=final_seq,
            )
        view.released = True
        with self._lock:
            self._sessions.pop(view.public_session_id, None)

    def actor_process_refs(self, *, run_id: str, actor_id: str) -> tuple[tuple[str, str], ...]:
        """Snapshot process references belonging to exactly one actor generation."""
        with self._lock:
            return tuple(
                (view.owner_id, view.process_id)
                for view in self._sessions.values()
                if view.run_id == run_id and view.actor_id == actor_id and not view.released
            )

    async def close_owner_async(self, owner_id: str, *, drain: bool = True) -> None:
        normalized = _require_owner_id(owner_id)
        with self._lock:
            client = self._clients.pop(normalized, None)
        if client is None:
            return
        try:
            if drain:
                await client.drain_and_shutdown_async()
        except Exception:
            pass
        finally:
            await complete_on_cancel(client.close_async())

    async def drain_async(self) -> dict[str, dict[str, Any]]:
        with self._lock:
            clients = tuple(self._clients.items())
        results: dict[str, dict[str, Any]] = {}
        errors: list[tuple[str, Exception]] = []
        for owner_id, client in clients:
            try:
                results[owner_id] = dict(await client.drain_and_shutdown_async())
            except Exception as exc:
                errors.append((owner_id, exc))
        if errors:
            failure = RuntimeError(f"failed to drain {len(errors)} process owner(s)")
            for owner_id, error in errors:
                failure.add_note(f"{owner_id}: {type(error).__name__}: {error}")
            raise failure from errors[0][1]
        return results

    async def close_async(self, *, drain: bool = True) -> None:
        with self._lock:
            owner_ids = tuple(self._clients)
        for owner_id in owner_ids:
            await self.close_owner_async(owner_id, drain=drain)

    def client_for(self, owner_id: str, **kwargs: Any) -> Any:
        return asyncio.run(self.client_for_async(owner_id, **kwargs))

    def bind_remote(self, **kwargs: Any) -> Any:
        return asyncio.run(self.bind_remote_async(**kwargs))

    def restore_remote_binding(self, **kwargs: Any) -> Any:
        return asyncio.run(self.restore_remote_binding_async(**kwargs))

    def start_process(self, **kwargs: Any) -> Any:
        return asyncio.run(self.start_process_async(**kwargs))

    def start_session(self, **kwargs: Any) -> Any:
        return asyncio.run(self.start_session_async(**kwargs))

    def read_session(self, view: ProcessOwnerSessionView, **kwargs: Any) -> Any:
        return asyncio.run(self.read_session_async(view, **kwargs))

    def write_session(self, view: ProcessOwnerSessionView, **kwargs: Any) -> Any:
        return asyncio.run(self.write_session_async(view, **kwargs))

    def terminate_session(self, view: ProcessOwnerSessionView, **kwargs: Any) -> Any:
        return asyncio.run(self.terminate_session_async(view, **kwargs))

    def read_process(self, **kwargs: Any) -> Any:
        return asyncio.run(self.read_process_async(**kwargs))

    def terminate_process(self, **kwargs: Any) -> Any:
        return asyncio.run(self.terminate_process_async(**kwargs))

    def release_process(self, **kwargs: Any) -> Any:
        return asyncio.run(self.release_process_async(**kwargs))

    def release_session(self, view: ProcessOwnerSessionView, final_seq: int, **kwargs: Any) -> Any:
        return asyncio.run(self.release_session_async(view, final_seq, **kwargs))

    def close_owner(self, owner_id: str, **kwargs: Any) -> Any:
        return asyncio.run(self.close_owner_async(owner_id, **kwargs))

    def drain(self, **kwargs: Any) -> Any:
        return asyncio.run(self.drain_async(**kwargs))

    def close(self, **kwargs: Any) -> Any:
        return asyncio.run(self.close_async(**kwargs))

    async def _connect_async(
        self,
        binding: ProcessOwnerBinding,
        *,
        remote_profile: Any | None = None,
    ) -> ProcessOwnerClient | RemoteProcessOwnerTransport:
        def notification_handler(method: str, params: dict[str, Any]) -> None:
            self._notify(binding.owner_id, method, params)

        if binding.transport_kind == "local_unix":
            if not binding.endpoint_ref or not binding.resume_session_id:
                raise ProcessOwnerProtocolError('original local owner endpoint/session is unavailable')
            return await ProcessOwnerClient.connect_unix_async(
                socket_path=binding.endpoint_ref,
                resume_session_id=binding.resume_session_id,
                notification_handler=notification_handler,
            )
        if remote_profile is None:
            raise RuntimeError(
                f"remote process owner {binding.owner_id} requires its transient SSH profile"
            )
        return await connect_remote_owner_async(
            profile=remote_profile,
            endpoint_ref=binding.endpoint_ref,
            resume_session_id=binding.resume_session_id,
            notification_handler=notification_handler,
        )

    def _notify(
        self,
        owner_id: str,
        method: str,
        params: dict[str, Any],
    ) -> None:
        if self._notification_handler is None or method == "process/output_delta":
            return
        process_id = str(params.get("process_id", "") or "")
        seq = int(params.get("seq", 0) or 0)
        if process_id and seq > 0:
            key = (owner_id, process_id)
            with self._lock:
                if seq <= self._notification_sequences.get(key, 0):
                    return
                self._notification_sequences[key] = seq
        self._notification_handler(
            method,
            {"owner_id": owner_id, **dict(params)},
        )

    async def _close_locked_async(self, owner_id: str) -> None:
        client = self._clients.pop(owner_id, None)
        if client is None:
            return
        try:
            await client.close_async()
        except Exception:
            pass


def _require_owner_id(value: str) -> str:
    normalized = str(value or "").strip()
    if not normalized or any(character.isspace() for character in normalized):
        raise ValueError("process owner owner_id must be a non-empty token")
    return normalized


__all__ = ["ProcessOwnerManager", "ProcessOwnerSessionView"]
