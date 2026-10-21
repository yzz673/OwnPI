# CLI 参考

**中文** | [English](cli-reference.md)

本文说明 CLI 当前暴露的命令，包括下文列出的兼容命令。首次安装和研究任务模板见[快速开始](getting-started.zh-CN.md)。

## 使用约定

- `<run_id>`、`<run_dir>` 和示例路径都要替换成实际值，不要输入尖括号。
- 默认应用目录为 `~/.ownpi`。如果设置了 `OWNPI_HOME`，在管理同一个 run 的所有终端中都应使用相同值。
- 在命令或命令组后添加 `--help` 可查看支持的参数，例如 `ownpi run start --help`。
- `--config PATH` 选择配置文件而非 `<app_home>/config.yaml`，不会切换应用目录。
- `--json` 请求结构化输出，不会让任务转入后台，也不会跳过模型调用。

## 配置与检查

```bash
ownpi setup
ownpi info
ownpi doctor
ownpi config show
ownpi config set-review-profile rigorous
```

`setup` 配置模型访问，`info` 查看安装版本与 Pack，`doctor` 检查安装状态。
Review profile 见[配置说明](configuration.zh-CN.md)。`ownpi init` 是可选命令，
安装和 setup 已经会初始化应用目录。

常用签名：`ownpi init [--force]`、`ownpi doctor [--json] [--install-check]`、
`ownpi config show [--config PATH]`、`ownpi config set-review-profile <auto|standard|rigorous|exhaustive> [--config PATH]`。
`init --force` 会覆盖生成的 `config.yaml` 和 `.env.example` 模板，不会覆盖保存凭据的 `.env`；
使用前应备份手动配置。`doctor --install-check` 用于安装器检查，不能替代完整诊断。
这些诊断命令不会实际发送模型请求。`info` 输出安装组件、应用目录和配置路径、模型就绪情况及托管资源的 JSON 摘要。

各命令的完整签名和行为如下：

| 命令 | 签名 | 功能 |
| --- | --- | --- |
| `ownpi init` | `ownpi init [--force]` | 创建应用目录和缺少的配置模板；不会建立可用的模型连接。安装与 setup 已初始化目录，通常无需单独运行。 |
| `ownpi setup` | `ownpi setup` | 交互配置 provider、API key、Base URL、模型、适用的 reasoning effort 和 review profile。会替换生成的 `config.yaml` 与 `.env`，不会追加 provider。 |
| `ownpi info` | `ownpi info` | 以 JSON 显示安装组件、应用目录和配置路径、模型就绪情况及托管资源；不验证真实模型请求。 |
| `ownpi doctor` | `ownpi doctor [--json] [--install-check]` | 诊断安装资源、配置、凭据可用性和运行时就绪情况。`--install-check` 用于安装器验证，不替代使用前的完整检查。 |
| `ownpi config show` | `ownpi config show [--config PATH]` | 显示运行时和模型配置摘要，包括模型 profile 与生效的 review policy；只检查本地设置。 |
| `ownpi config set-review-profile` | `ownpi config set-review-profile <auto\|standard\|rigorous\|exhaustive> [--config PATH]` | 无需重新 setup 即可保存 review profile；`auto` 在 Beta 的 Research Level workflow 中等同于 `rigorous`。 |

## 任务

| 命令 | 签名 | 功能 |
| --- | --- | --- |
| `ownpi run start` | `ownpi run start [message] [--message-file PATH] [--workspace PATH] [--background] [--json] [--config PATH]` | 创建并执行新的 research run。任务可作为带引号的位置参数、UTF-8 文件或 `--message-file -` 的标准输入提供。 |
| `ownpi run list` | `ownpi run list [--json] [--config PATH]` | 列出所选应用目录中的托管 run；可用返回的 ID 或目录执行 status、stop、resume。 |
| `ownpi run status` | `ownpi run status <run_id_or_run_dir> [--json] [--config PATH]` | 只读查询 run 状态和 Runtime 活动，不修改任务。 |
| `ownpi run stop` | `ownpi run stop <run_id_or_run_dir> [--reason TEXT] [--json] [--config PATH]` | 请求立即取消活动工作并保留记录以便恢复；前台 Ctrl-C 有相同效果。 |
| `ownpi run resume` | `ownpi run resume <run_id_or_run_dir> [--background] [--json] [--config PATH]` | 从停止或意外退出后的保存记录继续；默认前台运行，恢复可能重做尚未完成的工作。 |

