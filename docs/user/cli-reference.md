# CLI Reference

[中文](cli-reference.zh-CN.md) | **English**

This reference covers the commands currently exposed by the CLI, including the compatibility commands identified below. For the first installation and a research-task template, see [Getting Started](getting-started.md).

## Conventions

- Replace `<run_id>`, `<run_dir>`, and example paths with your own values. Do not type the angle brackets.
- The default app home is `~/.ownpi`. If you set `OWNPI_HOME`, use the same value in every terminal that manages a run.
- Add `--help` to a command or command group to see its accepted arguments, for example `ownpi run start --help`.
- Where listed, `--config PATH` selects a configuration file instead of `<app_home>/config.yaml`; it does not select another app home.
- Where listed, `--json` requests structured output. It does not put a run in the background or suppress model calls.

## Installation and configuration

### `ownpi init`

```bash
ownpi init [--force]
```

Create app-home directories and missing configuration templates. This does not configure a working model connection. Installation and setup already initialize the app home, so most users do not need to run it separately. `--force` overwrites generated `config.yaml` and `.env.example` templates; it does not replace `.env` with configured credentials. Back up manual configuration before using it.

### `ownpi setup`

```bash
ownpi setup
```

Interactively configure a provider, API key, Base URL, model, reasoning effort where applicable, and review profile. The OpenAI-compatible option uses the Responses API; the Anthropic-compatible option uses the Messages API. Setup replaces the generated `config.yaml` and `.env` rather than adding another provider to them. See [Configuration](configuration.md) for compatibility requirements and settings.

### `ownpi info`

```bash
ownpi info
```

Print a JSON summary of the installed components, app-home and configuration paths, model readiness, and managed resources. Use it to locate the installation or include version information in a problem report. It does not verify a real model request.

### `ownpi doctor`

```bash
ownpi doctor [--json] [--install-check]
```

Diagnose installation resources, configuration, credential availability, and runtime readiness. `--json` prints the diagnostic report as JSON. `--install-check` selects the installation checks intended for installer verification; it does not replace the full check before use. Neither mode sends a real model request.

### `ownpi config show`

```bash
ownpi config show [--config PATH]
```

Print the runtime and model configuration summary, including the selected model profile and effective review policy. This inspects local settings; it does not test model access.

### `ownpi config set-review-profile`

```bash
ownpi config set-review-profile <auto|standard|rigorous|exhaustive> [--config PATH]
```

Save the review profile without rerunning setup. For example, `ownpi config set-review-profile rigorous` selects unlimited reviewer rounds and up to three total Analysis revisits. In the Beta's Research Level workflow, `auto` resolves to `rigorous`. See [Review profile](configuration.md#review-profile) for all budgets.

## Runs

### `ownpi run start`

```bash
ownpi run start [message] [--message-file PATH] [--workspace PATH] [--background] [--json] [--config PATH]
```

