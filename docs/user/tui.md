# OwnPI TUI User Guide

OwnPI TUI is an interactive terminal interface for research and development. Describe a task, follow its progress, answer questions, approve operations, and resume unfinished work after leaving the application.

## 1. Launch and initial setup

Open a terminal in your project directory and start OwnPI:

```bash
cd /path/to/project
ownpi-tui
```

Or specify an existing workspace directly:

```bash
ownpi-tui --workspace /path/to/project
```

Replace the example path with your actual directory. Put paths containing spaces in double quotes. Without `--workspace`, OwnPI uses the current directory. Use a dedicated project directory rather than your entire home directory.

To create a new, empty project directory:

```bash
ownpi-tui --new-project /path/to/new-project
```

### If you already use the CLI

The CLI and TUI share configuration, credentials, and persisted runs. When using the same app home, you do not need to run `ownpi setup` again.

### If this is your first session

When launched in an interactive terminal, the TUI guides you through missing configuration. Supply your provider, API key, Base URL, and model name. The guide is skipped when the necessary configuration is already present.

You can also configure OwnPI from the command line first:

```bash
ownpi setup
ownpi doctor
ownpi-tui --workspace /path/to/project
```

Keep these points in mind:

- Use the exact model name and Base URL supplied by your provider. Do not append `/responses` or `/messages` yourself.
- The OpenAI-compatible configuration uses the Responses API. A service that only implements Chat Completions is not compatible with that configuration. Anthropic-compatible providers use the Messages API.
- `doctor` checks installation and configuration without sending a model request. Your first task tests whether the provider accepts the credentials and model.
- Do not rerun setup merely to switch interfaces. Setup can overwrite the files it generates; back up manual configuration changes first.

The default configuration is `~/.ownpi/config.yaml`, and credentials are stored in `~/.ownpi/.env`. Never include API keys in prompts, screenshots, or issue reports. Use `--config` or `--app-home` when you need explicit configuration or data paths, and keep the app home consistent when switching between the CLI and TUI.

## 2. Understand the interface

Before submitting work, check the model, permissions, and working directory shown near the input area.

| Area | What it shows |
| --- | --- |
| Conversation | Your requests, progress updates, tool activity, review feedback, and published answers |
| Input area | Your next message; type `/` to browse command suggestions |
| Status information | Model, permissions, working directory, Mode, Stage, and Context information |
| Menus and interaction panels | Model selection, session recovery, approvals, and questions that need your input |

Basic controls:

- **Enter:** submit a message, or confirm the selected menu or completion item when one is open.
- **Ctrl+J:** insert a newline. For a long request, draft it in an editor, paste it into the input area, and check it before submitting.
- **Up / Down:** move through menu options.
- **Tab:** complete a selected suggestion. Some panels assign it another function; for example, it changes the project scope in Resume.
- **Esc:** close or step back from the current panel. In the main conversation, with no overlay open, Esc requests a stop when work is active.
- **Ctrl+T:** open or close the full transcript viewer. Use arrow keys, PageUp, PageDown, Home, and End to navigate it.

Use `/commands` to browse available commands. Follow the hints at the bottom of the active panel: shortcuts are context-sensitive. For example, Ctrl+S changes sorting in Resume; it does not submit an answer there.

## 3. Complete your first task

Start with a bounded task, such as summarizing a local paper. Place the PDF in your project, check its path, and submit:

```text
Read /path/to/project/paper.pdf and summarize:
1. The research question and main contribution.
2. The data, experimental design, and core methods.
3. The main results, with page or figure references.
4. The scope and limitations of the conclusions.

Distinguish the paper's findings from your interpretation. Do not invent missing information.
Save the summary to /path/to/project/outputs/summary.md and include the file path in your final answer.
Do not modify the original PDF.
```

OwnPI may read files, call tools, request review, or ask for clarification or permission. Real model requests may incur provider charges. Use a small task to check an unfamiliar configuration before starting a long research run.