```bash
ownpi run start "Describe your research task in English."
ownpi run start --message-file task.md --workspace /path/to/workspace
ownpi run start --message-file task.md --workspace /path/to/workspace --background
ownpi run start --message-file - --workspace /path/to/workspace
ownpi run list
ownpi run status <run_id>
ownpi run status <run_id> --json
ownpi run stop <run_id> --reason "停止工作"
ownpi run resume <run_id>
ownpi run resume <run_id> --background
```

`--message-file` 读取 UTF-8 文本文件。`--workspace` 默认为当前目录，
指定目录不存在时会自动创建。`--background` 返回 run ID 后继续后台执行，
不代表任务已经完成。用 `run list` 查找任务，用 `run status` 查看指定任务的
状态、当前阶段和 Runtime 是否活动。状态查询是只读操作，也可用 run 目录代替 ID。

`--message` 是带引号的任务文本；`--message-file` 也可用 `-` 从标准输入读取。
文件内容按任务文本处理，不是 YAML 或 JSON 配置。`--json` 只改变命令结果格式。

`start` 和 `resume` 默认在前台运行；两者均可加 `--background`，让任务在后台
继续执行并返回终端。前台运行时，直接在当前终端按 **Ctrl-C** 即可停止；
后台任务或从另一个终端停止时，使用 `run stop`。两者都请求立即取消当前工作，
取消和清理活动操作时可能需要几秒。

`run list` 和 `run status` 也接受 `--json` 与 `--config PATH`。`stop` 接受
`<run_id_or_run_dir>`、可选的 `--reason TEXT`、`--json` 和 `--config PATH`；
停止不会保证取消独立的外部任务，例如远程训练作业。`resume` 接受 run ID 或 run 目录，
以及 `--background`、`--json` 和 `--config PATH`。Runtime 仍活动时不要启动另一个 resume。

`run status` 的字段含义：

| 字段 | 含义 |
| --- | --- |
| State | 综合 Runtime 进程是否活动后评估的当前状态。 |
| Recorded state | 最近一次保存的状态；例如恢复运行期间可能滞后于实际活动。 |
| Stage / Stage attempt | 最近记录的阶段和尝试次数，不是完成百分比。 |
| Runtime active / Runtime PID | Runtime 进程是否活动；若活动则显示其进程 ID。 |
| Last recorded activity | rollout 最近一次保存活动的时间；安静一段时间本身不能证明任务挂起。 |
| Run directory | 托管记录目录，可用于查找诊断信息；它与研究工作区不同。 |

`in_progress` 表示 Runtime 正在活动；`stopped` 表示已保存的停止状态；`unexpected_exit` 表示执行似乎未完成但 Runtime 已不活动，这两种状态都可用 `resume` 继续。`completed` 表示 run 已结束，请检查产出和局限以判断是否满足任务要求。Runtime 不活动本身不能证明任务已完成。

主动停止或进程意外退出后，都可用 `resume` 从持久化记录继续，未完成的工作
可能重新执行。不同终端应使用同一个应用目录。停止 OwnPI 不保证取消独立运行的
外部任务，例如远端训练作业。

## 更新与可选 Neuroscience Pack

OwnPI Core 无需可选 Neuroscience Pack 也可使用。Pack 增加 Brainlib、神经科学技能和研究知识。请在该应用目录没有 research run 活动时执行安装或维护命令。

| 命令 | 签名 | 功能 |
| --- | --- | --- |
| `ownpi update` | `ownpi update` | 从安装配置的发行渠道更新 OwnPI Core 和 Dependency Bundle，保留设置与 run 记录；不会更新 Pack。 |
| `ownpi pack install` | `ownpi pack install neuroscience` | 下载并安装与托管 OwnPI 安装兼容的 Pack；需要基础安装正常。 |
| `ownpi pack status` | `ownpi pack status neuroscience [--json]` | 检查 Pack 是否已安装、启用和就绪；不修改 Pack。 |
| `ownpi pack update` | `ownpi pack update neuroscience` | 获取并安装发行端点提供的 Pack；若当前缺失也会安装。 |
| `ownpi pack rollback` | `ownpi pack rollback neuroscience` | 激活本地保留的最近一个较旧 Pack 版本；不会回滚 Core 或研究文件。 |
| `ownpi pack disable` | `ownpi pack disable neuroscience` | 停用已安装 Pack，但保留其发布文件；Core 仍可使用。 |
| `ownpi pack enable` | `ownpi pack enable neuroscience` | 重新启用已安装但停用的 Pack；不会下载缺失的 Pack。 |
| `ownpi pack remove` | `ownpi pack remove neuroscience` | 删除 Pack 版本、托管 Brainlib 资源和 Pack 知识连接；不会删除工作区或 run 记录。 |

