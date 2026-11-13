"""Standalone native-payload checks, also loaded by the isolated wheel builder."""
from __future__ import annotations

import configparser
import hashlib
import os
from pathlib import Path, PurePosixPath
import platform
import stat
import struct
import subprocess
import zipfile


BINARY_RELATIVE = "ownpi/resources/bin/ownpi-tui-bin"
ENTRYPOINTS = {
    "ownpi": "ownpi.cli.main:main",
    "ownpi-tui": "ownpi.tui_bridge.launcher:main",
}


def host_target() -> str:
    machine = {"aarch64": "arm64", "amd64": "x86_64"}.get(
        platform.machine().lower(), platform.machine().lower()
    )
    target = f"{platform.system().lower()}-{machine}"
    if target not in {"darwin-arm64", "darwin-x86_64", "linux-arm64", "linux-x86_64"}:
        raise ValueError(f"unsupported TUI host: {target}")
    return target


def inspect_binary(data: bytes, *, target: str | None = None) -> dict[str, object]:
    minimum = (0, 0, 0)
    if data[:4] == b"\x7fELF":
        if len(data) < 64 or data[4:7] != b"\x02\x01\x01":
            raise ValueError("TUI must be a complete little-endian ELF64 executable")
        kind, machine = struct.unpack_from("<HH", data, 16)
        offset = struct.unpack_from("<Q", data, 32)[0]
        size, count = struct.unpack_from("<HH", data, 54)
        if kind not in (2, 3) or not count or size < 56 or offset < 64 or offset + size * count > len(data):
            raise ValueError("invalid or truncated TUI ELF program headers")
        for index in range(count):
            header = offset + index * size
            file_offset = struct.unpack_from("<Q", data, header + 8)[0]
            file_size = struct.unpack_from("<Q", data, header + 32)[0]
            if file_size and file_offset + file_size > len(data):
                raise ValueError("truncated TUI ELF segment")
        arch = {62: "x86_64", 183: "arm64"}.get(machine)
        system = "linux"
    elif data[:4] == b"\xcf\xfa\xed\xfe":
        if len(data) < 32:
            raise ValueError("truncated TUI Mach-O header")
        cpu, _, kind, count, size = struct.unpack_from("<IIIII", data, 4)
        if kind != 2 or not count or 32 + size > len(data) or count > size // 8:
            raise ValueError("invalid or truncated TUI Mach-O load commands")
        arch = {0x01000007: "x86_64", 0x0100000C: "arm64"}.get(cpu)
        system = "darwin"
        offset = 32
        found_version = False
        for _ in range(count):
            if offset + 8 > 32 + size:
                raise ValueError("truncated TUI Mach-O load command")
            command, length = struct.unpack_from("<II", data, offset)
            if length < 8 or offset + length > 32 + size:
                raise ValueError("invalid TUI Mach-O load command length")
            if command == 0x19:
                if length < 72:
                    raise ValueError("truncated TUI Mach-O segment command")
                file_offset, file_size = struct.unpack_from("<QQ", data, offset + 40)
                if file_size and file_offset + file_size > len(data):
                    raise ValueError("truncated TUI Mach-O segment")
            if command in (0x32, 0x24):
                required = 24 if command == 0x32 else 16
                if length < required:
                    raise ValueError("truncated TUI Mach-O version command")
                if command == 0x32 and struct.unpack_from("<I", data, offset + 8)[0] != 1:
                    raise ValueError("TUI Mach-O is not a macOS executable")
                version = struct.unpack_from("<I", data, offset + (12 if command == 0x32 else 8))[0]
                minimum = max(minimum, (version >> 16, (version >> 8) & 255, version & 255))
                found_version = True
            offset += length
        if offset != 32 + size or not found_version:
            raise ValueError("TUI Mach-O requires a valid macOS deployment version")
    else:
        raise ValueError("TUI payload is not a supported ELF64 or thin Mach-O executable")
    if arch is None:
        raise ValueError("unsupported TUI CPU architecture")
    actual = f"{system}-{arch}"
    if target is not None and actual != target:
        raise ValueError(f"TUI binary target {actual} does not match {target}")
    return {"target": actual, "minimum_macos": minimum}


