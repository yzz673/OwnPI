# Upstream Provenance

- Source: `~/.codex/skills/doc/`
- Migrated: 2026-08-03
- License: Apache-2.0, retained as `LICENSE.txt`
- Exclusions: none; Codex UI metadata/assets are retained for provenance but are not runtime authority

## SHA-256 Manifest

- `SKILL.md`: `0a635585817d1ac96e8e512114d08258b70f9e54ce0052078a258291ac13a8ed`
- `LICENSE.txt`: `4dd13869245e356246a5b770723247bbb80a8f07a181d1d3d873a1734297cdb9`
- `agents/openai.yaml`: `b77d452b23dfa0bef7788728317254f07bddcd5f2be1d6678d5c3cf754b833ae`
- `assets/doc.png`: `7eadf1088e5062f681dba6596173a1df13bbfbcddd8a8a9bb78fd2071947dcf8`
- `assets/doc-small.svg`: `bd5415fbbfe72c66c1527411d61625731fdd2f75b25d1542510b6ddef699ebe0`
- `scripts/render_docx.py`: `52b3c3158913ebcad2ada7b8193970b70453239e255054aa2d60c1683af08751`

## Adapter Patch

- Replaced `tmp/docs/` with the project convention `temp/docs/` in `SKILL.md`.
- Added an explicit check that DOCX tooling is an on-demand capability rather than a
  default OwnPI Dependency Bundle dependency.
- Updated the frontmatter description and dependency section to match that capability
  boundary.
- Every bundled resource other than the adapted `SKILL.md` is byte-identical to upstream.
