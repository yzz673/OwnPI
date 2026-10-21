<div align="center">
  <img src="docs/assets/readme/ownpi-lockup.png" alt="OwnPI: Your Own Principal Investigator" width="720">
  <p>
    <a href="https://github.com/yzz673/OwnPI/stargazers"><img src="https://img.shields.io/github/stars/yzz673/OwnPI?style=flat-square" alt="GitHub Stars"></a>
    <a href="https://github.com/yzz673/OwnPI/fork"><img src="https://img.shields.io/github/forks/yzz673/OwnPI?style=flat-square" alt="GitHub Forks"></a>
    <a href="https://github.com/yzz673/OwnPI/commits"><img src="https://img.shields.io/github/last-commit/yzz673/OwnPI?style=flat-square" alt="Last commit"></a>
    <a href="https://galaxy-dawn.github.io/neuroagent-website/?lang=en"><img src="https://img.shields.io/badge/Website-Visit-0ea5e9?style=flat-square&amp;logo=safari&amp;logoColor=white" alt="OwnPI website"></a>
    <a href="https://github.com/yzz673/OwnPI/pulls"><img src="https://img.shields.io/badge/PRs-welcome-2ea44f?style=flat-square&amp;logo=github&amp;logoColor=white" alt="Pull requests welcome"></a>
  </p>

  <strong>Language</strong>: <strong>English</strong> | <a href="README.zh-CN.md">中文</a>
  <p><strong>Supported Platforms</strong>: macOS | Linux</p>

</div>

