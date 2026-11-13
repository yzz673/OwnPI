# Upstream Provenance

- Source: `~/.codex/skills/webapp-testing/`
- Migrated: 2026-08-03
- License: Apache-2.0, retained as `LICENSE.txt`
- Exclusions: none

## SHA-256 Manifest

- `SKILL.md`: `e01a5c5567dde91929420fd6e973e96c0b9db1181a91526c88f7a0c236223126`
- `LICENSE.txt`: `58d1e17ffe5109a7ae296caafcadfdbe6a7d176f0bc4ab01e12a689b0499d8bd`
- `scripts/with_server.py`: `b0dcf4918935b795f4eda9821579b9902119235ff4447f687a30286e7d0925fd`
- `examples/console_logging.py`: `ea46877289acb82da7e7ce59d0bc37c8977cd57e2a006d0c88d7a1c625bf95da`
- `examples/element_discovery.py`: `d63c89604a22f8845d724e95dda45db49b1bf57c25ce0a83afbb7b8da3d402f0`
- `examples/static_html_automation.py`: `9d533aafb875ee3ab8b8ebf8f5b9003ac8d999da3d09b285cce252e623140064`

## Adapter Patch

- `SKILL.md`, helper script, examples, and license are byte-identical to upstream.
- The canonical Skill resource-root materialization makes relative bundled paths readable after invocation.
- The registration wrapper supplies no browser, job-control, filesystem, or shell Tool grants.
