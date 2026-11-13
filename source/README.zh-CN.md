# OwnPI 源码包

**中文** | [English](README.md)

推荐从 [OwnPI 产品仓库](https://github.com/yzz673/OwnPI)安装 release 运行 OwnPI。源码包仅提供阅读、修改和直接运行源码的可选方式。用户须自行准备依赖；包内不提供依赖环境、自动准备脚本或 release 打包工具。

## 源码结构

以下路径均相对于解压后的包根目录。

| 路径 | 内容 |
| --- | --- |
| `src/ownpi/cli/`、`src/ownpi/chat/` | CLI 入口与交互 session 接入。 |
| `src/ownpi/ui/`、`src/ownpi/tui_bridge/` | Rust 终端客户端及 Python Runtime bridge。 |
| `src/ownpi/controller/`、`src/ownpi/task_authoring/` | Workflow 编排、critic session 及 Research 任务准备。 |
| `src/ownpi/runtime/` | 原生 model/tool 执行、prompt 装配、历史、恢复、知识访问及执行边界。 |
| `src/ownpi/llm/`、`src/ownpi/subagent/`、`src/ownpi/tools/` | Provider 通信、任务委派及工具实现。 |
| `src/ownpi/static_semantic_contracts/content/` | 角色与阶段指令。 |
| `src/ownpi/skills/bundled/content/` | Skill 指令、脚本、模板及参考资产。 |
| `src/ownpi/resources/`、`src/ownpi/config/`、`src/ownpi/product/` | 随包资源、配置及产品生命周期代码。 |
| `pyproject.toml`、`setup.py`、`uv.lock` | Python 依赖声明、editable 安装元数据及依赖锁定。 |

源码包不包含内部计划、developer rerun 与观测工具，也不包含完整 Python 测试套件。Rust 测试及 fixture 随 TUI 源码保留。Skills 中的非代码资源用于 runtime，不是生成的科研产物。

## 需要自行准备的依赖

- **Python：**自行管理的 Python 3.11 或更高版本环境，以及 `pip`、`setuptools>=68`、`wheel` 和 `pyproject.toml` 中 `project.dependencies` 列出的全部依赖。它们须安装在运行 OwnPI 的同一个环境中。`uv.lock` 记录解析后的依赖版本；下面的 editable 安装命令不会自动应用该锁文件。
- **仅 TUI 需要：**arm64 或 x86_64 的 macOS，或基于 glibc 的 Linux 原生主机；兼容的 Rust 工具链，包括 `cargo`、`rustc`，以及 crate 所需的主机编译器、链接器和开发库。离线编译前，须自行将 `src/ownpi/ui/Cargo.lock` 中的依赖准备到 Cargo 缓存。仅使用 Python CLI 不需要编译 TUI。
- **任务相关工具：**自行安装具体任务需要的外部程序、可选 Python 依赖、浏览器资源、科学计算环境和数据集。例如，编译论文需要合适的 TeX 环境。可选 Python 依赖见 `project.optional-dependencies`；其外部依赖及可选 Pack 资产也不随包提供。
- **模型访问：**执行需要模型的任务前，按照产品文档配置支持的 provider 和凭据。包内不附带凭据。

以下命令以依赖已经就绪为前提，不替用户下载或安装依赖。不要求预先拥有内部开发安装或已安装的 OwnPI release。

## 直接运行源码

激活已经准备好的 Python 环境，在解压后的包根目录执行：

```bash
python -m pip install --no-deps --no-build-isolation -e .
python -m pip check
ownpi --help
```

Editable 安装注册 CLI/TUI 命令，但 Python 代码直接从此目录加载，因此需要保留该目录。常规 Python 修改不需要重建 release；依赖或入口元数据变化时，需要更新环境或重新执行安装。

需要 TUI 时，在已经准备好的 Rust 环境中编译原生客户端。使用当前主机目标，不设置交叉编译覆盖项：

```bash
cargo build --manifest-path src/ownpi/ui/Cargo.toml --target-dir src/ownpi/ui/target --release --locked --offline
ownpi-tui --help
```

Launcher 可以直接加载 `src/ownpi/ui/target/release/ownpi-tui-bin`。修改 Rust 源码后需要重新编译该 binary，不需要制作 release package。运行随包 Rust 测试时，将同一组 Cargo 参数中的 `build` 改为 `test`。

检查 Python 代码和 TUI binary 的实际加载位置：

```bash
python -c 'import ownpi; print(ownpi.__file__)'
python -c 'from ownpi.tui_bridge.binary import binary_path; print(binary_path())'
```

路径应指向这份解压后的源码树；第二项检查需要已经编译 TUI。帮助和路径检查不发起模型请求，也不验证 provider 连通性。配置和任务命令见产品文档。

## 发行边界

这不是托管 Base 发行包，不附带便携 Python、LaTeX、科学计算环境、可选 Neuroscience Pack 资产或 Base/Pack 构建发布系统。`src/ownpi/product/` 仍需保留，因为其中包含运行所需的配置、诊断、资源读取和 Pack 管理代码，并非只有 release 打包功能。

源码运行使用自行管理的 Python 环境，不建议覆盖托管 release 安装。此归档不是 `ownpi update` 的更新包；托管安装与升级使用产品发行包。

## 许可证

Copyright (c) 2026 OwnPI contributors. 除另有声明外，OwnPI 原创代码和文档采用 [Apache-2.0](LICENSE)。第三方代码、论文、模板等参考资产仍适用各自条款，项目许可证不改变其授权。见[第三方声明](THIRD_PARTY_NOTICES.md)及相关材料随附的声明。