def wheel_platform(info: dict[str, object]) -> str:
    target = str(info["target"])
    system, arch = target.split("-", 1)
    if system == "linux":
        return f"linux_{'aarch64' if arch == 'arm64' else arch}"
    major, minor, _ = info["minimum_macos"]
    # macOS 11+ wheel compatibility uses the major release, not minor releases.
    return f"macosx_{major}_{0 if major >= 11 else minor}_{arch}"


def check_binary(path: Path, *, target: str | None = None, execute: bool = True) -> dict[str, object]:
    if not path.is_file() or path.is_symlink() or not os.access(path, os.X_OK):
        raise ValueError(f"TUI binary is missing or not executable: {path}")
    info = inspect_binary(path.read_bytes(), target=target or host_target())
    if execute:
        result = subprocess.run([str(path), "--help"], capture_output=True, text=True, timeout=15, check=False)
        if result.returncode != 0 or "--bridge-python" not in result.stdout:
            raise RuntimeError(f"TUI startup check failed: {(result.stderr or result.stdout)[-1000:]}")
    return {**info, "path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}


def binary_path() -> Path:
    package = Path(__file__).resolve().parents[1]
    packaged = package / "resources/bin/ownpi-tui-bin"
    candidates = [packaged]
    crate = package / "ui"
    if (crate / "Cargo.toml").is_file():
        candidates.extend(crate / "target" / mode / "ownpi-tui-bin" for mode in ("release", "debug"))
    for candidate in candidates:
        if candidate.exists():
            check_binary(candidate, execute=False)
            return candidate.resolve()
    raise FileNotFoundError("TUI binary is missing. Reinstall OwnPI; in a source checkout run scripts/build_tui.py.")


def check_installed() -> dict[str, object]:
    return check_binary(binary_path())


def _installed_name(name: str) -> str:
    parts = PurePosixPath(name).parts
    if len(parts) > 2 and parts[0].endswith(".data") and parts[1] in {"purelib", "platlib"}:
        return "/".join(parts[2:])
    return name


def validate_tui_wheel(path: Path, *, target: str) -> None:
    with zipfile.ZipFile(path) as archive:
        names: dict[str, zipfile.ZipInfo] = {}
        for item in archive.infolist():
            name = _installed_name(item.filename)
            if name in names:
                raise ValueError(f"duplicate installed wheel path: {name}")
            names[name] = item
            parts = PurePosixPath(name).parts
            if name.startswith("ownpi/ui/") or any(p in {"target", "vendor", ".cargo"} for p in parts):
                raise ValueError(f"build-only content in Core wheel: {name}")
        required = (BINARY_RELATIVE, "ownpi/tui_bridge/launcher.py", "ownpi/tui_bridge/server.py",
                    "ownpi/tui_bridge/worker.py", "ownpi/tui_bridge/control.py", "ownpi/tui_bridge/binary.py")
        for name in required:
            if name not in names:
                raise ValueError(f"Core wheel lacks TUI payload: {name}")
        item = names[BINARY_RELATIVE]
        mode = item.external_attr >> 16
        if not mode & 0o111 or stat.S_ISLNK(mode):
            raise ValueError("Core wheel TUI binary lacks executable permissions or is a symlink")
        info = inspect_binary(archive.read(item), target=target)
        entries = [n for n in names if n.endswith(".dist-info/entry_points.txt")]
        wheels = [n for n in names if n.endswith(".dist-info/WHEEL")]
        if len(entries) != 1 or len(wheels) != 1:
            raise ValueError("Core wheel lacks unique entry point / WHEEL metadata")
        parser = configparser.ConfigParser(interpolation=None)
        try:
            parser.read_string(archive.read(names[entries[0]]).decode())
        except configparser.Error as error:
            raise ValueError("Core wheel contains invalid entry point metadata") from error
        for command, function in ENTRYPOINTS.items():
            if parser.get("console_scripts", command, fallback="") != function:
                raise ValueError(f"Core wheel lacks the {command} entry point")
        expected = f"py3-none-{wheel_platform(info)}"
        tags = [line[5:] for line in archive.read(names[wheels[0]]).decode().splitlines() if line.startswith("Tag: ")]
        if tags != [expected] or not path.name.endswith(f"-{expected}.whl"):
            raise ValueError(f"Core wheel platform must match its TUI binary: {expected}")
