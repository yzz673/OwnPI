# Third-Party Notices

## OpenAI Codex

The permission profile, filesystem policy, approval preset, and sandbox orchestration design in `src/ownpi/runtime/permissions/` is adapted from OpenAI Codex at commit `61a44880a85d2fd0d8770908dea5733495e571c8`.

Upstream sources include:

- `codex-rs/protocol/src/models.rs`
- `codex-rs/protocol/src/permissions.rs`
- `codex-rs/protocol/src/protocol.rs`
- `codex-rs/utils/approval-presets/src/lib.rs`
- `codex-rs/core/src/tools/orchestrator.rs`
- `codex-rs/core/src/tools/sandboxing.rs`

OpenAI Codex is licensed under the Apache License, Version 2.0. Its license is
available in the [upstream source](https://github.com/openai/codex/blob/61a44880a85d2fd0d8770908dea5733495e571c8/LICENSE)
and at <https://www.apache.org/licenses/LICENSE-2.0>.