```bash
ownpi update
ownpi pack install neuroscience
ownpi pack status neuroscience
ownpi pack update neuroscience
ownpi pack rollback neuroscience
ownpi pack disable neuroscience
ownpi pack enable neuroscience
ownpi pack remove neuroscience
```

`update` 更新 OwnPI，Pack 需要单独安装和更新。可以暂时禁用 Pack 后再启用，
也可以移除，均不影响 Core 独立使用。存在之前安装的版本时，
`rollback` 可以把 Pack 回退到该版本。

`update` 更新 OwnPI Core 及其 Dependency Bundle，并保留设置和 run 记录；Pack 使用独立更新命令。Pack 安装和维护应在该应用目录没有活跃 research run 时执行。`pack status neuroscience [--json]` 只检查安装、启用和就绪状态，不会修改 Pack；`pack update neuroscience` 会从发行端点获取并安装可用版本，即使 Pack 尚未安装也可安装。`rollback` 仅激活本地保留的较旧 Pack 版本，不会回滚 Core 或研究文件。`disable` 保留文件并停用 Pack，`enable` 重新启用已安装的 Pack，不会下载缺失的 Pack。`remove` 删除已安装版本、托管 Brainlib 资源和 Pack 托管知识连接，也会移除用于回滚的旧版本；不会删除工作区或 run 记录。

## 技能与工具

```bash
ownpi skills list
ownpi skills remove <skill_name>
ownpi tools list
ownpi tools doctor brainlib
```

研究技能随 OwnPI 提供，Beta 暂不支持发现和安装额外技能。这些命令用于查看技能、
移除已有的用户安装技能，以及检查已安装工具。Brainlib 需要 Neuroscience Pack。
更多参数可运行 `ownpi --help`，或在具体命令后添加 `--help`。

`skills list [--json]` 列出可见技能及其来源；Core 技能随包提供，安装 Neuroscience Pack 会增加对应技能。`skills remove <skill_name> [--json]` 只可删除 `<app_home>/skills` 中同名的用户安装技能，不能删除 Core 或 Pack 自带技能；成功结果以 JSON 输出。`tools list [--json]` 显示托管工具可用性和诊断摘要，列出 Brainlib 不代表 Pack 已安装。`tools doctor brainlib [--json]` 检查 Brainlib 托管安装及入口，不会运行研究任务。

## 帮助中仍显示的兼容命令

当前 CLI 还暴露以下澄清和权限命令。它们只处理现有 run 中等待处理的交互，不是 setup 步骤，也不属于 Beta 支持的无人值守工作流。命令出现在帮助中不代表普通 Beta 使用支持交互式权限模式。

这些命令需要 session 管理的 run 目录，而不是 run ID。可通过 `ownpi run status <run_id>` 获取目录。它们不会创建不存在的问题或权限请求。

| 命令 | 功能与用法 |
| --- | --- |
| `ownpi clarify show <run_dir>` | 查看待回答的澄清问题；没有待处理问题时会说明。 |
| `ownpi clarify answer <run_dir> --answers-file PATH` | 提交包含 `answers` 映射的 JSON 文件。使用 `clarify show` 显示的问题 ID 作为键，答案文本或提供的选项名称作为值。 |
| `ownpi permission show <run_dir>` | 查看待处理的工具权限请求；没有待处理请求时会说明。 |
| `ownpi permission approve <run_dir> [--scope once\|session]` | 批准仍有效的待处理请求一次（默认）或在本次 session 内批准。不能批准已停止的调用。 |
| `ownpi permission deny <run_dir>` | 拒绝所需执行并结束暂停的 run。若只是暂时停止并计划恢复，请使用 `run stop`。 |
| `ownpi permission deny-command <run_dir>` | 仅拒绝当前待处理的工具命令，让 agent 在任务继续时尝试其他方案。 |

自由文本问题的答案文件示例：`{"answers": {"question_id_from_show": "Your answer"}}`。`answer`、`approve`、`deny` 和 `deny-command` 还接受 `--config PATH`、`--llm-profile NAME`、`--project-root PATH` 和 `--json`。`--llm-profile` 选择操作使用的已有模型 profile；`--project-root` 明确指定运行时工作区根目录。提交交互响应可能继续执行并调用模型。这些命令不支持 `--background`；操作后先查看 `run status`，再判断是否需要 resume。
