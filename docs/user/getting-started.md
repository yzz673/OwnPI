# Getting Started

[中文](getting-started.zh-CN.md) | **English**

## Install

The current Beta provides packages for Apple Silicon macOS (arm64) and Linux x86_64 through GitHub Releases. The installer includes Python and the dependencies needed to run OwnPI; no Conda environment, source checkout, or `sudo` is required.

```bash
curl -fsSL https://raw.githubusercontent.com/yzz673/OwnPI/main/install.sh | bash
```

The default app home is `~/.ownpi`, and the command is installed at `~/.local/bin/ownpi`. If your terminal cannot find `ownpi`, add its directory to the current shell's `PATH`:

```bash
export PATH="$HOME/.local/bin:$PATH"
```

To keep this setting in new terminals, add that line to your shell's startup file. To use another app home, set `OWNPI_HOME` before installation and keep the same setting for subsequent commands; see [Configuration](configuration.md).

## Configure model access

Have your provider type, API key, Base URL, and exact model name ready. The OpenAI-compatible setup option uses the Responses API; a service that only supports Chat Completions is not sufficient for this setup path. The Anthropic-compatible option uses the Messages API. Check that your service supports the selected interface and tool use, then run:

```bash
ownpi setup
ownpi doctor
```

Setup also asks for reasoning effort where applicable and a review profile. Keep the default `auto` review profile to start. Use the Base URL supplied by your provider; do not append an individual request endpoint such as `/responses` or `/messages`. Settings are stored in `~/.ownpi/config.yaml`, and credentials in `~/.ownpi/.env`. Re-running setup replaces those generated files, so back up manual changes first.

`doctor` checks the installation and configuration. It does not make a real model request; successful provider calls during the first task confirm that the connection works. See [Troubleshooting](../troubleshooting.md) if that request fails.

The Beta runs the Research Level workflow through the CLI with unattended full access. Use a dedicated workspace without sensitive files. TUI and interactive permission approval are not part of the supported Beta workflow.

## Optional Neuroscience Pack

```bash
ownpi pack install neuroscience
ownpi pack status neuroscience
ownpi tools doctor brainlib
```

The Pack adds Brainlib, neuroscience skills, and research knowledge. OwnPI Core works without it. Skip this step if your task does not need those capabilities.

## Start your first task

Create a UTF-8 file named `task.md` with a research brief. Describe the question or direction, intended contribution, available datasets/code/papers with concrete paths, practical constraints, and expected deliverables. You do not need to design every experiment in advance: identify the decisions OwnPI should investigate. A short topic name alone leaves these requirements unspecified.

Use this template and replace every bracketed field with your own information:

```text
Treat this as an end-to-end research project in [research area].
Research question or direction: [what should the study understand, explain, or establish?]
Intended contribution: [the new method, explanation, or evidence the project should produce].
Available resources: [datasets, code, papers, tools, and Python environments, with concrete paths and any access instructions].
Constraints: [available compute, time or cost limits, data restrictions, and other requirements].
Expected deliverables: [manuscript, supplementary materials, source data, source code, or other outputs].
Develop and execute the research plan, evaluate the evidence, and state the limits of the conclusions.
```

For example, a sleep-and-memory project should specify which recordings and behavioral measurements are available, where they are stored, what relationship to investigate, and what evidence and deliverables are expected. Asking only to “develop a research plan” requests a planning output; use that wording only when a plan is the intended result.

Save the file, then start the run with your actual task-file and workspace paths:

```bash
ownpi run start --message-file task.md --workspace /path/to/workspace
```

Omit `--workspace` to use the current directory. The workspace is where OwnPI works on research files; choose a dedicated directory. The command runs in the foreground by default. Add `--background` to return to the shell while it runs, then use `run list` to find the run ID and check its state:

```bash
ownpi run list
ownpi run status <run_id>
```

Configuration and managed run records are kept in `~/.ownpi`. Press **Ctrl-C** to stop a foreground run, or use the following commands from another terminal with the same app home:

```bash
ownpi run stop <run_id>
ownpi run resume <run_id>
```

Resume works after a user stop or an unexpected process exit and may repeat unfinished work. Add `--background` to resume in the background. See the [CLI Reference](cli-reference.md) for parameters and status fields.

## Find your results

Use `ownpi run status <run_id>` to check progress. `State: completed` indicates that the run has finished; `Runtime active: no` alone does not, because stopped or failed runs are also inactive. Completion does not guarantee that every requested deliverable meets your requirements; review the findings and limitations.

Read the run's closing summary and inspect the workspace for the generated files. Final deliverables are usually organized under `<workspace>/output_synthesis/final_delivery/`, often with a `README.md` describing their locations and use. The model writes these artifacts, so the actual directories and filenames can vary; follow the delivery instructions and files produced by that run. Partial outputs from an unfinished run should not be assumed to be a final delivery.

## Update

```bash
ownpi update
ownpi pack update neuroscience
```

The second command is needed only if you installed the Pack. Updates preserve your settings and run records.

See [Configuration](configuration.md) to change settings and [Troubleshooting](../troubleshooting.md) for common problems.
