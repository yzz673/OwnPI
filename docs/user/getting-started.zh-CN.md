# 快速开始

**中文** | [English](getting-started.md)

## 安装

OwnPI v0.1.0 通过 GitHub Releases 提供 Apple Silicon macOS（arm64）和 Linux x86_64 安装包。安装器包含运行所需的 Python 和依赖，无需准备 Conda 环境、源码仓库或使用 `sudo`。

```bash
curl -fsSL https://raw.githubusercontent.com/yzz673/OwnPI/main/install.sh | bash
```

默认应用目录为 `~/.ownpi`，命令安装在 `~/.local/bin/ownpi` 和 `~/.local/bin/ownpi-tui`。如果终端提示找不到命令，把命令目录加入当前终端的 `PATH`：

```bash
export PATH="$HOME/.local/bin:$PATH"
```

要让新终端也使用此设置，请将该行加入 shell 启动文件。若要使用其他应用目录，请在安装前设置 `OWNPI_HOME`，并在后续命令中保持相同设置；详见[配置](configuration.zh-CN.md)。

## 配置模型访问

准备好模型服务类型、API key、Base URL 和准确的模型名称。OpenAI-compatible 选项使用 Responses API；仅支持 Chat Completions 的服务不适用于此配置。Anthropic-compatible 选项使用 Messages API。确认服务支持对应接口和工具调用，然后运行：

```bash
ownpi setup
ownpi doctor
```

Setup 还会在适用时询问 reasoning effort 和 review profile。初次使用可以保留默认的 `auto`。Base URL 使用服务方提供的地址，不要追加 `/responses` 或 `/messages` 等单个请求接口。设置保存在 `~/.ownpi/config.yaml`，凭据保存在 `~/.ownpi/.env`。重新运行 setup 会覆盖这些生成文件；如需保留手动修改，请先备份。

`doctor` 检查安装与配置，不会实际发送模型请求。首次任务会验证服务方是否
接受密钥和模型；请求失败时请查看[故障排查](../troubleshooting.zh-CN.md)。

CLI 和 TUI 共用同一应用目录中的配置、凭据和运行记录。CLI setup 选择 Research 模式和 Full Access；提交任务前请检查模式与权限。使用不含敏感文件的专用工作区。启动、审批、恢复和自定义安装路径见 [TUI 指南](tui.zh-CN.md)。

## 可选 Neuroscience Pack

```bash
ownpi pack install neuroscience
ownpi pack status neuroscience
ownpi tools doctor brainlib
```

Pack 提供 Brainlib、神经科学 skills 和相关科研知识。未安装 Pack 时，OwnPI Core 也可正常使用。如果任务不需要这些能力，可以跳过此步骤。

## 启动第一个任务

如需交互执行任务，在项目目录启动 TUI：

```bash
ownpi-tui
```

使用 `/direct` 选择 Task Level（Direct），输入请求后可继续发送后续消息。端到端研究使用 `/research`。这些设置适用于新任务；已有 run 保留原模式。会话之间切换模式的方法见 [TUI 指南](tui.zh-CN.md#执行模式)。

CLI 使用 `config.yaml` 中的 `runtime.default_profile`：`task` 对应 Direct，`research` 对应 Research。以下示例假定使用 CLI setup 生成的 `research` 配置，详见[执行模式](configuration.zh-CN.md#执行模式)。

创建 UTF-8 文本文件 `task.md`，写明研究问题或方向、预期贡献、可用资源及其具体路径、
实际限制和预期交付物。无需预先设计好每个实验；请指出 OwnPI 应调查和制定哪些决策。
单独提供一个简短主题名称，无法说明这些要求。研究简报模板保留英文：

```text
Treat this as an end-to-end research project in [research area].
Research question or direction: [what should the study understand, explain, or establish?]
Intended contribution: [the new method, explanation, or evidence the project should produce].
Available resources: [datasets, code, papers, tools, and Python environments, with concrete paths and any access instructions].
Constraints: [available compute, time or cost limits, data restrictions, and other requirements].
Expected deliverables: [manuscript, supplementary materials, source data, source code, or other outputs].
Develop and execute the research plan, evaluate the evidence, and state the limits of the conclusions.
```

例如，睡眠与记忆项目应说明有哪些记录和行为测量、文件存放位置、要研究的关系，以及预期证据和交付物。只要求“制定研究计划”意味着只需要计划；只有当计划本身就是预期结果时，才应这样描述。

保存文件后，将命令中的任务文件和工作区路径替换为实际路径并启动：

```bash
ownpi run start --message-file task.md --workspace /path/to/workspace
```

省略 `--workspace` 时使用当前目录。工作区是 OwnPI 处理研究文件的目录，请选择专用目录。命令默认在前台运行；添加 `--background` 后会返回终端，但任务会继续运行。可通过 `run list` 查找 run ID 并检查状态：

```bash
ownpi run list
ownpi run status <run_id>
```

研究文件在工作区中生成，配置和托管 run 记录保存在 `~/.ownpi`。
前台运行时可按 **Ctrl-C** 停止，也可在另一终端使用相同应用目录执行以下命令：

```bash
ownpi run stop <run_id>
ownpi run resume <run_id>
```

用户主动停止或进程意外退出后都可以使用 `resume`；恢复时可能会重复未完成的工作。
添加 `--background` 可在后台恢复。参数和状态字段详见 [CLI 参考](cli-reference.zh-CN.md)。

## 查找结果

使用 `ownpi run status <run_id>` 查看进度。`State: completed` 表示 run 已结束；
仅 `Runtime active: no` 并不足以判断完成，因为已停止或失败的 run 同样可能没有活动 Runtime。
运行结束也不保证所有交付物都符合要求，请检查实际发现和结论局限。

阅读 run 结束时的摘要并检查工作区中的生成文件。Research 的最终交付物通常整理在 `<workspace>/output_synthesis/final_delivery/` 下，并附有介绍文件位置与用途的 `README.md`。Direct 任务返回回答及任务相关产物，不要求使用该科研交付目录。模型会生成这些文件，因此实际目录和文件名可能不同。请以本次 run 的交付说明和文件为准，不要将未完成 run 的部分输出视为最终交付物。

## 更新

```bash
ownpi update
ownpi pack update neuroscience
```

第二条命令仅用于已经安装 Pack 的情况。更新会保留设置和任务记录。

修改设置见[配置](configuration.zh-CN.md)，常见问题见
[故障排查](../troubleshooting.zh-CN.md)。
