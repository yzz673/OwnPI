# CLI 参考

**中文** | [English](cli-reference.md)

本文说明 CLI 当前提供的命令。首次安装和研究任务模板见[快速开始](getting-started.zh-CN.md)。

## 使用约定

- 将 `<run_id>`、`<run_dir>` 和示例路径替换为实际值，不要输入尖括号。
- 默认应用目录为 `~/.ownpi`。如果设置了 `OWNPI_HOME`，在管理同一个 run 的所有终端中都应使用相同值。
- 在命令或命令组后添加 `--help` 可查看支持的参数，例如 `ownpi run start --help`。
- 在支持该参数的命令中，`--config PATH` 选择配置文件而非 `<app_home>/config.yaml`，不会切换应用目录。
- 在支持该参数的命令中，`--json` 请求结构化输出，不会让任务转入后台，也不会跳过模型调用。

## 安装与配置

### `ownpi init`

```bash
ownpi init [--force]
```

创建应用目录和缺少的配置模板，不会建立可用的模型连接。安装与 setup 已经会初始化应用目录，多数用户无需单独运行此命令。`--force` 会覆盖生成的 `config.yaml` 和 `.env.example` 模板，不会替换保存凭据的 `.env`。使用前请备份手动配置。

### `ownpi setup`

```bash
ownpi setup
```

交互配置 provider、API key、Base URL、模型、适用的 reasoning effort 和 review profile。OpenAI-compatible 选项使用 Responses API，Anthropic-compatible 选项使用 Messages API。Setup 会替换生成的 `config.yaml` 和 `.env`，不会在其中追加 provider。兼容要求与设置详见[配置](configuration.zh-CN.md)。

### `ownpi info`

```bash
ownpi info
```

以 JSON 显示安装组件、应用目录和配置路径、模型就绪情况及托管资源的摘要。可用它定位安装目录或在问题报告中提供版本信息。它不验证真实模型请求。

### `ownpi doctor`

```bash
ownpi doctor [--json] [--install-check]
```

诊断安装资源、配置、凭据可用性和运行时就绪情况。`--json` 输出 JSON 诊断报告。`--install-check` 选择供安装器验证使用的安装检查，不能替代使用前的完整检查。两种模式都不会发送真实模型请求。

### `ownpi config show`

```bash
ownpi config show [--config PATH]
```

显示运行时和模型配置摘要，包括选定的模型 profile 和生效的 review policy。它只检查本地设置，不测试模型访问。

### `ownpi config set-review-profile`

```bash
ownpi config set-review-profile <auto|standard|rigorous|exhaustive> [--config PATH]
```

