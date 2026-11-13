from __future__ import annotations

import ctypes
import errno
import os
import signal
import struct
import subprocess
import time
from typing import Any, Callable, cast

from ownpi.runtime.process_owner.protocol import ProcessOwnerProtocolError


def process_preexec() -> Callable[[], None] | None:
    if os.name != "posix":
        return None
    parent_pid = os.getpid()

    def _prepare() -> None:
        os.setsid()
        if sys_platform_linux():
            libc = ctypes.CDLL(None, use_errno=True)
            if libc.prctl(1, signal.SIGTERM) != 0:
                error_number = ctypes.get_errno()
                raise OSError(error_number, os.strerror(error_number))
            if os.getppid() != parent_pid:
                os.kill(os.getpid(), signal.SIGTERM)

    return _prepare


def popen_owned(
    argv: list[str],
    *,
    cwd: str,
    env: dict[str, str],
    stdin: Any,
    stdout: Any,
    stderr: Any,
) -> subprocess.Popen[bytes]:
    kwargs: dict[str, Any] = {
        "cwd": cwd,
        "env": env,
        "stdin": stdin,
        "stdout": stdout,
        "stderr": stderr,
        "text": False,
        "bufsize": 0,
    }
    preexec_fn = process_preexec()
    if preexec_fn is not None:
        kwargs["preexec_fn"] = preexec_fn
    elif os.name == "nt":
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    try:
        return cast(subprocess.Popen[bytes], subprocess.Popen(argv, **kwargs))
    except (OSError, subprocess.SubprocessError, ValueError) as exc:
        # Popen reports launch failure only after disposing of its failed child.
        raise ProcessOwnerProtocolError('process owner request failed', start_outcome='not_created') from exc


def spawn_owned_process(
    argv: list[str],
    *,
    cwd: str,
    env: dict[str, str],
    tty_mode: bool,
    pipe_stdin: bool,
    pty_columns: int | None = None,
    pty_rows: int | None = None,
) -> tuple[subprocess.Popen[bytes], int | None, int | None]:
    if tty_mode:
        if os.name != "posix":
            raise ProcessOwnerProtocolError(
                "PTY execution is unsupported on this platform", start_outcome='not_created',
            )
        import pty

        try:
            master_fd, slave_fd = pty.openpty()
        except OSError as exc:
            raise ProcessOwnerProtocolError('process owner request failed', start_outcome='not_created') from exc
        try:
            try:
                _set_pty_window_size(
                    slave_fd,
                    columns=pty_columns,
                    rows=pty_rows,
                )
            except (OSError, ValueError) as exc:
                raise ProcessOwnerProtocolError('process owner request failed', start_outcome='not_created') from exc
            process = popen_owned(
                argv,
                cwd=cwd,
                env=env,
                stdin=slave_fd,
                stdout=slave_fd,
                stderr=slave_fd,
            )
        except Exception:
            close_fd(master_fd)
            raise
        finally:
            close_fd(slave_fd)
        return process, master_fd, _spawned_process_group_id(process)
    process = popen_owned(
        argv,
        cwd=cwd,
        env=env,
        stdin=subprocess.PIPE if pipe_stdin else subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    return process, None, _spawned_process_group_id(process)


def _set_pty_window_size(
    fd: int,
    *,
    columns: int | None,
    rows: int | None,
) -> None:
    if columns is None and rows is None:
        return
    import fcntl
    import termios

    height = max(1, int(rows or 40))
    width = max(1, int(columns or 120))
    fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", height, width, 0, 0))


def terminate_process_tree(
    process: subprocess.Popen[Any],
    *,
    process_group_id: int | None = None,
    grace_seconds: float = 2.0,
) -> None:
    if process.poll() is not None and process_group_id is None:
        return
    _signal_process_tree(
        process,
        signal.SIGTERM,
        process_group_id=process_group_id,
    )
    deadline = time.monotonic() + max(0.0, float(grace_seconds))
    while _owned_process_tree_alive(process, process_group_id) and time.monotonic() < deadline:
        time.sleep(0.02)
    if _owned_process_tree_alive(process, process_group_id):
        _kill_process_tree(process, process_group_id=process_group_id)
    try:
        process.wait(timeout=max(0.1, grace_seconds))
    except subprocess.TimeoutExpired:
        process.kill()


def signal_process_tree(
    process: subprocess.Popen[Any],
    signal_number: int,
    *,
    process_group_id: int | None = None,
) -> None:
    if process.poll() is not None and process_group_id is None:
        return
    _signal_process_tree(
        process,
        signal_number,
        process_group_id=process_group_id,
    )


def _signal_process_tree(
    process: subprocess.Popen[Any],
    signal_number: int,
    *,
    process_group_id: int | None = None,
) -> None:
    try:
        if os.name == "posix":
            group_id = process_group_id or os.getpgid(process.pid)
            os.killpg(group_id, signal_number)
        elif os.name == "nt":
            if signal_number == signal.SIGTERM:
                _windows_taskkill(process.pid, force=False)
            else:
                process.send_signal(signal_number)
        else:
            process.send_signal(signal_number)
    except (ProcessLookupError, PermissionError):
        return
    except OSError as exc:
        if exc.errno != errno.ESRCH:
            raise


def _kill_process_tree(
    process: subprocess.Popen[Any],
    *,
    process_group_id: int | None = None,
) -> None:
    if os.name == "nt":
        if not _windows_taskkill(process.pid, force=True) and process.poll() is None:
            process.kill()
        return
    _signal_process_tree(
        process,
        signal.SIGKILL,
        process_group_id=process_group_id,
    )


def _spawned_process_group_id(process: subprocess.Popen[Any]) -> int | None:
    if os.name != "posix":
        return None
    # popen_owned() creates a new session, so the spawned PID is the stable PGID.
    return process.pid


def _owned_process_tree_alive(
    process: subprocess.Popen[Any],
    process_group_id: int | None,
) -> bool:
    if os.name != "posix" or process_group_id is None:
        return process.poll() is None
    return process_group_alive(process_group_id)


def process_group_alive(process_group_id: int) -> bool:
    """Probe a POSIX process group without signaling or adopting its members."""
    if os.name != "posix":
        raise NotImplementedError("process group probing requires POSIX")
    if type(process_group_id) is not int or process_group_id <= 0:
        raise ValueError("process group id must be a positive integer")
    try:
        os.killpg(process_group_id, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _windows_taskkill(pid: int, *, force: bool) -> bool:
    """Best-effort tree cleanup matching Codex's Windows fallback contract."""
    command = ["taskkill", "/PID", str(pid), "/T"]
    if force:
        command.append("/F")
    try:
        result = subprocess.run(
            command,
            check=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except OSError:
        return False
    return result.returncode == 0


def close_process_resources(
    process: subprocess.Popen[Any], master_fd: int | None
) -> None:
    if master_fd is not None:
        close_fd(master_fd)
    for stream in (process.stdin, process.stdout, process.stderr):
        try:
            if stream is not None:
                stream.close()
        except OSError:
            continue


def close_fd(fd: int) -> None:
    try:
        os.close(fd)
    except OSError:
        pass


def sys_platform_linux() -> bool:
    import sys

    return sys.platform.startswith("linux")
