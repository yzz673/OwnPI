<div align="center">
  <img src="docs/assets/readme/ownpi-lockup.png" alt="OwnPI：Your Own Principal Investigator" width="720">
  <p>
    <a href="https://github.com/yzz673/OwnPI/stargazers"><img src="https://img.shields.io/github/stars/yzz673/OwnPI?style=flat-square" alt="GitHub Stars"></a>
    <a href="https://github.com/yzz673/OwnPI/fork"><img src="https://img.shields.io/github/forks/yzz673/OwnPI?style=flat-square" alt="GitHub Forks"></a>
    <a href="https://github.com/yzz673/OwnPI/commits"><img src="https://img.shields.io/github/last-commit/yzz673/OwnPI?style=flat-square" alt="Last commit"></a>
    <a href="https://galaxy-dawn.github.io/neuroagent-website/?lang=zh"><img src="https://img.shields.io/badge/Website-Visit-0ea5e9?style=flat-square&amp;logo=safari&amp;logoColor=white" alt="OwnPI 网站"></a>
    <a href="https://github.com/yzz673/OwnPI/pulls"><img src="https://img.shields.io/badge/PRs-welcome-2ea44f?style=flat-square&amp;logo=github&amp;logoColor=white" alt="欢迎提交 Pull Request"></a>
  </p>

  <strong>Language</strong>: <a href="README.md">English</a> | <strong>中文</strong>
  <p><strong>Supported Platforms</strong>: macOS | Linux</p>

</div>

