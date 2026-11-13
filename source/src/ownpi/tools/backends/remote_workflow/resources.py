from __future__ import annotations

import csv
import io
import shlex
from typing import Any

from ownpi.tools.backends.remote_workflow.profiles import RemoteProfile
from ownpi.tools.backends.remote_workflow.results import WorkflowResult
from ownpi.tools.backends.remote_workflow.shell import RemoteWorkflowGateway


GPU_QUERY_FIELDS = (
    "index",
    "name",
    "memory.total",
    "memory.used",
    "memory.free",
    "utilization.gpu",
    "temperature.gpu",
)

PROCESS_QUERY_FIELDS = (
    "pid",
    "process_name",
    "gpu_uuid",
    "used_memory",
)


async def resources(profile: RemoteProfile, *, gateway: RemoteWorkflowGateway) -> WorkflowResult:
    script = "\n".join(
        [
            "set +e",
            "printf '__REMOTE_RESOURCES_SECTION__ gpu_csv\\n'",
            _nvidia_query_command("gpu", GPU_QUERY_FIELDS),
            "printf '__REMOTE_RESOURCES_SECTION__ compute_csv\\n'",
            _nvidia_query_command("compute-apps", PROCESS_QUERY_FIELDS),
            "printf '__REMOTE_RESOURCES_SECTION__ memory\\n'",
            "free -m 2>/dev/null || true",
            "printf '__REMOTE_RESOURCES_SECTION__ disk\\n'",
            f"df -h {shlex.quote(profile.remote_root)} 2>/dev/null || df -h . 2>/dev/null || true",
            "printf '__REMOTE_RESOURCES_SECTION__ tmux\\n'",
            "tmux ls 2>/dev/null || true",
        ]
    )
    remote = await gateway.run_remote_bash(script, name="remote_resources", timeout_seconds=30)
    sections = _split_sections(remote.stdout)
    gpus = _parse_gpu_rows(sections.get("gpu_csv", ""))
    processes = _parse_process_rows(sections.get("compute_csv", ""))
    memory = _parse_memory(sections.get("memory", ""))
    disk = _parse_disk(sections.get("disk", ""))
    tmux_sessions = _parse_tmux_sessions(sections.get("tmux", ""))
    recommendation = _recommend_gpu(gpus)
    warnings: list[str] = []
    if not gpus:
        warnings.append("nvidia-smi unavailable or no GPUs detected")
    data = {
        **{
            key: value
            for key, value in remote.data.items()
            if key
            in {
                'canonical_gateway',
                'environment_receipt_id',
                'permission_profile_id',
                'permission_policy_hash',
            }
        },
        "profile": profile.name,
        "host": profile.host,
        "remote_root": profile.remote_root,
        "gpu_count": len(gpus),
        "gpus": gpus,
        "gpu_processes": processes,
        "recommended_gpu": recommendation,
        "memory": memory,
        "disk": disk,
        "tmux_sessions": tmux_sessions,
    }
    return WorkflowResult(
        ok=remote.ok,
        command="resources",
        summary=_resource_summary(gpus, recommendation),
        stdout=remote.stdout,
        stderr=remote.stderr,
        returncode=remote.returncode,
        data=data,
        warnings=tuple(warnings),
    )


def _nvidia_query_command(kind: str, fields: tuple[str, ...]) -> str:
    return (
        f"if command -v nvidia-smi >/dev/null 2>&1; then "
        f"nvidia-smi --query-{kind}={','.join(fields)} --format=csv,noheader,nounits 2>/dev/null || true; "
        "fi"
    )


def _split_sections(text: str) -> dict[str, str]:
    sections: dict[str, list[str]] = {}
    current = ""
    for line in text.splitlines():
        if line.startswith("__REMOTE_RESOURCES_SECTION__ "):
            current = line.split(" ", 1)[1].strip()
            sections.setdefault(current, [])
            continue
        if current:
            sections.setdefault(current, []).append(line)
    return {key: "\n".join(lines).strip() for key, lines in sections.items()}


