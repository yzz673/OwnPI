"""Tag wheels containing the standalone Rust executable for their host platform."""
from pathlib import Path
import runpy

from setuptools import setup
from wheel.bdist_wheel import bdist_wheel


class TuiWheel(bdist_wheel):
    def finalize_options(self):
        super().finalize_options()
        root = Path(__file__).parent
        binary = root / "src/ownpi/resources/bin/ownpi-tui-bin"
        self.has_tui_binary = binary.is_file()
        if self.has_tui_binary:
            checks = runpy.run_path(str(root / "src/ownpi/tui_bridge/binary.py"))
            info = checks["check_binary"](binary, execute=False)
            self.tui_platform = checks["wheel_platform"](info)
            if self.plat_name_supplied and self.plat_name != self.tui_platform:
                raise ValueError("requested wheel platform does not match TUI binary")
            self.root_is_pure = False

    def get_tag(self):
        python, abi, platform = super().get_tag()
        return ("py3", "none", self.tui_platform) if self.has_tui_binary else (python, abi, platform)


setup(cmdclass={"bdist_wheel": TuiWheel})
