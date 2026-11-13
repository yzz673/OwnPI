# OwnPI TUI 用户指南

OwnPI TUI 是面向科研与开发的交互式终端界面。你可以描述任务、跟踪进度、回答问题、批准操作，并在退出应用后恢复未完成的工作。

## 1. 启动与首次配置

在项目目录打开终端并启动 OwnPI：

```bash
cd /path/to/project
ownpi-tui
```

也可以直接指定已有工作区：

```bash
ownpi-tui --workspace /path/to/project
```

将示例路径替换为实际目录，包含空格的路径用双引号括起。省略 `--workspace` 时使用当前目录。请使用专用项目目录，而非整个用户主目录。

创建新的空项目目录：

```bash
ownpi-tui --new-project /path/to/new-project
```

### 已经使用过 CLI

CLI 与 TUI 共用配置、凭据和持久化 run。使用相同应用目录时，无需再次运行 `ownpi setup`。

### 首次使用

在交互终端启动时，TUI 会引导补全缺少的配置。请提供 provider、API key、Base URL 和模型名称。必要配置已齐全时会跳过引导。

也可以先通过命令行配置：

```bash
ownpi setup
ownpi doctor
ownpi-tui --workspace /path/to/project
```

请注意：

- 使用 provider 提供的准确模型名称和 Base URL，不要自行追加 `/responses` 或 `/messages`。
- OpenAI-compatible 配置使用 Responses API，仅实现 Chat Completions 的服务不兼容此配置。Anthropic-compatible provider 使用 Messages API。
- `doctor` 检查安装与配置，不发送模型请求。首次任务会验证 provider 是否接受凭据和模型。
- 不要仅为了切换界面而重新运行 setup。Setup 可能覆盖其生成的文件；请先备份手动配置。

默认配置为 `~/.ownpi/config.yaml`，凭据存放在 `~/.ownpi/.env`。不要在提示词、截图或问题报告中包含 API key。需要明确指定配置或数据路径时，使用 `--config` 或 `--app-home`；切换 CLI 与 TUI 时保持应用目录一致。

## 2. 认识界面

提交任务前，检查输入区域附近显示的模型、权限和工作目录。

| 区域 | 显示内容 |
| --- | --- |
| 对话 | 请求、进度更新、工具活动、评审反馈和已发布回答 |
| 输入区域 | 下一条消息；输入 `/` 可浏览命令建议 |
| 状态信息 | 模型、权限、工作目录，以及 Mode、Stage 和 Context 信息 |
| 菜单与交互面板 | 模型选择、会话恢复、审批和需要回答的问题 |

基本操作：

- **Enter**：提交消息；菜单或补全面板打开时，确认选中项。
- **Ctrl+J**：插入换行。长请求可先在编辑器中撰写，粘贴到输入区域并检查后再提交。
- **Up / Down**：在菜单选项之间移动。
- **Tab**：补全选中的建议。部分面板赋予它其他功能，例如在 Resume 中切换项目范围。
- **Esc**：关闭当前面板或返回上一级。在主对话中没有浮层且工作正在进行时，Esc 会请求停止。
- **Ctrl+T**：打开或关闭完整对话查看器；使用方向键、PageUp、PageDown、Home 和 End 浏览。

使用 `/commands` 浏览可用命令。请以当前面板底部提示为准，快捷键的作用与上下文有关。例如，Ctrl+S 在 Resume 中切换排序方式，不会提交回答。

## 3. 完成第一个任务

从范围明确的任务开始，例如总结本地论文。将 PDF 放入项目，确认路径后提交：

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

OwnPI 可能读取文件、调用工具、请求评审，或请求澄清与授权。真实模型请求可能产生 provider 费用。开始长期研究前，先用小任务验证不熟悉的配置。

最终回答出现后：

1. 检查是否覆盖请求。
2. 核实重要数值、页码和图号。
3. 打开生成文件，与交付摘要对照。
4. 如需修改，可在同一对话中继续，例如：“补充实验设置，并区分相关性发现与因果主张。”