Create and execute a new research run. Supply the task as a quoted positional message or a UTF-8 file with `--message-file`. For a research project, use a brief that states the question, resources, constraints, and deliverables; see the [task template](getting-started.md#start-your-first-task). The file is read as task text, not as a YAML or JSON configuration. Use `--message-file -` to read task text from standard input.

`--workspace` selects the research directory, creating it if necessary; it defaults to the directory where you launch the command. `--background` starts a detached process and returns a run ID without waiting for completion. Without it, the run stays in the foreground. `--json` changes the CLI result format, not whether work runs in the foreground.

```bash
ownpi run start --message-file /path/to/task.md --workspace /path/to/project
ownpi run start --message-file /path/to/task.md --workspace /path/to/project --background
```

### `ownpi run list`

```bash
ownpi run list [--json] [--config PATH]
```

List managed runs in the selected app home. Use the returned run ID or run directory with `status`, `stop`, or `resume`. This does not start or resume work. If a run is missing, check that `OWNPI_HOME` points to the app home used to start it.

### `ownpi run status`

```bash
ownpi run status <run_id_or_run_dir> [--json] [--config PATH]
```

Read a run's state and Runtime activity without changing it. The target can be a run ID from `run list` or its session-managed run directory.

| Field | Meaning |
| --- | --- |
| State | Current assessed state, accounting for whether the Runtime process is active. |
| Recorded state | Latest saved state; this can lag behind current activity, such as during a resume. |
| Stage / Stage attempt | Latest recorded stage and attempt, not a percentage complete. |
| Runtime active / Runtime PID | Whether the owning Runtime process is active and, if so, its process ID. |
| Last recorded activity | Time of the latest saved rollout activity; quiet periods alone do not prove a hang. |
| Run directory | Managed record directory, useful for finding diagnostics. It is distinct from the research workspace. |

`in_progress` means the Runtime is active. `stopped` indicates a saved stop, while `unexpected_exit` means execution appears unfinished but no Runtime is active. Both can be continued with `resume`. `completed` means the run has finished; inspect its outputs and limitations to judge whether it met your task requirements. An inactive Runtime alone does not establish completion.

### `ownpi run stop`

```bash
ownpi run stop <run_id_or_run_dir> [--reason TEXT] [--json] [--config PATH]
```

Request immediate cancellation of active work while preserving records for resume. `--reason` adds an optional explanation, for example `--reason "stop for now"`. Pressing **Ctrl-C** in the foreground run's terminal requests the same stop. Use this command for a background run or from a second terminal with the same app home.

Cancellation and cleanup may take a few seconds. Check `run status` to see whether the Runtime has exited. Stopping OwnPI does not guarantee cancellation of independent external jobs, such as a remote training job.

### `ownpi run resume`

```bash
ownpi run resume <run_id_or_run_dir> [--background] [--json] [--config PATH]
```

Continue an existing run from saved records after a stop or an unexpected process exit. Recovery may repeat unfinished work rather than restart at the exact instruction where execution stopped. Keep the original workspace and required resources available. Do not start a competing resume while that run's Runtime is still active.

Resume runs in the foreground by default. Add `--background` for a detached process that returns immediately:

```bash
ownpi run resume <run_id>
ownpi run resume <run_id> --background
```

## Updates and Neuroscience Pack

OwnPI Core works without the optional Neuroscience Pack. The Pack adds Brainlib, neuroscience skills, and research knowledge. Run installation or maintenance commands when no research run is active in that app home.

### `ownpi update`

```bash
ownpi update
```

Update OwnPI Core and its Dependency Bundle from the installation's configured release channel. Settings and run records are preserved. The optional Pack has its own update command.

### `ownpi pack install`

```bash
ownpi pack install neuroscience
```

Download and install the Pack compatible with the managed OwnPI installation. A working base installation is required. After installation, check it with `ownpi pack status neuroscience` and `ownpi tools doctor brainlib`.

### `ownpi pack status`

```bash
ownpi pack status neuroscience [--json]
```

Inspect whether the Pack is installed, enabled, and ready. `--json` includes detailed inspection data. This does not install or modify the Pack.

### `ownpi pack update`

```bash
ownpi pack update neuroscience
```

Fetch and install the Pack offered by the installation's distribution endpoint. It uses the same installation path as `pack install`, so it can also install the Pack if absent. Updating OwnPI alone does not run this command.

### `ownpi pack rollback`

```bash
ownpi pack rollback neuroscience
```

Activate the most recent retained Pack release older than the current version. This requires a valid older release to remain installed locally; it fails if none is available. It does not roll back OwnPI Core or research files.

### `ownpi pack disable`

```bash
ownpi pack disable neuroscience
```

Deactivate the installed Pack without deleting its release files. Core remains usable. Use `pack enable` to make the retained Pack available again.

### `ownpi pack enable`

```bash
ownpi pack enable neuroscience
```

Reactivate an installed, disabled Pack. It does not download a missing Pack; use `pack install` if it is absent.

### `ownpi pack remove`

```bash
ownpi pack remove neuroscience
```

Remove installed Pack releases and managed Brainlib resources and disconnect the Pack's managed knowledge delivery. This also removes retained Pack releases used for rollback. It does not delete your research workspace or run records. Use `pack install` to install it again.

## Skills and tools

### `ownpi skills list`

```bash
ownpi skills list [--json]
```

List visible research skills and their sources. Core skills are bundled; installing the Neuroscience Pack adds its skills. The Beta does not support discovering or installing additional skills through the CLI.

### `ownpi skills remove`

```bash
ownpi skills remove <skill_name> [--json]
```

Delete a user-installed skill directory from `<app_home>/skills`. Use its directory name. This command cannot remove bundled skills or Pack skills, even if they appear in `skills list`, and fails if no matching user-installed skill exists. A fresh Beta installation generally has no such user-installed skills. The removal result is printed as JSON.

### `ownpi tools list`

```bash
ownpi tools list [--json]
```

Show managed tool availability and diagnostic summaries. The current CLI inspects Brainlib; seeing it listed does not mean its Pack is installed.

### `ownpi tools doctor`

```bash
ownpi tools doctor brainlib [--json]
```

Check Brainlib's managed installation and entrypoint. Brainlib requires the Neuroscience Pack. If it is unavailable, install or enable the Pack as appropriate, then repeat the check. This does not run a research task.

## Compatibility commands still visible in help

The current CLI also exposes the following clarification and permission commands. They act only on pending interactions in an existing run and are not setup steps or part of the supported unattended Beta workflow. Their presence in help does not mean interactive permission modes are supported for normal Beta use.

These commands require the session-managed run directory, not a run ID. Obtain it from `ownpi run status <run_id>`. They do not create a missing question or permission request.

| Command | Function and use |
| --- | --- |
| `ownpi clarify show <run_dir>` | Read pending clarification questions, or report that none are pending. |
| `ownpi clarify answer <run_dir> --answers-file PATH` | Submit a JSON file containing an `answers` mapping for the pending questions. Use the question IDs shown by `clarify show` as keys and answer text or offered option labels as values. |
| `ownpi permission show <run_dir>` | Read pending tool permission requests, or report that none are pending. |
| `ownpi permission approve <run_dir> [--scope once\|session]` | Approve a live pending request once (the default) or for the session. It cannot approve an already stopped invocation. |
| `ownpi permission deny <run_dir>` | Deny required execution and end the paused run. Use `run stop` when the intention is to stop work temporarily and resume it later. |
| `ownpi permission deny-command <run_dir>` | Reject only the pending tool command so the agent can try an alternative when work continues. |

For example, an answers file for a free-text question could contain `{"answers": {"question_id_from_show": "Your answer"}}`. The `answer`, `approve`, `deny`, and `deny-command` commands also accept `--config PATH`, `--llm-profile NAME`, `--project-root PATH`, and `--json`. `--llm-profile` selects an existing model profile for the operation; `--project-root` explicitly selects its runtime workspace root. Responding to an interaction may continue execution and call the model. These commands do not offer `--background`; inspect `run status` afterward before deciding whether another resume is needed.