> **Beta notice:** The current release is a Beta intended for experiencing the complete research workflow and should not yet be treated as production-stable. Compatibility issues or undiscovered defects may remain. The Beta runs the workflow through the command line with unattended `full access` execution; use it only in a dedicated workspace and do not expose sensitive files. The Beta does not publish the TUI or provide interactive permission approval. The Stable release will formally include these capabilities. Bug reports are welcome through [GitHub Issues](https://github.com/yzz673/OwnPI/issues), along with community [Pull Requests](https://github.com/yzz673/OwnPI/pulls) that improve features, documentation, and stability.

# OwnPI

OwnPI, your own principal investigator. After the user provides a research objective and available resources, OwnPI advances literature research, problem framing, method and experiment design, implementation, analysis, and output synthesis as needed, dynamically drawing on the appropriate tools, paper-derived knowledge, and general research skills. When a stage result is insufficient, it returns to the relevant stage for iterative correction. Throughout the run, it records sources, decisions, state, and deliverables so the research process and results remain inspectable, traceable, and evidence-grounded.

OwnPI is currently designed primarily for neuroscience research, especially work involving brain signals, neural data, and neural decoding. OwnPI contains a Core that supports a general research workflow, with an optional Neuroscience Pack that adds neuroscience-specific skills and knowledge to OwnPI Core. OwnPI Core can also be extended to research in other fields; the pack specializes Core for neuroscience without replacing it.

## Quick Navigation

| Section | What it helps with |
| --- | --- |
| [Why You Need OwnPI](#why-you-need-ownpi) | Decide when OwnPI is a better fit than a fixed automated research pipeline. |
| [Core Workflow](#core-workflow) | See task routing, iterative review, paper-derived knowledge, and skill assembly. |
| [Quick Start](#quick-start) | Install the Beta, describe a research task, and start the first command-line run. |
| [End-to-end Research Example](#end-to-end-research-example) | See a completed research project and read its manuscript. |
| [Stable Roadmap](#stable-roadmap) | See the capabilities planned for the Stable release. |
| [Documentation](#documentation) | Find the user documentation for installing and operating OwnPI. |
| [Feedback](#feedback) | Report issues and propose changes with enough context to evaluate them. |
| [Acknowledgments](#acknowledgments) | See the projects and contributors that support OwnPI. |
| [References](#references) | Trace the upstream projects used by OwnPI. |

## Why You Need OwnPI

Common automated research frameworks are good at taking a well-defined task through a preset path: gather sources and produce a report, turn an idea into a paper, or repeat experiments against one metric. When the objective and route are stable, they may be all you need.

Real research rarely follows a straight path. A research plan often needs repeated refinement or even a complete rethink; one failed experiment may send the researcher back to the implementation or method design. OwnPI treats research as a project you can keep advancing, inspect, and recover, not a one-shot prompt-to-answer task:

- It activates only the stages a task needs instead of forcing every request through the full pipeline.
- When something goes wrong, it can return to the upstream stage that needs correction instead of only retrying the last step.
- It records key decisions, tool activity, permissions, progress, and artifacts so interrupted work can be inspected and continued.

## Core Workflow

### Review-Driven Iteration

[![Native website animation: iterative research workflow](docs/assets/readme/hd-workflow.en.webp)](docs/assets/readme/hd-workflow.en.webp)

OwnPI organizes research as a loop that can return upstream, not a pipeline that can only move forward. After locking the task and establishing its literature and background, it advances through the necessary hypothesis, method, experiment, implementation, and analysis work; when analysis and review expose a problem, the run returns to the hypothesis, method, experiment, or implementation stage that needs correction, and only accepted work proceeds to packaging the paper, code, figures, and reproducible outputs. Each run activates the stages it needs. Review and revisit limits depend on the selected review profile; rigorous review has no round limit, and exhaustive review also removes the Analysis revisit limit.

### Paper-Derived Knowledge

[![Native website animation: distilling paper evidence into method knowledge and research strategies](docs/assets/readme/hd-knowledge.en.webp)](docs/assets/readme/hd-knowledge.en.webp)

OwnPI turns one body of paper evidence into two complementary kinds of reusable support: method knowledge formed by grouping comparable cases and inducing recurring patterns, and research strategies formed by comparing paper structures and research approaches across studies. Both knowledge bases retain searchable, expandable, and traceable links to their evidence and are prepared and released before a run. During the task, OwnPI retrieves only the relevant material on demand, avoiding repeated knowledge-base construction and keeping prior knowledge distinct from evidence for the current claim.

The knowledge currently prepared for the neuroscience use case is derived primarily from neuroscience, brain-signal, and neural-data papers. It supports neuroscience research when the Neuroscience Pack is installed, while the underlying paper-derived knowledge mechanism remains part of OwnPI Core.

### Dynamic Research Skill Assembly

[![Native website animation: selecting research skills and assembling the current method pack](docs/assets/readme/hd-skills.en.webp)](docs/assets/readme/hd-skills.en.webp)

Building on [Claude Scholar](https://github.com/Galaxy-Dawn/claude-scholar), OwnPI includes a collection of mature research skills as built-in features. After installing OwnPI, users do not need to find, install, or configure these skills one by one; they are ready to use. For each task, OwnPI selects only the relevant skills and reads their detailed guidance when needed, giving different kinds of research the right support.

This is a general Core capability. The Neuroscience Pack contributes a small set of additional skills for brain-signal and neural-data work; it is not a large replacement collection of neuroscience skills.

## Quick Start

### Install and configure

The current Beta provides packages for Apple Silicon macOS (arm64) and Linux x86_64. Packages are hosted on GitHub Releases. Install the release package:

```bash
curl -fsSL https://raw.githubusercontent.com/yzz673/OwnPI/main/install.sh | bash
```

The installer places the app home at `~/.ownpi` and the command at `~/.local/bin/ownpi`. If your shell cannot find `ownpi`, add its directory to the current shell's `PATH`:

```bash
export PATH="$HOME/.local/bin:$PATH"
```

Configure model access and check the installation:

```bash
ownpi setup
ownpi doctor
```

- `setup` asks for your provider type, API key, Base URL, model name, applicable reasoning effort, and review settings, then writes the configuration to the app home. OpenAI-compatible and Anthropic-compatible providers are supported.
- `init` is optional: the installer and setup already initialize the app home.
- `doctor` checks installation, configuration, credentials, and optional capabilities. A successful check does not verify a real model request.

To use a different app home, set `OWNPI_HOME` before installation and use the same setting for subsequent commands. See [Configuration](docs/user/configuration.md).

### Optional Neuroscience Pack

OwnPI Core works without the Pack. For neuroscience, brain-signal, or neural-data research, install it after the base installation:

```bash
ownpi pack install neuroscience
```

The Pack provides Brainlib, neuroscience-specific skills, managed tool entrypoints, and paper-derived research knowledge. Check its installation with:

```bash
ownpi pack status neuroscience
ownpi tools doctor brainlib
```

### Describe a research task

Give OwnPI a research brief that explains the project you want to pursue. Include the research question or direction, the intended contribution, available resources with concrete paths, practical constraints, and expected deliverables. You do not need a finished experimental plan; make clear which decisions OwnPI should investigate and develop.

Create a UTF-8 file named `task.md`. Use this template as a starting point:

```text
Treat this as a serious research project in [research area].
The central research question or direction is: [what do you want to understand, explain, or establish?]
The intended contribution is: [what new method, framework, explanation, or evidence should the study produce?]
Available resources: [datasets, code, papers, tools, Python environments, or other materials, with concrete paths].
Constraints: [available compute, time or cost limits, data restrictions, and other requirements].
Expected deliverables: [manuscript, supplementary materials, source data, source code, or other outputs].
```

### Start and manage a run

Start the task in a dedicated project workspace:

```bash
ownpi run start --message-file /path/to/task.md --workspace /path/to/your/project
```

Omit `--workspace` to use the current directory. The workspace holds research files and outputs; `~/.ownpi` holds configuration and managed run records. The Beta runs the Research Level workflow through the CLI. OwnPI reads the request and selects the stages needed for the task.

`start` and `resume` run in the foreground by default. Add `--background` to either command to return to the shell while work continues. Use `run list` to find a run ID, then inspect or control that run:

```bash
ownpi run list
ownpi run status <run_id>
ownpi run stop <run_id>
ownpi run resume <run_id>
ownpi run resume <run_id> --background
```

In the foreground, press **Ctrl-C** in the same terminal to stop. Both Ctrl-C and `run stop` cancel active work; cleanup may take a few seconds. After a stop or an unexpected process exit, `resume` continues from saved records and may repeat unfinished work. Use the same app home in each terminal.

See [Getting Started](docs/user/getting-started.md) for setup details, task files, and updates, or the [CLI reference](docs/user/cli-reference.md) for status, stop, and resume.

## End-to-end Research Example

**Research topic:** How acoustic and sentence-associated information differ in their temporal support and sensor recurrence in non-invasive brain recordings.

**Deliverables:** Manuscript, supplementary materials, source data, and source code.

**Manuscript:** This 32-page paper, *Acoustic and sentence-associated information differ in their temporal support and sensor recurrence in non-invasive recordings*, was produced by OwnPI **without any human intervention** during the research run. It asks which descriptions of speech predict non-invasive brain recordings, when those associations contribute, and how their sensor patterns recur across stories and recording sessions. Using 164 MEG recordings from 23 participants, the study compares acoustic features, phoneme identity, isolated-word representations, contextual representations, and word surprisal. Stories are held out for testing, and competing predictors, temporal controls, preprocessing comparisons, and direct prediction tests define what the results can support.

**Complete paper:** [Read or download the 32-page PDF](docs/assets/readme/output-example-paper.pdf).

<table>
  <tr>
    <td align="center" valign="top" width="25%">
      <a href="docs/assets/readme/output-example-paper-page-1.png"><img src="docs/assets/readme/output-example-paper-page-1.png" alt="Page 1: title and abstract of the example research paper" width="220"></a>
    </td>
    <td align="center" valign="top" width="25%">
      <a href="docs/assets/readme/output-example-paper-page-9.png"><img src="docs/assets/readme/output-example-paper-page-9.png" alt="Page 9: acoustic lag support, history controls, and temporal sensitivity analyses" width="220"></a>
    </td>
    <td align="center" valign="top" width="25%">
      <a href="docs/assets/readme/output-example-paper-page-15.png"><img src="docs/assets/readme/output-example-paper-page-15.png" alt="Page 15: distributed sensor patterns and their recurrence across stories and sessions" width="220"></a>
    </td>
    <td align="center" valign="top" width="25%">
      <a href="docs/assets/readme/output-example-paper-page-21.png"><img src="docs/assets/readme/output-example-paper-page-21.png" alt="Page 21: covariance, calibrated recovery, and prediction controls" width="220"></a>
    </td>
  </tr>
</table>

> **Model:** GPT-Astra, medium reasoning effort
>
> **Provider calls:** 4,437
>
> **Successful tool calls:** 5,591

## Stable Roadmap

The following capabilities are planned for the Stable release and are not part of the current Beta.

### TUI

The Stable release will add the OwnPI TUI for defining research tasks, starting runs, and inspecting staged progress. The current Beta is operated through the CLI documented above.

[![Preview of the OwnPI TUI planned for the Stable release](docs/assets/readme/terminal-demo-0-5s.gif)](https://galaxy-dawn.github.io/neuroagent-website/?lang=en#heroTerminalDemo)

### Adaptive Task Orchestration

[![Native website animation: adaptive runtime routing and research depth](docs/assets/readme/hd-routing.en.webp)](docs/assets/readme/hd-routing.en.webp)

The Stable release will support adaptive routing across task depths and workflow modes. The current Beta runs the Research Level workflow through the CLI.

### Safer Permission Modes

The Stable release will provide more granular permission profiles and interactive approval controls, allowing users to choose how file, network, and execution access is granted during a run.

### Source Availability

The Stable release will publish the OwnPI source code together with the installable product.

## Documentation

- [Getting Started](docs/user/getting-started.md): installation, model setup, first task, and updates.
- [Configuration](docs/user/configuration.md): model access, review profiles, and subagents.
- [CLI Reference](docs/user/cli-reference.md): task management and optional Pack commands.
- [Troubleshooting](docs/troubleshooting.md): installation, model access, and task recovery.
- [Third-Party Notices](THIRD_PARTY_NOTICES.md).

## Feedback

The Beta is distributed as an installable product rather than a source release. Please use [GitHub Issues](https://github.com/yzz673/OwnPI/issues) to report installation problems, provider failures, runtime interruptions, unexpected outputs, or documentation issues.

Include the OwnPI version, operating system, command, relevant run status, and steps to reproduce. Do not include API keys, private data, or sensitive workspace contents.

## Acknowledgments

OwnPI builds on research skills developed in Claude Scholar, including its Nature-oriented skills for scientific writing, polishing, reviewer responses, and data availability. It packages Brainlib as a managed neuroscience tool and adapts specific permission and sandbox orchestration designs from OpenAI Codex. We thank the maintainers and contributors whose work supports these foundations and the broader open-source ecosystem used by OwnPI.

## References

- [Claude Scholar](https://github.com/Galaxy-Dawn/claude-scholar) - provides the foundation for OwnPI's bundled research skills.
- [Brainlib](https://github.com/Galaxy-Dawn/brain-plug-and-play-library) - is integrated as OwnPI's managed neuroscience data and modeling tool.
- [OpenAI Codex](https://github.com/openai/codex) - is the source of the permission and sandbox orchestration design adapted in OwnPI; the pinned commit and source paths are recorded in [Third-Party Notices](THIRD_PARTY_NOTICES.md).