Run 完成不保证每项交付物都满足要求。请检查回答与实际文件，不要只依赖 `COMPLETED` 标签。

### 描述更复杂的任务

写明目标、可用资源与路径、允许的操作、时间或费用限制，以及预期输出。

```text
Goal: Compare two EEG feature representations for cross-subject classification.
Resources: Data are in /path/to/data; code is in /path/to/project.
Constraints: Do not modify raw data. Check the data split for leakage first.
Show me the experimental plan before starting full training.
Budget: Run a small validation first; do not launch long training jobs.
Deliverables: An experimental plan, reproducible scripts, a results table, and limitations.
```

要求制定计划时，计划就是当前交付物。如需执行，请明确允许执行的范围。提供路径不会绕过访问权限，提示词中的预算也不是自动计费上限。

## 4. 选择模型、执行模式和 review profile

这些是用途不同的独立设置。

### 模型与 reasoning effort

使用 `/model` 选择已配置的模型或 profile，使用 `/reasoning` 选择可用的 reasoning effort。

选项取决于配置和 provider，并非所有模型支持相同设置。更高的 effort 可能增加延迟与费用，但不保证更好的结果。

### 执行模式

打开 `/runtime` 选择执行模式：

| 模式 | 适用场景 |
| --- | --- |
| Direct | 持续对话、文件分析、编码及范围明确的任务，支持工具、评审和后续消息 |
| Research | 应遵循七阶段研究工作流的端到端科研 |
| Automatic | 使用默认执行级别，当前为 Direct；不会自动将每个复杂请求路由到 Research |

也可以使用 `/direct` 或 `/research` 为新任务设置模式。

已有 run 保留原模式。修改菜单不会将活动 Direct run 转换为 Research 工作流。如需使用另一模式，先完成或停止当前工作，通过 `/clear` 开启新对话，并在提交前检查设置。

旧截图或记录可能使用 `Task` 等标签。本指南采用当前模式菜单中的 Direct / Research 名称。

### Review profile

在 `/settings` 中打开 **Review profile**。它控制评审策略，而非模型的 reasoning effort。

对于 Research，Standard 最多允许一次阶段回访，Rigorous 最多三次，Exhaustive 不设回访上限。这些限制不是 Reviewer 轮数上限。Automatic 使用配置的默认值。更充分的评审可能需要更多模型调用和时间，但不保证正确性。

执行设置通常适用于后续 run。请阅读菜单说明并查看状态信息，不要假定活动 run 的配置已经改变。

## 5. 使用文件、项目和技能

### 工作区与应用目录

工作区包含项目文件和生成产物。应用目录包含配置、凭据和持久化运行记录，两者不是同一个位置。

- `/project`：查看当前项目、打开其他工作区或创建工作区。
- `/clear`：在当前项目开启新对话，保留项目文件和之前的对话；会先出现确认菜单。
- 切换项目或开启新对话前，先停止活动工作。

同一工作区的对话共用文件。`/clear` 不删除历史，也不回滚之前的修改。请使用不同的项目目录隔离实验。

### 提供材料

提供具体文件或目录路径，并说明每项资源的用途。包含空格的路径用引号括起。提供目录不意味着其中每个文件都会自动被读取；必要时可要求先检查目录。

避免只说“之前那篇论文”而不说明位置。复用较早会话的材料时，确认当前工作区与文件正确。

### 使用技能

使用 `/skills` 查看和管理已安装技能。在输入区域键入 `$` 可查找技能并将名称插入消息。

仍需说明任务和输入路径。技能已安装不代表所需软件、服务或数据均可用；按需解决缺失依赖即可，第一个任务不需要全部扩展。

## 6. 跟踪执行、评审与 subagent

对话中包含不同角色和状态：

- **Core**：执行任务、调用工具、准备或修订回答。
- **Reviewer / Audit**：检查提交的工作并报告问题。
- **Subagents**：执行被委派的子任务，并非每项任务都会并行运行。
- **Working**：工作正在进行或等待后续运行时事件；工具不一定持续产生可见输出。
- **Reviewing**：正在评审当前提交。
- **Completed**：对应请求或 run 已结束，交付物仍需检查。