When the final answer appears:

1. Check that it covers your request.
2. Verify important numbers, page references, and figure references.
3. Open the generated file and compare it with the delivery summary.
4. Continue in the same conversation if you need changes, such as: “Add the experimental settings and distinguish correlational findings from causal claims.”

A completed run is not a guarantee that every deliverable meets your requirements. Check the answer and the actual files, rather than relying on the `COMPLETED` label alone.

### Describe a more complex task

Specify the goal, available resources and paths, permitted actions, time or cost constraints, and expected outputs.

```text
Goal: Compare two EEG feature representations for cross-subject classification.
Resources: Data are in /path/to/data; code is in /path/to/project.
Constraints: Do not modify raw data. Check the data split for leakage first.
Show me the experimental plan before starting full training.
Budget: Run a small validation first; do not launch long training jobs.
Deliverables: An experimental plan, reproducible scripts, a results table, and limitations.
```

Asking for a plan makes the plan the immediate deliverable. To request execution, state its permitted scope explicitly. Providing a path does not override access permissions, and a budget written in a prompt is not an automatic billing cap.

## 4. Choose a model, execution mode, and review profile

These are separate controls with different purposes.

### Model and reasoning effort

Use `/model` to select a configured model or profile, and `/reasoning` to choose an available reasoning effort.

Options depend on your configuration and provider. Not every model supports the same settings. Higher effort can increase latency and cost without guaranteeing a better result.

### Execution mode

Open `/runtime` to choose the execution mode:

| Mode | When to use it |
| --- | --- |
| Direct | Ongoing conversation, file analysis, coding, and bounded tasks, with tools, review, and follow-up messages |
| Research | End-to-end research that should follow the seven-stage research workflow |
| Automatic | Uses the default execution level, currently Direct; it does not automatically route every complex request to Research |

You can also use `/direct` or `/research` to set the mode for new tasks.

Existing runs retain their original mode. Changing the menu does not convert an active Direct run into a Research workflow. To start in another mode, finish or stop the current work, start a new conversation with `/clear`, and check the settings before submitting.

Older screenshots or records may use labels such as `Task`. This guide uses the Direct / Research names shown in the current mode menu.

### Review profile

Open **Review profile** under `/settings`. This controls review policy, not the model's reasoning effort.

For Research, Standard allows up to one stage revisit, Rigorous up to three, and Exhaustive has no revisit cap. These limits are not caps on Reviewer rounds. Automatic uses the configured default. More extensive review can require additional model calls and time; it is not a guarantee of correctness.

Execution settings generally apply to subsequent runs. Read the menu description and check the status information rather than assuming that an active run has changed configuration.

## 5. Work with files, projects, and skills

### Workspace versus app home

Your workspace contains project files and generated outputs. The app home contains configuration, credentials, and persisted run records. They are different locations.

- `/project`: inspect the current project, open another workspace, or create one.
- `/clear`: start a new conversation in the current project, preserving its files and the previous conversation. A confirmation menu appears first.
- Stop active work before switching projects or starting a new conversation.

Conversations in the same workspace share files. `/clear` neither deletes history nor rolls back earlier edits. Use separate project directories to isolate experiments.

### Provide materials

Include concrete file or directory paths and explain what each resource is for. Quote paths containing spaces. Providing a directory does not mean every file will automatically be read; ask for an initial directory inspection when appropriate.

Avoid references such as “the paper from before” without naming its location. When reusing materials from an earlier session, confirm that the current workspace and files are correct.

### Use skills

Use `/skills` to inspect and manage installed skills. Type `$` in the input area to find a skill and insert its name into your message.

You still need to specify the task and input paths. An installed skill does not guarantee that its required software, services, or data are available. Resolve missing requirements as needed; you do not need every extension for your first task.

## 6. Follow execution, review, and subagents

The conversation includes different roles and states:

