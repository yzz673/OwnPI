# Terminal UI

The unified Stable Base contains Core, CLI and native TUI. Users do not need
Rust, Cargo, system Python or a source checkout. CLI and TUI use the same APP
home, credentials and persisted runs; they are independent interfaces to Core.

```bash
ownpi setup
ownpi-tui --workspace ./workspace
ownpi-tui --workspace ./workspace --resume RUN_ID
```

Setup is needed only for an unconfigured APP home. `--config` and `--app-home`
select explicit configuration and data paths. Stop a running task before
resuming it in another interface; two interfaces must not own the same run.

After Stable is published, existing Beta installations on the default `stable`
channel upgrade in place:

```bash
ownpi update
```

Do not uninstall Beta or remove its data. Updates preserve configuration,
credentials, sessions, memory and workspaces; installation failure restores the
previous version and entry points. For an old custom bin directory, set
`OWNPI_BIN_DIR` to that directory for the first upgrade. New wrappers remember
the directory. A custom `beta` channel subscription is not switched automatically.
The Beta product name does not imply a `beta` update channel.

This documents the unified package; it does not announce that Stable has been
published or change the current download channel.