无需重新运行 setup 即可保存 review profile。例如，`ownpi config set-review-profile rigorous` 允许一个 Research 工作流最多进行三次 Analysis 回访。所有 profile 都不限制 Reviewer 轮数。`auto` 在 Task Level（Direct）下解析为 `standard`，在 Research Level 下解析为 `rigorous`。全部预算见[Review profile](configuration.zh-CN.md#review-profile)。

## Runs

### `ownpi run start`

```bash
ownpi run start [message] [--message-file PATH] [--workspace PATH] [--background] [--json] [--config PATH]
```

根据 `runtime.default_profile` 创建并执行新的 Task Level（Direct）或 Research Level run，详见[执行模式](configuration.zh-CN.md#执行模式)。任务可以作为带引号的位置参数，或通过 `--message-file` 提供 UTF-8 文件。科研项目应使用说明问题、资源、限制和交付物的简报，见[任务模板](getting-started.zh-CN.md#启动第一个任务)。文件按任务文本读取，不作为 YAML 或 JSON 配置处理。使用 `--message-file -` 可从标准输入读取任务文本。

`--workspace` 选择研究目录，必要时创建该目录；默认使用启动命令时的目录。`--background` 启动独立后台进程并立即返回 run ID，不等待任务完成。不加此参数时任务在前台运行。`--json` 改变 CLI 结果格式，不改变前台或后台运行方式。

```bash
ownpi run start --message-file /path/to/task.md --workspace /path/to/project
ownpi run start --message-file /path/to/task.md --workspace /path/to/project --background
```

### `ownpi run list`

```bash
ownpi run list [--json] [--config PATH]
```

列出所选应用目录中的托管 run。可以用返回的 run ID 或目录执行 `status`、`stop` 或 `resume`。此命令不会启动或恢复任务。若找不到某个 run，请检查 `OWNPI_HOME` 是否指向启动它时使用的应用目录。

### `ownpi run status`

```bash
ownpi run status <run_id_or_run_dir> [--json] [--config PATH]
```

读取 run 状态和 Runtime 活动，不作修改。目标可以是 `run list` 返回的 run ID，也可以是 session 管理的 run 目录。

| 字段 | 含义 |
| --- | --- |
| State | 综合 Runtime 进程是否活动后评估的当前状态。 |
| Recorded state | 最近一次保存的状态；例如恢复运行期间可能滞后于实际活动。 |
| Stage / Stage attempt | 最近记录的阶段和尝试次数，不是完成百分比。 |
| Runtime active / Runtime PID | Runtime 所属进程是否活动；若活动则显示其进程 ID。 |
| Last recorded activity | rollout 最近一次保存活动的时间；安静一段时间本身不能证明任务挂起。 |
| Run directory | 托管记录目录，可用于查找诊断信息；它与研究工作区不同。 |

`in_progress` 表示 Runtime 正在活动。`stopped` 表示已保存的停止状态，`unexpected_exit` 表示执行似乎未完成但 Runtime 已不活动。这两种状态都可用 `resume` 继续。`completed` 表示 run 已结束；请检查产出和局限，判断是否满足任务要求。Runtime 不活动本身不能证明任务已完成。

### `ownpi run stop`

```bash
ownpi run stop <run_id_or_run_dir> [--reason TEXT] [--json] [--config PATH]
```

请求立即取消活动工作，同时保留记录以便恢复。`--reason` 可附加说明，例如 `--reason "stop for now"`。在前台 run 所在终端按 **Ctrl-C** 会请求同样的停止操作。可用此命令停止后台 run，或从使用相同应用目录的另一终端停止任务。

取消和清理可能需要几秒。通过 `run status` 检查 Runtime 是否已退出。停止 OwnPI 不保证取消独立的外部任务，例如远程训练作业。

### `ownpi run resume`

```bash
ownpi run resume <run_id_or_run_dir> [--background] [--json] [--config PATH]
```

在任务停止或进程意外退出后，从已保存记录继续现有 run。恢复时可能重复未完成的工作，而非从停止时的精确指令位置重启。请保持原工作区和所需资源可用。该 run 的 Runtime 仍活动时，不要另行启动竞争性的 resume。

Resume 默认在前台运行。添加 `--background` 可启动独立后台进程并立即返回：

```bash
ownpi run resume <run_id>
ownpi run resume <run_id> --background
```

## 更新与 Neuroscience Pack

OwnPI Core 无需可选的 Neuroscience Pack 也可使用。Pack 增加 Brainlib、神经科学技能和研究知识。请在该应用目录没有活动 research run 时执行安装或维护命令。

### `ownpi update`

```bash
ownpi update
```

从安装配置的发行渠道更新 OwnPI Core 及其 Dependency Bundle，保留设置和 run 记录。可选 Pack 使用独立的更新命令。

### `ownpi pack install`

```bash
ownpi pack install neuroscience
```

下载并安装与托管 OwnPI 安装兼容的 Pack，需要基础安装可正常工作。安装后使用 `ownpi pack status neuroscience` 和 `ownpi tools doctor brainlib` 检查。

### `ownpi pack status`

```bash
ownpi pack status neuroscience [--json]
```

检查 Pack 是否已安装、启用和就绪。`--json` 包含详细检查数据。此命令不会安装或修改 Pack。

### `ownpi pack update`

```bash
ownpi pack update neuroscience
```

获取并安装发行端点提供的 Pack。它与 `pack install` 使用相同的安装路径，因此也可以在 Pack 缺失时安装。仅更新 OwnPI 不会执行此命令。

### `ownpi pack rollback`

```bash
ownpi pack rollback neuroscience
```

激活本地保留的、早于当前版本的最近一个 Pack 版本。这要求本地仍安装有有效的旧版本；没有可用版本时会失败。它不会回滚 OwnPI Core 或研究文件。

### `ownpi pack disable`

```bash
ownpi pack disable neuroscience
```

停用已安装的 Pack，不删除其发布文件。Core 仍可使用。运行 `pack enable` 可重新启用保留的 Pack。

### `ownpi pack enable`

```bash
ownpi pack enable neuroscience
```

重新启用已安装但停用的 Pack。它不会下载缺失的 Pack；缺失时请使用 `pack install`。

### `ownpi pack remove`

```bash
ownpi pack remove neuroscience
```

删除已安装的 Pack 版本和托管 Brainlib 资源，并断开 Pack 托管知识的连接。此操作也会删除用于回滚的保留版本，不会删除研究工作区或 run 记录。可使用 `pack install` 重新安装。

## 技能与工具

### `ownpi skills list`

```bash
ownpi skills list [--json]
```

列出可见研究技能及其来源。Core 技能随包提供，安装 Neuroscience Pack 会增加对应技能。CLI 不提供额外技能的发现或安装命令。

### `ownpi skills remove`

```bash
ownpi skills remove <skill_name> [--json]
```

删除 `<app_home>/skills` 中用户安装的技能目录，参数使用目录名。此命令不能删除内置技能或 Pack 技能，即使它们出现在 `skills list` 中；没有匹配的用户安装技能时会失败。全新安装通常没有此类用户安装技能。删除结果以 JSON 输出。

### `ownpi tools list`

```bash
ownpi tools list [--json]
```

显示托管工具可用性和诊断摘要。当前 CLI 检查 Brainlib；看到它被列出不代表其 Pack 已安装。

### `ownpi tools doctor`

```bash
ownpi tools doctor brainlib [--json]
```

检查 Brainlib 的托管安装及入口。Brainlib 需要 Neuroscience Pack；若不可用，请按情况安装或启用 Pack，然后重新检查。此命令不会运行研究任务。

## 问题与权限

以下命令处理现有 run 中待回答的问题和待处理的权限请求，不是 setup 步骤。TUI 也提供[交互式问题与审批控件](tui.zh-CN.md#7-处理审批与问题)。

这些命令需要 session 管理的 run 目录，而不是 run ID。可通过 `ownpi run status <run_id>` 获取目录。它们不会创建不存在的问题或权限请求。

| 命令 | 功能与用法 |
| --- | --- |
| `ownpi clarify show <run_dir>` | 查看待回答的澄清问题；没有待处理问题时会说明。 |
| `ownpi clarify answer <run_dir> --answers-file PATH` | 为待回答的问题提交包含 `answers` 映射的 JSON 文件。使用 `clarify show` 显示的问题 ID 作为键，答案文本或提供的选项名称作为值。 |
| `ownpi permission show <run_dir>` | 查看待处理的工具权限请求；没有待处理请求时会说明。 |
| `ownpi permission approve <run_dir> [--scope once\|session]` | 批准仍有效的待处理请求一次（默认）或在本次 session 内批准。不能批准已停止的调用。 |
| `ownpi permission deny <run_dir>` | 拒绝所需执行并结束暂停的 run。若只是暂时停止并计划恢复，请使用 `run stop`。 |
| `ownpi permission deny-command <run_dir>` | 仅拒绝当前待处理的工具命令，让 agent 在任务继续时尝试其他方案。 |

自由文本问题的答案文件示例：`{"answers": {"question_id_from_show": "Your answer"}}`。`answer`、`approve`、`deny` 和 `deny-command` 还接受 `--config PATH`、`--llm-profile NAME`、`--project-root PATH` 和 `--json`。`--llm-profile` 选择操作使用的已有模型 profile；`--project-root` 明确指定运行时工作区根目录。提交交互响应可能继续执行并调用模型。这些命令不支持 `--background`；操作后先查看 `run status`，再判断是否需要 resume。