- **Core:** carries out the task, calls tools, and prepares or revises the answer.
- **Reviewer / Audit:** checks submitted work and reports findings.
- **Subagents:** handle delegated subtasks when the execution uses them. Not every task runs in parallel.
- **Working:** work is active or awaiting further runtime events; a tool need not be producing visible output continuously.
- **Reviewing:** the current submission is being reviewed.
- **Completed:** the corresponding request or run has finished; its deliverables still need checking.

Use `/subagents` or **F2** to inspect subagents. Use `/requests` or **F4** to inspect pending approvals and questions.

During review, progress and feedback may appear before the final answer is published. `No findings` means that the Reviewer reported no issues in that round. It is neither the final answer nor proof that the work is error-free.

If execution ends with only progress text or revision notes and no complete answer, check `/status`, `/audit`, and the transcript. Keep the run ID for troubleshooting.

## 7. Handle approvals and questions

Use `/permissions` to inspect permission presets:

| Preset | Behavior and cautions |
| --- | --- |
| Ask for approval | Can read and edit workspace files and run commands; internet access or edits to other files require approval. This is not a read-only mode |
| Approve for me | Asks about actions detected as potentially unsafe; this is not an absolute safety guarantee |
| Full Access | Can access the internet and edit files outside the workspace without asking each time; use with care |

For initial use, keep human approval enabled and work in a dedicated directory without sensitive files. Permission changes generally apply to subsequent runs. While work is active, the status information reflects that run's actual permissions.

Before approving an operation, inspect its target, command, and possible effects. Do not approve an unfamiliar action merely to keep the task moving.

For a question, select an option or enter your answer as prompted, then submit it. Closing a question panel with Esc does not answer it. Closing an approval panel neither approves nor rejects the operation. Reopen pending interactions with `/requests` or F4.

## 8. Stop, exit, and resume

### Stop the current task

Enter:

```text
/stop
```

Esc sends the same stop request when work is active in the main conversation and no menu, interaction panel, or transcript viewer is open. Elsewhere it first handles the current interface, such as closing a panel or leaving Vim insert mode. A single Esc press does not stop work from every screen.

The backend must process the stop request; `Stopping` may appear briefly. Use `/status` to check the execution state. `/stop` stops the model loop, but background commands already launched may continue. Inspect them with `/ps` rather than assuming a training job or download has also stopped.

### Exit the interface

Enter `/exit` and select **Stop and exit**. This stops active work and exits while preserving run records. It is not a command for detaching the interface while leaving the model task running in the background.

### Continue the task later

To stop now and continue in 20 minutes:

1. Enter `/stop`, wait for the request to be processed, and check the status.
2. Enter `/exit` and confirm.
3. Later, launch `ownpi-tui` using the same app home.
4. Enter `/resume`, search for the original conversation, and select it.
5. Press Enter to continue. Resolve any pending questions or approvals as prompted.

The length of the break does not change the recovery mechanism. Recovery uses persisted records, an available workspace, and a supported run format—not a snapshot of the paused process's memory. Unfinished steps or tool operations may be repeated.

### Navigate Resume

The list shows relative times and conversation titles. Type keywords directly to search.

| Control | Action |
| --- | --- |
| Up / Down | Select a conversation |
| Enter | Continue a recoverable run or view history that cannot be resumed |
| Ctrl+O | Open details for the selected run |
| Tab | Switch between the current project and all projects |
| Ctrl+F | Cycle the status filter |
| Ctrl+S | Switch between updated-time and created-time sorting |
| Ctrl+U | Clear the search query |
| Esc | Clear a nonempty query first; otherwise go back |

Resuming unfinished work continues execution. Opening an idle Direct conversation normally restores its history without making another model request until you send a message. Being able to view a record does not mean it can be resumed; some older formats are explicitly unsupported.

If you know the run ID, start directly with:

```bash
ownpi-tui --workspace /path/to/project --resume RUN_ID
```