def _parse_gpu_rows(text: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for raw in _csv_rows(text):
        if len(raw) < len(GPU_QUERY_FIELDS):
            continue
        total = _to_int(raw[2])
        used = _to_int(raw[3])
        free = _to_int(raw[4])
        utilization = _to_int(raw[5])
        rows.append(
            {
                "index": _to_int(raw[0]),
                "name": raw[1].strip(),
                "memory_total_mb": total,
                "memory_used_mb": used,
                "memory_free_mb": free,
                "memory_free_ratio": round(free / total, 4) if total > 0 else 0.0,
                "utilization_gpu_percent": utilization,
                "temperature_gpu_c": _to_int(raw[6]),
                "status": _gpu_status(free_mb=free, total_mb=total, utilization_percent=utilization),
            }
        )
    return rows


def _parse_process_rows(text: str) -> list[dict[str, Any]]:
    processes: list[dict[str, Any]] = []
    for raw in _csv_rows(text):
        if len(raw) < len(PROCESS_QUERY_FIELDS):
            continue
        processes.append(
            {
                "pid": _to_int(raw[0]),
                "process_name": raw[1].strip(),
                "gpu_uuid": raw[2].strip(),
                "used_memory_mb": _to_int(raw[3]),
            }
        )
    return processes


def _csv_rows(text: str) -> list[list[str]]:
    return [row for row in csv.reader(io.StringIO(text)) if row and any(cell.strip() for cell in row)]


def _parse_memory(text: str) -> dict[str, int]:
    for line in text.splitlines():
        parts = line.split()
        if parts and parts[0].rstrip(":").casefold() == "mem" and len(parts) >= 7:
            return {
                "total_mb": _to_int(parts[1]),
                "used_mb": _to_int(parts[2]),
                "free_mb": _to_int(parts[3]),
                "available_mb": _to_int(parts[6]),
            }
    return {}


def _parse_disk(text: str) -> list[dict[str, str]]:
    lines = [line.split() for line in text.splitlines() if line.strip()]
    if len(lines) < 2:
        return []
    rows: list[dict[str, str]] = []
    for parts in lines[1:]:
        if len(parts) < 6:
            continue
        rows.append(
            {
                "filesystem": parts[0],
                "size": parts[1],
                "used": parts[2],
                "available": parts[3],
                "use_percent": parts[4],
                "mounted_on": parts[5],
            }
        )
    return rows


def _parse_tmux_sessions(text: str) -> list[str]:
    return [line.strip() for line in text.splitlines() if line.strip()]


def _recommend_gpu(gpus: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not gpus:
        return None
    ranked = sorted(
        gpus,
        key=lambda item: (
            int(item.get("memory_free_mb", 0)),
            -int(item.get("utilization_gpu_percent", 100)),
        ),
        reverse=True,
    )
    best = ranked[0]
    reason = (
        f"largest free GPU memory ({best.get('memory_free_mb', 0)} MB) "
        f"with {best.get('utilization_gpu_percent', 0)}% utilization"
    )
    return {
        "index": best.get("index"),
        "status": best.get("status"),
        "memory_free_mb": best.get("memory_free_mb"),
        "utilization_gpu_percent": best.get("utilization_gpu_percent"),
        "reason": reason,
        "cuda_visible_devices": str(best.get("index")),
    }


def _gpu_status(*, free_mb: int, total_mb: int, utilization_percent: int) -> str:
    free_ratio = free_mb / total_mb if total_mb > 0 else 0.0
    if free_ratio >= 0.75 and utilization_percent <= 20:
        return "idle"
    if free_ratio >= 0.4 and utilization_percent <= 60:
        return "usable"
    return "busy"


def _resource_summary(gpus: list[dict[str, Any]], recommendation: dict[str, Any] | None) -> str:
    if not gpus:
        return "remote resources checked; no GPU data available"
    if recommendation is None:
        return f"remote resources checked; {len(gpus)} GPU(s) found"
    return f"remote resources checked; recommended GPU {recommendation.get('index')}"


def _to_int(value: Any) -> int:
    text = str(value or "").strip().replace("MiB", "").replace("%", "")
    try:
        return int(float(text))
    except ValueError:
        return 0
