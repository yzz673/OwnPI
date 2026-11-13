# Configuration

[中文](configuration.zh-CN.md) | **English**

## Files and model settings

The default app home is `~/.ownpi`. Run `ownpi setup` to configure or replace your model connection. Re-running setup overwrites the generated `config.yaml` and `.env`; back up any manual settings you want to keep first. It does not append another provider to an existing configuration.

| Setting | What to enter |
| --- | --- |
| Provider | OpenAI-compatible (Responses API) or Anthropic-compatible (Messages API) |
| API key | A valid key from that provider |
| Base URL | The provider's service URL, without an individual request endpoint |
| Model | The exact model name accepted by the provider |
| Reasoning effort | A supported effort level, when setup asks for one |

The OpenAI-compatible setup option configures `openai_responses`; a gateway that supports only Chat Completions will not work with that generated configuration. The Anthropic-compatible option configures `anthropic_messages`. Your selected model and service must support the corresponding interface and tool use. Use your provider's Base URL, retaining any required version prefix but without appending `/responses`, `/chat/completions`, or `/messages`.

Setup asks for reasoning effort for the OpenAI-compatible option; choose a value supported by your model and service. It uses the selected model for both research and review. The default review profile is `auto`.

Settings are stored in `config.yaml`, and credentials in `.env` inside the app home. Do not share the credentials file. Inspect the active settings with:

```bash
ownpi config show
```

This shows the selected model profile, runtime settings, and effective review policy. It does not send a model request or prove that the provider accepts the configuration.

To use a different app home, set this before installation and subsequent commands:

```bash
export OWNPI_HOME=/path/to/app-home
```

## Execution mode

Set `runtime.default_profile` in the existing `runtime` section of `config.yaml` to `task` (Direct) for interactive requests or `research` for the seven-stage research workflow. `auto` falls back to Direct when no explicit mode is selected; it does not classify requests by complexity. CLI setup generates a `research` configuration.

In the TUI, use `/runtime`, `/direct`, or `/research` to select the mode for new tasks. Existing runs retain their original mode. See the [TUI guide](tui.md#execution-mode).

## Review profile

The default `auto` review profile selects `standard` for Task Level (Direct) and `rigorous` for Research Level.

| Profile | Total Analysis revisits per Research workflow |
| --- | --- |
| `auto` | Same as `rigorous` |
| `standard` | 1 |
| `rigorous` | 3 |
| `exhaustive` | Unlimited |

A revisit returns to an earlier research stage for revision. No profile imposes a Reviewer round limit within a critic loop. Revisit budgets apply to Research workflows, not follow-up messages in Direct sessions. These settings do not guarantee a particular quality level or duration. Review can run for a long time; use [Ctrl-C or run stop](cli-reference.md#ownpi-run-stop) when needed.

```bash
ownpi config set-review-profile standard
```

## Subagents

Subagents are enabled by default, with a maximum of 8 tasks per batch. Core decides when to delegate work; enabling subagents does not require it to use them for every task. Edit these fields within the existing `runtime` section of `config.yaml`, preserving the other settings:

```yaml
runtime:
  subagent_enabled: true
  subagent_max_count: 8
```

Set `subagent_enabled` to `false` to disable them. `subagent_max_count` must be a positive integer; it limits tasks in one batch, not the total across a run.

## Workspace and permissions

Use `ownpi run start --workspace /path/to/project --message-file /path/to/task.md` to choose the research workspace. Without `--workspace`, a new run uses the current directory. Keep using the same app home when listing, stopping, or resuming that run; a workspace and an app home serve different purposes.

Use a dedicated workspace and provide clear resource paths and constraints in the task brief. CLI setup generates `runtime.permissions.preset: full_access`; interactive approval is also supported. In the TUI, use `/permissions` to inspect and select Ask for approval, Approve for me, or Full Access. Check the active run's actual permissions; changing settings does not retroactively change an existing run. See [approvals and questions](tui.md#7-handle-approvals-and-questions).
