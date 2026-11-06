# 终端界面

统一的 Stable Base 同时包含 Core、CLI 和原生 TUI。用户无需 Rust、Cargo、系统 Python 或源码。两个入口共用 APP home、凭据和运行记录，分别面向命令行和交互式终端操作。

```bash
ownpi setup
ownpi-tui --workspace ./workspace
ownpi-tui --workspace ./workspace --resume RUN_ID
```

已有配置无需重复 setup。`--config` 和 `--app-home` 可指定配置和数据目录。切换入口恢复前先停止正在运行的任务，避免两个入口同时执行同一个 run。

Stable 发布后，默认订阅 `stable` 频道的 Beta 安装可以直接原地升级：

```bash
ownpi update
```

无需卸载 Beta 或删除数据。升级保留配置、凭据、sessions、memory 和 workspace；安装失败恢复旧版本及原入口。旧安装使用自定义命令目录时，首次升级需将 `OWNPI_BIN_DIR` 设置为原目录，新 wrapper 会自动保留该设置。自定义 `beta` 频道不会自动切换；产品名称 Beta 不等于更新频道 beta。

本文描述统一包，不代表 Stable 已发布，也不修改当前下载频道。
