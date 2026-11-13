"""Same-host identity for local process owners."""

import os
from pathlib import Path
import platform
from uuid import getnode


def local_host_scope() -> dict[str, str] | None:
    if os.name != "posix":
        return None
    namespace = ""
    if platform.system() == "Linux":
        try:
            namespace = os.readlink(Path("/proc/self/ns/pid"))
        except OSError:
            return None
    return {"system": platform.system(), "node": platform.node(),
            "hardware_node": str(getnode()), "pid_namespace": namespace}