使用 `/subagents` 或 **F2** 查看 subagent。使用 `/requests` 或 **F4** 查看待处理审批和问题。

评审期间，最终回答发布前可能出现进度与反馈。`No findings` 表示 Reviewer 在该轮没有报告问题，既不是最终回答，也不证明工作毫无错误。

如果执行结束后只有进度文本或修订说明，没有完整回答，请检查 `/status`、`/audit` 和对话记录，并保留 run ID 用于排查。

## 7. 处理审批与问题

使用 `/permissions` 查看权限预设：

| 预设 | 行为与注意事项 |
| --- | --- |
| Ask for approval | 可以读取和编辑工作区文件、运行命令；访问互联网或编辑其他文件需要审批。这不是只读模式 |
| Approve for me | 对检测到的潜在不安全操作提出询问；这不是绝对安全保证 |
| Full Access | 可以访问互联网、编辑工作区之外的文件而无需逐次询问；请谨慎使用 |

初次使用时，保持人工审批开启，并在不含敏感文件的专用目录工作。权限修改通常适用于后续 run。工作活动期间，状态信息反映该 run 的实际权限。

批准操作前，检查目标、命令和可能影响。不要仅为了让任务继续而批准不熟悉的操作。

遇到问题时，按提示选择选项或输入答案后提交。按 Esc 关闭问题面板不等于回答问题；关闭审批面板既不批准也不拒绝操作。通过 `/requests` 或 F4 可重新打开待处理交互。

## 8. 停止、退出与恢复

### 停止当前任务

输入：

```text
/stop
```

主对话中工作正在进行且没有打开菜单、交互面板或对话查看器时，Esc 发送同样的停止请求。在其他位置，它优先处理当前界面，例如关闭面板或退出 Vim 插入模式。不是在任意界面按一次 Esc 都会停止工作。

后端需要处理停止请求，期间可能短暂显示 `Stopping`。使用 `/status` 检查执行状态。`/stop` 停止模型循环，但已经启动的后台命令可能继续。请用 `/ps` 检查，不要假定训练作业或下载也已停止。

### 退出界面

输入 `/exit` 并选择 **Stop and exit**。这会停止活动工作并退出，同时保留运行记录。它不是在模型任务继续后台运行时脱离界面的命令。

### 稍后继续任务

如需现在停止、20 分钟后继续：

1. 输入 `/stop`，等待请求处理并检查状态。
2. 输入 `/exit` 并确认。
3. 稍后使用相同应用目录启动 `ownpi-tui`。
4. 输入 `/resume`，搜索并选择原对话。
5. 按 Enter 继续，按提示处理待回答问题或审批。

间隔时长不会改变恢复机制。恢复依赖持久化记录、可用工作区和受支持的 run 格式，而非暂停进程的内存快照。未完成的步骤或工具操作可能重复执行。

### 使用 Resume 列表

列表显示相对时间和对话标题。直接输入关键词即可搜索。

| 操作 | 作用 |
| --- | --- |
| Up / Down | 选择对话 |
| Enter | 继续可恢复的 run，或查看无法恢复的历史 |
| Ctrl+O | 打开选中 run 的详细信息 |
| Tab | 在当前项目和全部项目之间切换 |
| Ctrl+F | 循环切换状态筛选 |
| Ctrl+S | 在按更新时间和创建时间排序之间切换 |
| Ctrl+U | 清空搜索内容 |
| Esc | 搜索内容非空时先清空，否则返回 |

恢复未完成工作会继续执行。打开空闲 Direct 对话通常只恢复历史，发送消息前不会再次请求模型。能查看记录不代表能恢复它；部分旧格式明确不受支持。

已知 run ID 时，可直接启动：

```bash
ownpi-tui --workspace /path/to/project --resume RUN_ID
```

将 `RUN_ID` 替换为实际标识。切换 CLI 与 TUI 前，先停止原执行，并使用相同应用目录。不要尝试从两个入口同时执行同一个 run。

### 为什么停止后不能提交新问题？

