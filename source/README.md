# OwnPI Source Package

[中文](README.zh-CN.md) | **English**

The recommended way to run OwnPI is to install a release from the [OwnPI product repository](https://github.com/yzz673/OwnPI). This source package provides an optional way to inspect, modify, and run the code. Users are responsible for preparing its dependencies; no dependency environment, automatic provisioning scripts, or release-packaging tools are included.

## Source Layout

All paths are relative to the extracted package root.

| Path | Contents |
| --- | --- |
| `src/ownpi/cli/`, `src/ownpi/chat/` | CLI entry and interactive session admission. |
| `src/ownpi/ui/`, `src/ownpi/tui_bridge/` | Rust terminal client and Python Runtime bridge. |
| `src/ownpi/controller/`, `src/ownpi/task_authoring/` | Workflow orchestration, critic sessions, and Research task preparation. |
| `src/ownpi/runtime/` | Native model/tool execution, prompt assembly, history, recovery, knowledge, and execution boundaries. |
| `src/ownpi/llm/`, `src/ownpi/subagent/`, `src/ownpi/tools/` | Provider transport, delegated work, and tool implementations. |
| `src/ownpi/static_semantic_contracts/content/` | Role and stage instructions. |
| `src/ownpi/skills/bundled/content/` | Skill instructions, scripts, templates, and reference assets. |
| `src/ownpi/resources/`, `src/ownpi/config/`, `src/ownpi/product/` | Packaged resources, configuration, and product lifecycle code. |
| `pyproject.toml`, `setup.py`, `uv.lock` | Python dependency declarations, editable-install metadata, and dependency lock. |

The source package does not include internal plans, developer rerun or observability tooling, or the full Python test suite. Rust tests and their fixtures remain with the TUI source. Skills include non-code resources needed at runtime; they are not generated research outputs.

## Dependencies To Prepare

- **Python:** Python 3.11 or newer in a user-managed environment, with `pip`, `setuptools>=68`, `wheel`, and all dependencies listed in `pyproject.toml` under `project.dependencies`. Prepare these in the same environment used to run OwnPI. `uv.lock` records resolved dependencies; the editable-install command below does not apply that lock automatically.
- **TUI only:** a native macOS or glibc-based Linux host, arm64 or x86_64; a compatible Rust toolchain with `cargo` and `rustc`; and the host compiler/linker and development libraries required by the crate. Prepare the dependencies recorded in `src/ownpi/ui/Cargo.lock` in your Cargo cache before the offline build. Python-only CLI use does not require compiling the TUI.
- **Task-specific tools:** install any external programs, optional Python dependencies, browser assets, scientific environments, and datasets required by your task. For example, paper compilation requires a suitable TeX environment. Optional Python dependencies are declared in `project.optional-dependencies`; neither their external dependencies nor optional Pack assets are supplied here.
- **Model access:** configure a supported provider and credentials before running model-backed tasks, following the product documentation. No credentials are supplied.

The commands below assume dependencies are already available. They do not download or install them for you. A private development installation or an existing OwnPI release is not required.

## Run From Source

Activate your prepared Python environment and run from the extracted package root:

```bash
python -m pip install --no-deps --no-build-isolation -e .
python -m pip check
ownpi --help
```

Editable installation registers the CLI/TUI commands while loading Python code from this directory. Keep the directory in place. Ordinary Python edits do not require rebuilding a release; changes to dependencies or entry-point metadata require updating the environment or repeating the installation.

For the TUI, compile the native client using your prepared Rust environment. Use the host target, without cross-compilation overrides:

```bash
cargo build --manifest-path src/ownpi/ui/Cargo.toml --target-dir src/ownpi/ui/target --release --locked --offline
ownpi-tui --help
```

The launcher can load `src/ownpi/ui/target/release/ownpi-tui-bin` directly. Rust source changes require recompiling that binary, not producing a release package. To run the included Rust tests, use the same Cargo arguments with `test` instead of `build`.

Check where Python code and the TUI binary are loaded from:

```bash
python -c 'import ownpi; print(ownpi.__file__)'
python -c 'from ownpi.tui_bridge.binary import binary_path; print(binary_path())'
```

The paths should point into this extracted source tree; the second check requires the TUI build. Help and path checks do not start a model request or prove provider connectivity. Follow the product documentation for configuration and task commands.

## Distribution Boundaries

This is not the managed Base release and does not include portable Python, LaTeX, scientific environments, optional Neuroscience Pack assets, or the Base/Pack build and publication system. `src/ownpi/product/` remains because it contains runtime configuration, diagnostics, resources, and Pack-management code; it is not just a release builder.

Use a user-managed Python environment for source execution, rather than replacing a managed release installation. This archive is not an `ownpi update` artifact; use product releases for managed installation and upgrades.

## License

Copyright (c) 2026 OwnPI contributors. Unless separately identified, OwnPI-authored code and documentation are licensed under [Apache-2.0](LICENSE). Third-party code, papers, templates, and other reference assets retain their own terms; the project license does not relicense them. See [third-party notices](THIRD_PARTY_NOTICES.md) and notices accompanying those materials.