> **Beta 版本提示：** 当前版本用于体验完整的研究工作流，尚不应视为生产级稳定版本，仍可能存在兼容性问题或未发现的缺陷。Beta 通过 CLI 运行工作流，并以无人值守的 `full access` 权限执行；请只在专用工作区中运行，不要暴露敏感文件。Beta 暂不发布 TUI，也不提供交互式权限审批；这些能力计划在 Stable 版本中正式提供。欢迎通过 [GitHub Issues](https://github.com/yzz673/OwnPI/issues) 反馈问题，也欢迎社区通过 [Pull Requests](https://github.com/yzz673/OwnPI/pulls) 改进功能、文档和稳定性。

> **Beta 范围：** 当前版本仅支持通过 CLI 运行 Research Level workflow，不提供 Task Level。用户安装发布包，不需要运行源码仓库；默认 app home 为 `~/.ownpi`。Beta 使用无人值守的 `full_access` 执行，请只在专用、隔离的 workspace 中运行，不要暴露敏感文件。

# OwnPI

OwnPI，你的专属首席研究员。用户给出研究目标和可用资源后，OwnPI 会根据任务需要推进文献调研、问题界定、方法与实验设计、实现执行、结果分析和成果整理，并动态调配合适的工具、论文知识与科研技能。当阶段产出未达到要求时，它会回到相应环节迭代修正；同时持续记录来源、关键决策、运行状态与交付物，使研究过程与成果可检查、可追溯，并有证据支撑。

OwnPI 当前主要面向神经科学研究，尤其是脑信号、神经数据和神经解码。OwnPI Core 提供通用研究工作流；可选的 Neuroscience Pack 为 Core 增加神经科学专用工具、少量相关 skills 和论文知识。Core 也可以扩展到其他研究领域；Neuroscience Pack 是对 Core 的专业化扩展，不会替代 Core。

[![OwnPI 后续 Stable TUI 的 30 秒演示：任务、技能装配、并行协作与分析迭代](docs/assets/readme/terminal-demo-30s-10shots.gif)](https://galaxy-dawn.github.io/neuroagent-website/?lang=zh#heroTerminalDemo)

*这段 30 秒剪辑包含 10 个镜头，每个 3 秒，展示 Task 模式下的研究任务、技能装配、并行协作与分析迭代。这是后续 Stable TUI 的预览；当前公开 Beta 使用命令行工作流。*

## 快速导航

| 部分 | 作用 |
| --- | --- |
| [为什么你需要 OwnPI](#为什么你需要-ownpi) | 判断什么时候应该继续用现有框架，什么时候选择 OwnPI。 |
| [核心工作流](#核心工作流) | 查看任务路由、迭代评审、论文知识与技能装配。 |
| [快速开始](#快速开始) | 安装 Beta 发布包并启动第一个命令行任务。 |
| [产出示例](#产出示例) | 打开完整论文，并查看其结论所依据的证据。 |
| [Stable 路线图](#stable-路线图) | 查看计划在 Stable 版本中提供的能力。 |
| [文档导航](#文档导航) | 查找安装和使用 OwnPI 所需的用户文档。 |
| [反馈](#反馈) | 报告使用问题或提出改进建议。 |
| [致谢](#致谢) | 了解支持 OwnPI 的项目与贡献者。 |
| [参考资料](#参考资料) | 追溯 OwnPI 使用的上游项目。 |

## 为什么你需要 OwnPI

常见的自动化科研框架擅长把一个明确任务沿预设流程跑到底：搜集资料并生成报告、把想法推进成论文，或围绕单一指标反复实验。目标和路线都稳定时，它们已经够用。

真正的研究很少沿着一条直线推进。研究方案往往需要反复优化，甚至推倒重来；一次实验失败，也可能让研究者重新检查代码实现或方法设计。任务还可能因等待人工判断、权限确认，或模型与环境故障而中断。如果系统只保存最终答案，恢复工作时就只能重新猜测此前进行到了哪一步。

OwnPI 把研究当成一个可以长期推进、回看和恢复的项目，而不是一次从提示词到答案的任务：

- 按任务只启用需要的阶段，不强制跑完整套流程。
- 发现问题时回到真正需要修正的上游阶段，而不是只重试最后一步。
- 保存关键决策、工具调用、权限、进度和产物，中断后可以检查并继续。
- 在澄清、授权和评审节点把决定权留给研究者。

## 核心工作流

### 评审驱动迭代

[![七阶段研究流程动画](docs/assets/readme/hd-workflow.zh.webp)](docs/assets/readme/hd-workflow.zh.webp)

OwnPI 把研究组织成可以回头修正的循环，而不是只能向前推进的流水线。研究流程包含七个阶段：文献与背景、研究问题界定、方法设计、实验设计、实现与执行、分析与评审、产物打包与论文撰写。当分析与评审发现问题时，系统会按需返回研究问题界定、方法设计、实验设计或实现与执行阶段；通过评审后，再整理论文、代码、图表与可复现研究产物。每次运行启用任务需要的阶段。评审与返回修改的次数由 review profile 决定；rigorous 不限制评审轮数，exhaustive 还取消 Analysis 返回修改的次数上限。

### 论文知识支持

[![网站原生动画：从论文证据提炼方法知识与研究策略](docs/assets/readme/hd-knowledge.zh.webp)](docs/assets/readme/hd-knowledge.zh.webp)

OwnPI 将同一批论文证据提炼成两类互补的可复用支持：通过归类相似案例、归纳共性规律形成方法知识，通过比较论文框架和研究思路形成研究策略。两类知识都会保留可检索、可展开、可追溯的依据，并在任务开始前整理和发布。运行时 OwnPI 只按需读取相关内容，既不用临时重建知识库，也不会把已有知识当作当前结论的直接证据。

### 科研技能动态装配

[![网站原生动画：根据研究任务选择技能并装配本轮方法包](docs/assets/readme/hd-skills.zh.webp)](docs/assets/readme/hd-skills.zh.webp)

OwnPI 在 [Claude Scholar](https://github.com/Galaxy-Dawn/claude-scholar) 的基础上，把多项成熟的科研技能直接作为内置能力提供。用户安装 OwnPI 后，无需再逐项寻找、安装和配置这些技能，打开就能使用。面对不同任务，OwnPI 只选择与当前工作有关的技能，需要时再读取详细说明，让每项研究获得合适的支持。

这是 Core 的通用能力。Neuroscience Pack 只增加少量面向脑信号和神经数据工作的专用 skills，并不是一套替代 Core 的大型神经科学技能集合。

## 快速开始

### 安装和配置

当前 Beta 提供 Apple Silicon macOS（arm64）和 Linux x86_64 安装包，托管于 GitHub Releases。安装发布包：

```bash
curl -fsSL https://raw.githubusercontent.com/yzz673/OwnPI/main/install.sh | bash
```

安装器会将应用目录设为 `~/.ownpi`，并将命令安装到 `~/.local/bin/ownpi`。
如果终端找不到 `ownpi`，将命令目录加入当前终端的 `PATH`：

```bash
export PATH="$HOME/.local/bin:$PATH"
```

配置模型访问并检查安装：

```bash
ownpi setup
ownpi doctor
```

- `setup` 会询问服务类型、API key、Base URL、模型名称、适用的 reasoning effort 和 review 设置，并将配置写入应用目录。支持 OpenAI-compatible 与 Anthropic-compatible 服务。
- `init` 为可选命令：安装器和 setup 已经会初始化应用目录。
- `doctor` 检查安装、配置、凭据和可选能力；检查成功不代表已经真实调用模型。

如需使用其他应用目录，在安装前设置 `OWNPI_HOME`，并在之后的命令中保持该值不变。详见[配置](docs/user/configuration.zh-CN.md)。

### 可选 Neuroscience Pack

OwnPI Core 无需 Pack 也可以使用。对于神经科学、脑信号或神经数据研究，可在完成基础安装后安装 Pack：

```bash
ownpi pack install neuroscience
```

Neuroscience Pack 提供 Brainlib、神经科学专用 skills、托管工具入口和基于论文的研究知识。可检查安装状态：

```bash
ownpi pack status neuroscience
ownpi tools doctor brainlib
```

### 描述研究任务

向 OwnPI 提供研究简报，说明你计划开展的项目。内容应包括研究问题或方向、预期贡献、带具体路径的可用资源、实际限制和预期交付物。无需预先完成实验方案；请指出 OwnPI 应调查和制定哪些决策。

创建 UTF-8 文件 `task.md`，可从以下模板开始并填写所有方括号内容。任务描述模板保留英文：

```text
Treat this as a serious research project in [research area].
The central research question or direction is: [what do you want to understand, explain, or establish?]
The intended contribution is: [what new method, framework, explanation, or evidence should the study produce?]
Available resources: [datasets, code, papers, tools, Python environments, or other materials, with concrete paths].
Constraints: [available compute, time or cost limits, data restrictions, and other requirements].
Expected deliverables: [manuscript, supplementary materials, source data, source code, or other outputs].
```

### 启动并管理 run

在专用项目工作区启动任务：

```bash
ownpi run start --message-file /path/to/task.md --workspace /path/to/your/project
```

省略 `--workspace` 时使用当前目录。工作区保存研究文件和产物；`~/.ownpi` 保存配置和托管 run 记录。Beta 通过 CLI 运行 Research Level workflow，并根据任务选择所需阶段。

`start` 和 `resume` 默认在前台运行；添加 `--background` 可在任务继续运行时返回终端。用 `run list` 查找 run ID，再查看或控制任务：

```bash
ownpi run list
ownpi run status <run_id>
ownpi run stop <run_id>
ownpi run resume <run_id>
ownpi run resume <run_id> --background
```

前台运行时，在同一个终端按 **Ctrl-C** 停止。Ctrl-C 和 `run stop` 都会取消当前工作；清理可能需要几秒。用户主动停止或进程意外退出后，`resume` 会从已保存记录继续，并可能重做未完成的工作。不同终端应使用相同的应用目录。详见[快速开始](docs/user/getting-started.zh-CN.md)和 [CLI 参考](docs/user/cli-reference.zh-CN.md)。

## 产出示例

这篇 42 页论文 *Non-invasive brain recordings retain acoustic and linguistic information with distinct temporal and sensor profiles* 是 OwnPI 自主科研产出的一个端到端研究示例。它研究哪些声学和语言描述能够预测非侵入式脑响应，以及相关信息如何在时间和传感器维度上表现出来。研究发现，非侵入式脑记录保留了声学、语音形式、词汇和上下文等多个层面的信息，词语引发的脑响应也随其在前文语境中的可预测性而变化。这些信息在时间和传感器维度上既有共性也有差异：它们呈现不同的时间响应特征，而在传感器上的分布则可以具有可重复性并相互重叠。

**完整论文：** [阅读或下载 42 页 PDF](docs/assets/readme/output-example-paper.pdf)。

<table>
  <tr>
    <td align="center" valign="top" width="25%"><a href="docs/assets/readme/output-example-paper-page-1.png"><img src="docs/assets/readme/output-example-paper-page-1.png" alt="第 1 页：示例论文标题与摘要" width="220"></a></td>
    <td align="center" valign="top" width="25%"><a href="docs/assets/readme/output-example-paper-page-11.png"><img src="docs/assets/readme/output-example-paper-page-11.png" alt="第 11 页：声学与语言描述的条件预测贡献及短语差异" width="220"></a></td>
    <td align="center" valign="top" width="25%"><a href="docs/assets/readme/output-example-paper-page-18.png"><img src="docs/assets/readme/output-example-paper-page-18.png" alt="第 18 页：传感器响应分布的相似性、可重复性与受控头皮响应对比" width="220"></a></td>
    <td align="center" valign="top" width="25%"><a href="docs/assets/readme/output-example-paper-page-21.png"><img src="docs/assets/readme/output-example-paper-page-21.png" alt="第 21 页：时间与传感器的联合组织及校准" width="220"></a></td>
  </tr>
</table>

> **模型：** gpt-6.1-sol，high reasoning effort
>
> **Provider 调用次数：** 40,472（含 947 次上下文压缩）
>
> **成功的工具调用次数：** 69,993

## Stable 路线图

以下能力计划在 Stable 版本中提供，当前 Beta 尚不包含。

### TUI

统一的 Stable Base 同时包含 `ownpi`（CLI）和 `ownpi-tui`（终端界面），共用配置、凭据和运行记录。执行一次 `ownpi setup` 后即可用 `ownpi-tui` 启动。当前已发布 Beta 在 Stable 发布前仍为 CLI-only；发布后默认 Beta 安装可用 `ownpi update` 原地升级，无需卸载或清空数据。工作区和恢复参数见 [终端界面](docs/user/tui.zh-CN.md)。

[![OwnPI 后续 Stable TUI 的 30 秒演示：任务、技能装配、并行协作与分析迭代](docs/assets/readme/terminal-demo-30s-10shots.gif)](https://galaxy-dawn.github.io/neuroagent-website/?lang=zh#heroTerminalDemo)

### 自适应任务编排

[![网站原生动画：自适应运行路由与研究深度](docs/assets/readme/hd-routing.zh.webp)](docs/assets/readme/hd-routing.zh.webp)

Stable 版本将支持跨任务深度和工作流模式的自适应路由。当前 Beta 通过 CLI 运行 Research Level workflow。

### 更安全的权限模式

Stable 版本将提供更细粒度的权限配置和交互式审批控制，让用户选择任务运行期间如何授予文件、网络和执行权限。

### 源码开放

Stable 版本将与可安装产品一同发布 OwnPI 源码。

## 文档导航

- [快速开始](docs/user/getting-started.zh-CN.md)：安装、配置模型、首次任务和更新。
- [配置](docs/user/configuration.zh-CN.md)：模型访问、review profile 和 subagent。
- [CLI 参考](docs/user/cli-reference.zh-CN.md)：任务管理和可选 Pack 命令。
- [故障排查](docs/troubleshooting.zh-CN.md)：安装、模型访问和任务恢复。
- [第三方声明](THIRD_PARTY_NOTICES.md)。

## 使用许可

OwnPI Beta 采用 [OwnPI Beta Evaluation License](LICENSE)，允许安装和研究试用。
OwnPI 自有产品组件的再分发、出售和改作需要另行授权；第三方组件继续适用各自许可。
详见[使用许可说明](docs/licensing.md)。

## 反馈

Beta 以可安装产品而非源码版本发布。请通过 [GitHub Issues](https://github.com/yzz673/OwnPI/issues) 报告安装问题、provider 故障、运行中断、意外产出或文档问题，并附上 OwnPI 版本、操作系统、命令、相关 run 状态和复现步骤。不要提供 API key、私有数据或敏感工作区内容。

## 致谢

OwnPI 使用了 Claude Scholar 中形成的科研技能，包括面向 Nature 论文的写作、润色、审稿意见回复和数据可用性技能；同时将 Brainlib 作为神经科学托管工具打包，并借鉴了 OpenAI Codex 中特定的权限与沙箱编排设计。感谢这些项目的维护者与贡献者，以及 OwnPI 所依赖的开源生态。

## 参考资料

- [Claude Scholar](https://github.com/Galaxy-Dawn/claude-scholar)：为 OwnPI 的内置科研技能提供基础。
- [Brainlib](https://github.com/Galaxy-Dawn/brain-plug-and-play-library)：作为 OwnPI 托管的神经科学数据与建模工具集成。
- [OpenAI Codex](https://github.com/openai/codex)：OwnPI 权限与沙箱编排设计的改编来源；固定提交和源文件路径记录在[第三方声明](THIRD_PARTY_NOTICES.md)中。
