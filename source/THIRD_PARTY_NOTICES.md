# Third-Party Notices

The project [Apache-2.0 license](LICENSE) applies to OwnPI-authored materials
unless separately identified. Third-party code, dependencies, papers, templates,
and other reference assets retain their own licenses and attribution notices.
This file is not a blanket redistribution grant for those materials.

## OpenAI Codex

The permission profile, filesystem policy, approval preset, and sandbox orchestration design in
`src/ownpi/runtime/permissions/` is adapted from OpenAI Codex at commit
`61a44880a85d2fd0d8770908dea5733495e571c8`.

Upstream sources include:

- `codex-rs/protocol/src/models.rs`
- `codex-rs/protocol/src/permissions.rs`
- `codex-rs/protocol/src/protocol.rs`
- `codex-rs/utils/approval-presets/src/lib.rs`
- `codex-rs/core/src/tools/orchestrator.rs`
- `codex-rs/core/src/tools/sandboxing.rs`

OpenAI Codex is licensed under the Apache License, Version 2.0. Its license is
available in the [upstream source](https://github.com/openai/codex/blob/61a44880a85d2fd0d8770908dea5733495e571c8/LICENSE)
and at <https://www.apache.org/licenses/LICENSE-2.0>; the license text is also
included in [LICENSE](LICENSE).

The upstream [NOTICE](https://github.com/openai/codex/blob/61a44880a85d2fd0d8770908dea5733495e571c8/NOTICE)
contains the following attributions, retained here as upstream notices:

> OpenAI Codex
> Copyright 2025 OpenAI
>
> This project includes code derived from [Ratatui](https://github.com/ratatui/ratatui), licensed under the MIT license.
> Copyright (c) 2016-2022 Florian Dehau
> Copyright (c) 2023-2025 The Ratatui Developers
