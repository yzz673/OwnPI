# 故障排查

**中文** | [English](troubleshooting.md)

## 先运行诊断

```bash
ownpi doctor
ownpi info
```

- **找不到命令**：按[快速开始](user/getting-started.zh-CN.md)把
  `~/.local/bin` 加入 `PATH`。若需在新终端中生效，请将 export 命令加入 shell 启动文件。
- **缺少配置或凭据**：运行 `ownpi setup`。
- **模型请求失败**：检查服务类型、Base URL、API key 和准确的模型名称。
  `doctor` 通过不代表已经验证过真实模型调用。HTTP 401/403 通常与授权有关，
  404 可能是 URL 或模型不正确，具体以服务方返回信息为准。OpenAI-compatible
  配置要求 Responses API；仅支持 Chat Completions 的网关无法使用。另请查看
  [配置说明](user/configuration.zh-CN.md)。
- **缺少产品资源或安装损坏**：使用安装器重新安装，再运行 `doctor`。
  仍然失败时请报告错误。
- **无法使用 Brainlib**：运行 `ownpi pack status neuroscience` 检查 Pack 状态；若未安装则安装可选 Pack，若已停用则启用，然后运行 `ownpi tools doctor brainlib` 检查。

## 下载或 checksum 校验失败

检查网络，重试[快速开始](user/getting-started.zh-CN.md)中的安装命令。
如果收到私有内测下载地址，请按随附的访问说明操作。
不要跳过 TLS 或 checksum 校验；反复失败时请报告命令和错误信息。

## 任务长时间没有输出或意外中断

用 `ownpi run list` 查找 run ID，再查看任务状态：

```bash
ownpi run status <run_id>
```

后台命令返回 run ID 不代表任务已完成。终端输出少也不一定表示卡住，
任务可能正在子智能体中继续。可对照状态、Runtime 是否活动、阶段和最近记录的活动；
状态记录可能落后于当前执行情况，Runtime 不活动本身也不能证明任务成功。
详见 [status 字段说明](user/cli-reference.zh-CN.md#ownpi-run-status)。主动停止或进程意外退出后，可选择前台或后台恢复：

```bash
ownpi run resume <run_id>
ownpi run resume <run_id> --background
```

如果 Runtime 仍在运行，而你想停止后重新继续，先在前台终端按 Ctrl-C，
或执行 `ownpi run stop <run_id>`。清理可能需要几秒；请检查 Runtime 已退出后再恢复。
Resume 使用已保存记录，可能会重复未完成的工作。请保持原工作区和所需资源可用。
如果 `run list` 找不到已知 run，检查 `OWNPI_HOME` 是否仍指向启动该 run 时使用的应用目录；
更改工作区不会切换应用目录。停止和恢复的说明见 [CLI 参考](user/cli-reference.zh-CN.md)。

## 任务已完成，但交付物不易查找

阅读 run 结束时的摘要并检查研究工作区。最终交付物通常位于 `<workspace>/output_synthesis/final_delivery/` 下，也可能包含交付说明 `README.md`；实际目录和文件名由模型决定。应以本次 run 生成的说明和文件为准，不要假定路径固定。`run status` 中的 `Run directory` 指向托管运行记录，不一定是研究交付物目录。

`State: completed` 只表示执行结束，不代表科研结果的质量或完整性已经得到保证。请对照实际文件、报告中的局限和任务要求检查结果。已停止或失败的 run 可能留下中间产物。

## 帮助中出现澄清或权限命令

当前 CLI 仍显示用于处理待定交互的兼容命令。它们不是正常无人值守 Beta 工作流的必需步骤，也不会开启受支持的交互式权限模式。没有对应的待处理交互时，运行这些命令不会创建交互。详见 [CLI 参考](user/cli-reference.zh-CN.md#帮助中仍显示的兼容命令)。如需停止并稍后恢复任务，请使用 `run stop`。

## 报告问题

通过 GitHub Issues 提供 `ownpi info` 显示的 OwnPI 版本、操作系统、执行命令、任务状态和复现步骤。可从 `ownpi run status <run_id>` 的 Run directory 字段找到托管 run 目录。若存在诊断日志，路径为 `<run_dir>/diagnostics/runtime.jsonl`，可附上相关片段。分享前检查日志并删除 API key、私有 URL、数据和路径。
