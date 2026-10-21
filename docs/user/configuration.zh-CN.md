# 配置

**中文** | [English](configuration.md)

## 配置文件与模型设置

默认应用目录是 `~/.ownpi`。运行 `ownpi setup` 可配置或更换模型连接。
重新运行 setup 会覆盖生成的 `config.yaml` 和 `.env`，不会在现有配置中追加 provider；如有需要保留的手动设置，请先备份。

| 设置 | 填写内容 |
| --- | --- |
| Provider | OpenAI-compatible 或 Anthropic-compatible |
| API key | 所选服务方提供的有效密钥 |
| Base URL | 服务地址，不追加具体请求接口 |
| Model | 服务方实际支持的准确模型名称 |
| Reasoning effort | Setup 询问时，选择模型支持的推理强度 |

设置保存在应用目录下的 `config.yaml`，凭据保存在 `.env`，请勿分享凭据文件。
OpenAI-compatible 选项配置 `openai_responses`，仅支持 Chat Completions 的网关无法使用；
Anthropic-compatible 选项配置 `anthropic_messages`。所选模型和服务必须支持对应接口及工具调用。
Base URL 使用服务方提供的地址，可保留所需的版本前缀，但不要追加 `/responses`、
`/chat/completions` 或 `/messages`。OpenAI-compatible 选项会询问 reasoning effort，
应选择模型和服务支持的值。所选模型同时用于研究和审阅，默认 review profile 为 `auto`。
查看当前生效配置：

```bash
ownpi config show
```

此命令显示选定的模型 profile、运行时设置和生效的 review policy。它只检查本地设置，不会发送模型请求，也不能证明服务方接受该配置。

如需更换应用目录，在安装和后续运行命令前设置：

```bash
export OWNPI_HOME=/path/to/app-home
```

## Review profile

Beta 使用 Research Level workflow，默认的 `auto` 会选择 `rigorous`。

| Profile | 每个审阅循环的 Reviewer 轮数上限 | 整个 workflow 的 Analysis 回访次数上限 |
| --- | --- | --- |
| `auto` | 与 `rigorous` 相同 | 与 `rigorous` 相同 |
| `standard` | 1 | 1 |
| `rigorous` | 不设上限 | 3 |
| `exhaustive` | 不设上限 | 不设上限 |

回访是返回之前的研究阶段进行修改。达到审阅轮数上限会结束该循环，
不要求 Reviewer 确认没有问题。不设上限的策略可能长时间运行，
需要时可以[按 Ctrl-C 或用 run stop 停止任务](cli-reference.zh-CN.md)，之后用 `resume` 继续。

```bash
ownpi config set-review-profile standard
```

## Subagent

默认启用 subagent，每批最多 8 个任务。Core 决定何时委派工作；启用 subagent 不代表每项任务都会使用它。请在现有 `config.yaml` 的 `runtime` 部分修改以下字段，并保留其他设置：

```yaml
runtime:
  subagent_enabled: true
  subagent_max_count: 8
```

将 `subagent_enabled` 设为 `false` 可关闭。`subagent_max_count` 必须为正整数，
限制单批任务数量，不限制整个 run 的累计数量。

## 工作区与权限

可通过 `ownpi run start --workspace /path/to/project --message-file /path/to/task.md` 指定研究工作区；省略 `--workspace` 时使用启动命令时的当前目录。列出、停止或恢复 run 时继续使用同一个应用目录。工作区保存研究文件，应用目录保存配置和托管 run 记录，两者用途不同。

Beta 使用无人值守的完全访问模式（配置中的 `full_access`）和 Research Level workflow。请使用专用工作区，并在任务说明中提供清晰的资源路径和限制。交互式权限审批不属于当前 Beta 支持的工作流。
