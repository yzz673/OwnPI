# Upstream Provenance

- Source: `~/.codex/skills/pdf/`
- Migrated: 2026-08-03
- License: Apache-2.0, retained as `LICENSE.txt`
- Exclusions: none; Codex UI metadata/assets are retained for provenance but are not runtime authority

## SHA-256 Manifest

- `SKILL.md`: `d108cf2b36355ab37eb5962933f4d09785ec002f3105c506129320209306b9d2`
- `LICENSE.txt`: `4dd13869245e356246a5b770723247bbb80a8f07a181d1d3d873a1734297cdb9`
- `agents/openai.yaml`: `f8b223e25343559f57e1839ccd849fd1494a9729da33da04bb5074b7f80f4887`
- `assets/pdf.png`: `17da356d195d4e3fa24f9becbee6b8d9e60e13005762b5ab0809c520c76da6ee`

## Adapter Patch

- Replaced `tmp/pdfs/` with the project convention `temp/pdfs/` in `SKILL.md`.
- Updated PDF creation and rendering guidance to use the managed LaTeX and Python
  capabilities provided by the OwnPI Dependency Bundle.
- Removed the default skill's install commands because dependency installation is
  managed by OwnPI's bundle layer.
- Every bundled resource other than the adapted `SKILL.md` is byte-identical to upstream.