Replace `RUN_ID` with the actual identifier. Stop the original execution before switching between CLI and TUI, and use the same app home. Do not try to execute the same run from both interfaces at once.

### Why can't I submit a new question after stopping?

Stopping can leave the current request unfinished. Run protection may reject a new message rather than overwrite that work. Choose one of these paths:

- To finish the original task, resume it with `/resume`.
- To start something else, confirm that execution has stopped, then use `/clear` for a new conversation or `/project` for another workspace.

Do not repeatedly resubmit the same message or manually create missing event files to force recovery.

## 9. Check answers, sources, and generated files

Published answers may contain Markdown, source links, and local file paths. Clickable links depend on your terminal. If clicking is unsupported, copy the URL or open the file by its path.

- `/copy`: copy the latest response into notes or another document.
- `/raw`: switch between rich and raw transcript rendering to inspect Markdown or original content.
- `/diff`: inspect changes in the current Git working tree. This is not a complete audit of arbitrary directories, and untracked outputs may not appear in the diff.
- **Ctrl+T**: inspect earlier conversation content rather than judging the result from the last few visible lines.

Use the delivery paths reported for the actual task. You can request an `outputs/` directory, but not every run produces the same file structure.

For research outputs, check whether each source supports the associated claim. Distinguish model summaries, published findings, and results from experiments actually run. Automated review does not replace human research judgment.

## 10. Troubleshooting

### `ownpi-tui` is not found

Check the entry points:

```bash
command -v ownpi
command -v ownpi-tui
```

For installations using the default command directory, add it to the current terminal's path:

```bash
export PATH="$HOME/.local/bin:$PATH"
```

If `ownpi` exists but `ownpi-tui` does not, check whether your installed package includes the TUI. `ownpi update` follows your configured release channel; it does not install every new source-branch commit automatically.

### The model connection fails

Run `ownpi doctor`, then check the provider's API type, Base URL, credentials, and exact model name. Read the returned error rather than repeatedly changing reasoning effort and retrying. Redact credentials when requesting help.

### Working remains visible without a new answer

Check `/requests` for pending interactions, then `/status` for the backend state. Use `/subagents`, `/ps`, or `/audit` as needed. Execution, review, provider waits, and background commands are different situations; animation alone cannot establish whether a task has finished.

### A conversation is missing from Resume

Press Tab to include all projects, clear the search query, and check the status filter. If you used `OWNPI_HOME` or `--app-home`, make sure the new launch uses the original app home. The same workspace can be used with different run-record directories.

### Recovery reports missing files or an unsupported format

Keep the complete error, run ID, and original run directory. Do not delete the session, fabricate `run-events.jsonl`, or write into an old run as though it were new. Check whether its records are complete and whether the installed version supports that format.

### Execution finishes without a final answer or output file

Inspect the transcript, `/status`, and `/audit`. Record the last visible message, completion state, run ID, and missing deliverables. Review approval, execution stopping, request completion, and answer delivery are different events.

## 11. Everyday quick reference

| Goal | Command or shortcut |
| --- | --- |
| Open a project | `ownpi-tui --workspace /path/to/project` |
| Browse commands | `/commands` |
| Choose a model | `/model` |
| Set reasoning effort | `/reasoning` |
| Choose execution mode | `/runtime` |
| Configure permissions | `/permissions` |
| Inspect skills | `/skills` |
| Inspect subagents | `/subagents` or F2 |
| Handle questions and approvals | `/requests` or F4 |
| Stop current work | `/stop` |
| Resume a run | `/resume` |
| Start a new chat in the same project | `/clear` |
| Switch projects | `/project` |
| Copy the latest response | `/copy` |
| Check execution state | `/status` |
| Stop and exit | `/exit`, then **Stop and exit** |

Before work, check the model, permissions, and workspace. During work, resolve questions and approvals. After work, verify the answer, sources, and actual files. These habits matter more than memorizing every command.