停止可能让当前请求处于未完成状态。Run 保护机制可能拒绝新消息，以免覆盖原工作。可选择：

- 完成原任务：通过 `/resume` 恢复。
- 开始其他任务：确认执行已停止，通过 `/clear` 开启新对话，或通过 `/project` 切换工作区。

不要反复提交同一消息，也不要手动创建缺失的事件文件来强行恢复。

## 9. 检查回答、来源和生成文件

已发布回答可能包含 Markdown、来源链接和本地文件路径。链接能否点击取决于终端；不支持点击时，复制 URL 或按路径打开文件。

- `/copy`：将最新回答复制到笔记或其他文档。
- `/raw`：切换富文本和原始对话渲染，检查 Markdown 或原始内容。
- `/diff`：查看当前 Git 工作区的改动。这不是对任意目录的完整审计，未跟踪产物可能不出现在 diff 中。
- **Ctrl+T**：检查更早的对话，不要仅凭最后几行可见内容判断结果。

以实际任务报告的交付路径为准。可以要求使用 `outputs/` 目录，但并非所有 run 都生成相同文件结构。

对科研产出，应检查每个来源是否支持对应主张，区分模型总结、已发表发现与实际执行实验的结果。自动评审不能替代人的科研判断。

## 10. 故障排查

### 找不到 `ownpi-tui`

检查命令入口：

```bash
command -v ownpi
command -v ownpi-tui
```

默认命令目录安装可将其加入当前终端的 PATH：

```bash
export PATH="$HOME/.local/bin:$PATH"
```

如果存在 `ownpi` 但没有 `ownpi-tui`，请检查安装包是否包含 TUI。`ownpi update` 遵循配置的发布渠道，不会自动安装源码分支的每个新提交。

### 模型连接失败

运行 `ownpi doctor`，然后检查 provider 的 API 类型、Base URL、凭据和准确模型名称。阅读返回的错误，而不是反复修改 reasoning effort 并重试。求助时隐去凭据。

### 持续显示 Working，但没有新回答

先通过 `/requests` 检查待处理交互，再通过 `/status` 查看后端状态。按需使用 `/subagents`、`/ps` 或 `/audit`。执行、评审、等待 provider 和后台命令是不同情况，不能仅凭动画判断任务是否结束。

### Resume 中找不到对话

按 Tab 纳入全部项目，清空搜索内容，并检查状态筛选。如果使用了 `OWNPI_HOME` 或 `--app-home`，确保本次启动使用原应用目录。同一工作区可能搭配不同的运行记录目录。

### 恢复时报告缺少文件或格式不受支持

保留完整错误、run ID 和原 run 目录。不要删除 session、伪造 `run-events.jsonl`，也不要将旧 run 当作新 run 写入。检查记录是否完整，以及安装版本是否支持其格式。

### 执行结束却没有最终回答或输出文件

检查对话记录、`/status` 和 `/audit`，记录最后可见消息、完成状态、run ID 及缺失交付物。评审通过、执行停止、请求完成和回答交付是不同事件。

## 11. 日常速查

| 目标 | 命令或快捷键 |
| --- | --- |
| 打开项目 | `ownpi-tui --workspace /path/to/project` |
| 浏览命令 | `/commands` |
| 选择模型 | `/model` |
| 设置 reasoning effort | `/reasoning` |
| 选择执行模式 | `/runtime` |
| 配置权限 | `/permissions` |
| 查看技能 | `/skills` |
| 查看 subagent | `/subagents` 或 F2 |
| 处理问题与审批 | `/requests` 或 F4 |
| 停止当前工作 | `/stop` |
| 恢复 run | `/resume` |
| 在同一项目开启新对话 | `/clear` |
| 切换项目 | `/project` |
| 复制最新回答 | `/copy` |
| 检查执行状态 | `/status` |
| 停止并退出 | `/exit`，然后选择 **Stop and exit** |

工作前检查模型、权限和工作区；工作中处理问题与审批；工作后核实回答、来源和实际文件。这些习惯比记住每个命令更重要。
