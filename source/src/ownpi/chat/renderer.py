from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from ownpi import __version__
from ownpi.chat.theme import (
    UISemanticTokens,
    build_rich_panel_theme,
    get_ui_tokens,
    resolve_theme_setting,
)
from ownpi.schemas.artifacts import FinalPackage
from ownpi.schemas.runtime import LLMConfig, UserConfig
from ownpi.schemas.workflow_plan import resolve_runtime_profile
from ownpi.utils.ansi import style_text


_IMPLICIT_REASONING_DEFAULTS = {
    "openai": "medium",
    "openai_compatible": "medium",
}


def _implicit_reasoning_effort(provider: str) -> str | None:
    return _IMPLICIT_REASONING_DEFAULTS.get(provider)


def _display_workspace_path(raw_path: str | None) -> str:
    if not raw_path:
        return "."
    path = Path(raw_path).expanduser()
    try:
        resolved = path.resolve()
        home = Path.home().resolve()
        if resolved == home:
            return "~"
        if home in resolved.parents:
            return f"~/{resolved.relative_to(home)}"
        return str(resolved)
    except OSError:
        return str(path)


def _effective_reasoning_display(
    *,
    config: LLMConfig,
    override: str | None = None,
    override_applies: bool = False,
) -> tuple[str, str]:
    if override and override_applies:
        return override, "session-override"
    if config.reasoning_effort:
        return config.reasoning_effort, "profile-default"
    implicit = _implicit_reasoning_effort(config.provider)
    if implicit is not None:
        return implicit, "implicit-default"
    return "<unset>", "provider-default"


def _renderer_tokens(
    *,
    theme_setting: str = 'auto',
    use_terminal_background: bool = True,
) -> UISemanticTokens:
    return get_ui_tokens(
        resolve_theme_setting(theme_setting),
        use_terminal_background=use_terminal_background,
    )


def _semantic_text(
    text: str,
    *,
    role: str,
    bold: bool = False,
    tokens: UISemanticTokens,
) -> str:
    color = _semantic_color(role, tokens=tokens)
    return style_text(text, color=color, bold=bold)


def _semantic_color(role: str, *, tokens: UISemanticTokens) -> str:
    mapping = {
        'success': tokens.success,
        'warning': tokens.warning,
        'info': tokens.focus,
        'accent': tokens.accent,
        'title': tokens.text_title,
        'muted': tokens.text_muted,
        'primary': tokens.text,
    }
    return mapping.get(role, tokens.text)


def _format_human_clarification_target(
    session: dict[str, object],
    *,
    include_round: bool = False,
) -> str:
    continuation_kind = str(session.get("continuation_kind", "stage_attempt_reproduce") or "stage_attempt_reproduce")
    if continuation_kind == "direct_runtime":
        return "direct_runtime"
    stage = str(session.get("stage", "<unknown>") or "<unknown>")
    attempt = int(session.get("attempt", 0) or 0)
    continuation_index = int(session.get("continuation_index", 0) or 0)
    if include_round and continuation_index > 0:
        return f"{stage} a{attempt} continuation={continuation_index}"
    return f"{stage} a{attempt}"


def _format_human_clarification_session_id(session: dict[str, object]) -> str:
    display = str(session.get("display_session_id", "") or "").strip()
    if display:
        return display
    session_id = str(session.get("session_id", "<unknown>") or "<unknown>")
    continuation_kind = str(session.get("continuation_kind", "stage_attempt_reproduce") or "stage_attempt_reproduce")
    if continuation_kind == "direct_runtime":
        suffix = session_id.rsplit("-", 1)[-1] if "-" in session_id else session_id
        return f"direct-runtime-{suffix}" if suffix else "direct-runtime"
    return session_id


def _run_outcome_mapping(payload: object) -> dict[str, object] | None:
    return dict(payload) if isinstance(payload, dict) else None


def _append_run_outcome_lines(
    lines: list[str],
    run_outcome: dict[str, object] | None,
    *,
    markdown: bool,
    tokens: UISemanticTokens | None = None,
) -> None:
    if not isinstance(run_outcome, dict) or not run_outcome:
        return
    heading = "## Delivery outcome" if markdown else _semantic_text("Delivery outcome:", role='title', bold=True, tokens=tokens or _renderer_tokens())
    lines.extend(["", heading])
    prefix = "- " if markdown else "  - "
    label = _markdown_label if markdown else _plain_label
    outcome_label = 'Runtime outcome' if markdown else 'Runtime outcome'
    delivery_status = str(run_outcome.get('delivery_status') or "").strip()
    if delivery_status:
        lines.append(f"{prefix}{label('Delivery status')} {delivery_status}")
    delivery_class = str(run_outcome.get('delivery_class') or "").strip()
    if delivery_class:
        lines.append(f"{prefix}{label('Delivery class')} {delivery_class}")
    lines.append(
        f"{prefix}{label('Runtime delivery success')} "
        f"{run_outcome.get('runtime_delivery_success', run_outcome.get('task_success', False))}"
    )
    lines.append(f"{prefix}{label(outcome_label)} {run_outcome.get('outcome', '<unknown>')}")
    lines.append(f"{prefix}{label('Outcome class')} {run_outcome.get('outcome_class', '<unknown>')}")
    blocking_stage = str(run_outcome.get('blocking_stage') or "").strip()
    if blocking_stage:
        lines.append(f"{prefix}{label('Blocking stage')} {blocking_stage}")
    blocking_run_status = str(run_outcome.get('blocking_run_status') or "").strip()
    if blocking_run_status:
        lines.append(f"{prefix}{label('Blocking run status')} {blocking_run_status}")
    package_quality = str(run_outcome.get('package_quality') or "").strip()
    if package_quality:
        lines.append(f"{prefix}{label('Package quality')} {package_quality}")
    claim_boundary = str(run_outcome.get('claim_boundary') or "").strip()
    if claim_boundary:
        lines.append(f"{prefix}{label('Claim boundary')} {claim_boundary}")
    rationale = [
        str(item).strip()
        for item in run_outcome.get('rationale', [])
        if str(item).strip()
    ]
    if rationale:
        lines.append(f"{prefix}{label('Rationale')}")
        lines.extend([f"{prefix}  - {item}" for item in rationale])


def _render_final_deliverable_lines(
    final_deliverable: dict[str, object],
    *,
    markdown: bool,
    tokens: UISemanticTokens | None = None,
) -> list[str]:
    if not isinstance(final_deliverable, dict) or not final_deliverable:
        return []
    final_text = str(final_deliverable.get('final_text') or '').strip()
    output_type = str(final_deliverable.get('output_type') or '').strip()
    target_audience = str(final_deliverable.get('target_audience') or '').strip()
    limitations = _coerce_text_list(final_deliverable.get('limitations'))
    evidence_refs = _coerce_text_list(final_deliverable.get('evidence_refs'))
    if markdown:
        lines = ["", "## Final deliverable"]
        meta_parts = [part for part in (output_type, target_audience) if part]
        if meta_parts:
            lines.append(f"- **Type/audience:** {' / '.join(meta_parts)}")
        if final_text:
            lines.extend(["", final_text])
        if limitations:
            lines.extend(["", "### Limitations", *[f"- {item}" for item in limitations[:5]]])
        if evidence_refs:
            lines.append(f"- **Evidence refs:** {', '.join(evidence_refs[:8])}")
        return lines
    header = _semantic_text("Final deliverable:", role='title', bold=True, tokens=tokens or _renderer_tokens())
    lines = [header]
    meta_parts = [part for part in (output_type, target_audience) if part]
    if meta_parts:
        lines.append(f"  - Type/audience: {' / '.join(meta_parts)}")
    if final_text:
        lines.append(final_text)
    if limitations:
        lines.append("  - Limitations:")
        lines.extend(f"    - {item}" for item in limitations[:5])
    if evidence_refs:
        lines.append(f"  - Evidence refs: {', '.join(evidence_refs[:8])}")
    return lines


def _coerce_text_list(value: object) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, tuple):
        return [str(item).strip() for item in value if str(item).strip()]
    text = str(value or '').strip()
    return [text] if text else []


def _markdown_label(text: str) -> str:
    return f"**{text}:**"


def _plain_label(text: str) -> str:
    return f"{text}:"


def _append_decision_mapping_lines(
    lines: list[str],
    *,
    final_decision: object,
    raw_final_decision: object,
    decision_mapping_applied: object,
    decision_mapping_reason: object,
    markdown: bool,
) -> None:
    raw_value = str(raw_final_decision or "").strip()
    mapping_applied = bool(decision_mapping_applied)
    mapping_reason = str(decision_mapping_reason or "").strip()
    final_value = str(final_decision or "").strip()
    if not raw_value and not mapping_applied and not mapping_reason:
        return
    prefix = "- " if markdown else "  - "
    if raw_value:
        lines.append(f"{prefix}Raw final decision: {raw_value}")
    if mapping_applied:
        lines.append(f"{prefix}Decision mapping applied: True")
    if mapping_reason:
        lines.append(f"{prefix}Decision mapping reason: {mapping_reason}")
    if raw_value and final_value and raw_value != final_value:
        lines.append(f"{prefix}Decision view: raw={raw_value} -> effective={final_value}")


def _surface_is_developer(ui_mode: str) -> bool:
    return str(ui_mode or 'developer').strip().lower() == 'developer'


def _compact_execution_summary(run_summary: dict[str, object] | None) -> dict[str, object]:
    payload = dict(run_summary) if isinstance(run_summary, dict) else {}
    compact = payload.get('compact_execution_summary', {})
    if isinstance(compact, dict) and compact:
        tools = compact.get('tools', {})
        return {
            'tool_invocation_count': int(compact.get('tool_invocation_count', 0) or 0),
            'tools': dict(tools) if isinstance(tools, dict) else {},
        }
    tool_runtime_overview = payload.get('tool_runtime_overview', {})
    tool_counts = (
        dict(tool_runtime_overview.get('top_tool_counts', {}))
        if isinstance(tool_runtime_overview, dict) and isinstance(tool_runtime_overview.get('top_tool_counts'), dict)
        else {}
    )
    return {
        'tool_invocation_count': (
            int(tool_runtime_overview.get('invocation_count', 0) or 0)
            if isinstance(tool_runtime_overview, dict)
            else 0
        ),
        'tools': tool_counts,
    }


def _render_compact_execution_summary_lines(run_summary: dict[str, object] | None) -> list[str]:
    compact = _compact_execution_summary(run_summary)
    tools = compact.get('tools', {})
    tool_invocation_count = int(compact.get('tool_invocation_count', 0) or 0)
    if not tools and tool_invocation_count <= 0:
        return []
    lines = ['', '## Execution summary']
    if isinstance(tools, dict) and tools:
        formatted_tools = ', '.join(f'{key}×{value}' for key, value in list(tools.items())[:6])
        lines.append(f'- **Tools:** {formatted_tools}')
    elif tool_invocation_count > 0:
        lines.append(f'- **Tools:** {tool_invocation_count} invocation(s)')
    return lines


def render_final_package(
    result: FinalPackage,
    *,
    theme_setting: str = 'auto',
    use_terminal_background: bool = True,
) -> str:
    tokens = _renderer_tokens(
        theme_setting=theme_setting,
        use_terminal_background=use_terminal_background,
    )
    execution_tier = str(result.run_summary.get("execution_tier", "full"))
    if result.status == "paused_for_human":
        pending = result.run_summary.get("pending_human_clarification", {}) if isinstance(result.run_summary, dict) else {}
        pending_permission = result.run_summary.get("pending_tool_permission", {}) if isinstance(result.run_summary, dict) else {}
        pause_reason = str(result.run_summary.get("pause_reason", "") or "") if isinstance(result.run_summary, dict) else ""
        lines = [
            _semantic_text("=== OwnPI Paused ===", role='warning', bold=True, tokens=tokens),
            f"{_semantic_text('Status:', role='info', bold=True, tokens=tokens)} {result.status}",
            f"{_semantic_text('Decision:', role='warning', bold=True, tokens=tokens)} {result.final_decision}",
            "",
        ]
        if isinstance(pending_permission, dict) and pending_permission and pause_reason == "tool_permission":
            waiting_for = str(
                pending_permission.get('waiting_for')
                or f"approve {pending_permission.get('tool_name', '<unknown>')}"
            )
            lines.append(
                f"Permission session: {pending_permission.get('session_id', '<unknown>')} "
                f"{pending_permission.get('stage', '<unknown>')} a{pending_permission.get('attempt', 0)} "
                f"continuation={pending_permission.get('continuation_index', 0)} "
                f"resume={pending_permission.get('continuation_kind', 'continuation_runtime')}"
            )
            lines.append(f"Waiting for: {waiting_for}")
            lines.append(
                f"Origin: {pending_permission.get('origin_kind', 'runtime')} "
                f"interaction_request_id={pending_permission.get('interaction_request_id', pending_permission.get('request_id', '<unknown>'))}"
            )
            lines.append(
                f"Tool: {pending_permission.get('tool_name', '<unknown>')} "
                f"class={pending_permission.get('permission_class', 'default')}"
            )
            reason = str(pending_permission.get('reason', '')).strip()
            if reason:
                lines.append(f"Reason: {reason}")
            human_prompt = str(pending_permission.get('human_prompt', '')).strip()
            if human_prompt:
                lines.append(f"Prompt: {human_prompt}")
            actions = pending_permission.get('available_actions', [])
            if isinstance(actions, list) and actions:
                lines.append(f"Actions: {', '.join(str(item) for item in actions)}")
            lines.extend(["", f"Artifacts saved to: {result.run_artifacts.get('run_dir', '<unknown>')}", ""])
            return "\n".join(lines)
        if isinstance(pending, dict) and pending:
            pending_label = _format_human_clarification_target(pending, include_round=True)
            lines.append("Waiting for: answer questions")
            lines.append(
                f"Session: {_format_human_clarification_session_id(pending)} "
                f"{pending_label} "
                f"resume={pending.get('continuation_kind', 'stage_attempt_reproduce')}"
            )
            lines.append(
                f"Origin: {pending.get('origin_kind', 'controller')} "
                f"interaction_request_id={pending.get('interaction_request_id', pending.get('session_id', '<unknown>'))}"
            )
            reason = str(pending.get('reason', '')).strip()
            if reason:
                lines.append(f"Reason: {reason}")
            for item in pending.get('questions', []):
                if not isinstance(item, dict):
                    continue
                lines.append(f"  - [{item.get('question_key', item.get('question_id', '?'))}] {item.get('question', '')}")
            lines.extend(
                [
                    "",
                    "Answer format: single question can be answered directly, or with question_id:/question_key:.",
                    "For multiple questions use 1:/question_id:/question_key:. Multi-select accepts comma-separated labels.",
                ]
            )
        lines.extend(["", f"Artifacts saved to: {result.run_artifacts.get('run_dir', '<unknown>')}", ""])
        return "\n".join(lines)
    if execution_tier == "direct":
        answer = str(result.run_summary.get("direct_answer") or "").strip() or (
            'Request completed; session is waiting for input.' if result.status == 'idle'
            else '_No answer generated._')
        lines = [
            _semantic_text("=== OwnPI Answer ===", role='success', bold=True, tokens=tokens),
            f"{_semantic_text('Status:', role='info', bold=True, tokens=tokens)} {result.status}",
            f"{_semantic_text('Final decision:', role='warning', bold=True, tokens=tokens)} {result.final_decision}",
            f"{_semantic_text('Execution tier:', role='accent', bold=True, tokens=tokens)} {execution_tier}",
            "",
            answer,
        ]
        _append_decision_mapping_lines(
            lines,
            final_decision=result.final_decision,
            raw_final_decision=result.raw_final_decision,
            decision_mapping_applied=result.decision_mapping_applied,
            decision_mapping_reason=result.decision_mapping_reason,
            markdown=False,
        )
        _append_run_outcome_lines(
            lines,
            _run_outcome_mapping(result.run_outcome.to_dict() if result.run_outcome is not None else None),
            markdown=False,
            tokens=tokens,
        )
        lines.extend(["", f"Artifacts saved to: {result.run_artifacts.get('run_dir', '<unknown>')}", ""])
        return "\n".join(lines)
    completed_stages = result.run_summary.get("completed_stages", [])
    final_deliverable = result.final_deliverable or {}
    lines = [
        _semantic_text("=== OwnPI Answer ===", role='success', bold=True, tokens=tokens),
        f"{_semantic_text('Status:', role='info', bold=True, tokens=tokens)} {result.status}",
        f"{_semantic_text('Final decision:', role='warning', bold=True, tokens=tokens)} {result.final_decision}",
        "",
        _semantic_text("Completed stages:", role='info', bold=True, tokens=tokens),
        *([f"  - {item}" for item in completed_stages] if completed_stages else ["  - None"]),
        "",
        *_render_final_deliverable_lines(final_deliverable, markdown=False, tokens=tokens),
    ]
    _append_decision_mapping_lines(
        lines,
        final_decision=result.final_decision,
        raw_final_decision=result.raw_final_decision,
        decision_mapping_applied=result.decision_mapping_applied,
        decision_mapping_reason=result.decision_mapping_reason,
        markdown=False,
    )
    _append_run_outcome_lines(
        lines,
        _run_outcome_mapping(result.run_outcome.to_dict() if result.run_outcome is not None else None),
        markdown=False,
        tokens=tokens,
    )
    lines.extend(["", f"Artifacts saved to: {result.run_artifacts.get('run_dir', '<unknown>')}", ""])
    return "\n".join(lines)


def render_final_package_markdown(result: FinalPackage, *, ui_mode: str = 'developer') -> str:
    developer_surface = _surface_is_developer(ui_mode)
    execution_tier = str(result.run_summary.get("execution_tier", "full"))
    compact_summary_lines = _render_compact_execution_summary_lines(result.run_summary)
    if result.status == "paused_for_human":
        pending = result.run_summary.get("pending_human_clarification", {}) if isinstance(result.run_summary, dict) else {}
        pending_permission = result.run_summary.get("pending_tool_permission", {}) if isinstance(result.run_summary, dict) else {}
        pause_reason = str(result.run_summary.get("pause_reason", "") or "") if isinstance(result.run_summary, dict) else ""
        run_dir = result.run_artifacts.get("run_dir", "<unknown>")
        lines = [
            "# OwnPI Paused",
            "",
            f"- **Status:** {result.status}",
            f"- **Final decision:** {result.final_decision}",
        ]
        if compact_summary_lines:
            lines.extend(compact_summary_lines)
        if isinstance(pending_permission, dict) and pending_permission and pause_reason == "tool_permission":
            waiting_for = str(
                pending_permission.get('waiting_for')
                or f"approve {pending_permission.get('tool_name', '<unknown>')}"
            )
            lines.extend(
                [
                    "",
                    f"- **Waiting for:** {waiting_for}",
                    f"- **Tool:** {pending_permission.get('tool_name', '<unknown>')} "
                    f"(class={pending_permission.get('permission_class', 'default')})",
                ]
            )
            if developer_surface:
                lines.extend(
                    [
                        f"- **Pending permission:** {pending_permission.get('session_id', '<unknown>')}",
                        f"- **Origin:** {pending_permission.get('origin_kind', 'runtime')} · "
                        f"interaction_request_id={pending_permission.get('interaction_request_id', pending_permission.get('request_id', '<unknown>'))}",
                        f"- **Resume target:** {pending_permission.get('stage', '<unknown>')} "
                        f"a{pending_permission.get('attempt', 0)} "
                        f"continuation={pending_permission.get('continuation_index', 0)} "
                        f"resume={pending_permission.get('continuation_kind', 'continuation_runtime')}",
                    ]
                )
            reason = str(pending_permission.get('reason', '')).strip()
            if reason:
                lines.append(f"- **Reason:** {reason}")
            human_prompt = str(pending_permission.get('human_prompt', '')).strip()
            if human_prompt:
                lines.append(f"- **Prompt:** {human_prompt}")
            actions = pending_permission.get('available_actions', [])
            if isinstance(actions, list) and actions:
                lines.append(f"- **Actions:** {', '.join(str(item) for item in actions)}")
            lines.extend(["", f"Artifacts saved to: {run_dir}", ""])
            return "\n".join(lines)
        if isinstance(pending, dict) and pending:
            pending_label = _format_human_clarification_target(pending, include_round=True)
            lines.extend(["", "- **Waiting for:** answer questions"])
            if developer_surface:
                lines.extend(
                    [
                        f"- **Pending session:** {_format_human_clarification_session_id(pending)} "
                        f"{pending_label} "
                        f"resume={pending.get('continuation_kind', 'stage_attempt_reproduce')}",
                        f"- **Origin:** {pending.get('origin_kind', 'controller')} · "
                        f"interaction_request_id={pending.get('interaction_request_id', pending.get('session_id', '<unknown>'))}",
                    ]
                )
            reason = str(pending.get('reason', '')).strip()
            if reason:
                lines.append(f"- **Reason:** {reason}")
            lines.extend(["", "## Questions"])
            for index, item in enumerate(pending.get('questions', []), start=1):
                if not isinstance(item, dict):
                    continue
                if developer_surface:
                    question_label = item.get('question_key', item.get('question_id', '?'))
                    lines.append(f"- **{question_label}**: {item.get('question', '')}")
                else:
                    question_id = str(item.get('question_id', '?') or '?')
                    question_key = str(item.get('question_key', question_id) or question_id)
                    header = str(item.get('header') or question_id or f'Question {index}')
                    lines.append(
                        f"{index}. **{header}** `[question_id: {question_id}]` "
                        f"`[question_key: {question_key}]`: {item.get('question', '')}"
                    )
            lines.extend(
                [
                    "",
                    "- **Answer format:** single question can be answered directly, or with `question_id:` / `question_key:`.",
                    "- **Multi-question format:** use `1:` / `question_id:` / `question_key:`. Multi-select accepts comma-separated labels.",
                ]
            )
        lines.extend(["", f"Artifacts saved to: {run_dir}", ""])
        return "\n".join(lines)
    if execution_tier == "direct":
        answer = str(result.run_summary.get("direct_answer") or "").strip() or (
            'Request completed; session is waiting for input.' if result.status == 'idle'
            else '_No answer generated._')
        run_dir = result.run_artifacts.get("run_dir", "<unknown>")
        lines = [
            "# OwnPI Answer",
            "",
            f"- **Status:** {result.status}",
            f"- **Final decision:** {result.final_decision}",
        ]
        if developer_surface:
            lines.append(f"- **Execution tier:** {execution_tier}")
        if compact_summary_lines:
            lines.extend(compact_summary_lines)
        lines.extend(["", answer])
        _append_decision_mapping_lines(
            lines,
            final_decision=result.final_decision,
            raw_final_decision=result.raw_final_decision,
            decision_mapping_applied=result.decision_mapping_applied,
            decision_mapping_reason=result.decision_mapping_reason,
            markdown=True,
        )
        _append_run_outcome_lines(
            lines,
            _run_outcome_mapping(result.run_outcome.to_dict() if result.run_outcome is not None else None),
            markdown=True,
        )
        lines.extend(["", f"Artifacts saved to: {run_dir}", ""])
        return "\n".join(lines)
    completed_stages = result.run_summary.get("completed_stages", [])
    final_deliverable = result.final_deliverable or {}
    run_dir = result.run_artifacts.get("run_dir", "<unknown>")
    lines = [
        "# OwnPI Answer",
        "",
        f"- **Status:** {result.status}",
        f"- **Final decision:** {result.final_decision}",
    ]
    if compact_summary_lines:
        lines.extend(compact_summary_lines)
    lines.extend(_render_final_deliverable_lines(final_deliverable, markdown=True))
    if developer_surface:
        lines.extend(
            [
                "",
                "## Completed stages",
                *([f"- {item}" for item in completed_stages] if completed_stages else ["- None"]),
            ]
        )
    else:
        if completed_stages:
            lines.extend(["", f"- **Completed stages:** {', '.join(str(item) for item in completed_stages[:6])}"])
    _append_decision_mapping_lines(
        lines,
        final_decision=result.final_decision,
        raw_final_decision=result.raw_final_decision,
        decision_mapping_applied=result.decision_mapping_applied,
        decision_mapping_reason=result.decision_mapping_reason,
        markdown=True,
    )
    _append_run_outcome_lines(
        lines,
        _run_outcome_mapping(result.run_outcome.to_dict() if result.run_outcome is not None else None),
        markdown=True,
    )
    lines.extend(["", f"Artifacts saved to: {run_dir}", ""])
    return "\n".join(lines)


def render_help(commands: tuple[Any, ...] | list[Any] | None = None) -> str:
    lines = [
            "# OwnPI Help",
            "",
    ]
    if commands is not None:
        lines.extend(["## Registered commands"])
        for command in commands:
            name = str(getattr(command, 'name', '') or '').strip()
            description = str(getattr(command, 'description', '') or '').strip()
            source = str(getattr(command, 'source', '') or '').strip()
            kind = str(getattr(command, 'kind', '') or '').strip()
            if not name:
                continue
            suffix = f" ({kind}; {source})" if kind and source else ''
            lines.append(f"- {name} — {description}{suffix}".rstrip())
        lines.append("")
    lines.extend(
        [
            "## Core commands",
            "- /help — show this help message",
            "- /exit — exit the chat",
            "- /last — show the last interpreted task",
            "- /status — show current provider, model, and runtime status",
            "",
            "## Run control",
            "- /runs — list recent workflow runs",
            "",
            "## Task shortcuts",
            "- /run-task <task.yaml> — load and run a task spec file",
            "- /summarize-pdf <paper.pdf> [objective] — create and run a paper summarization task",
            "- /review-task <objective> — create and run a literature-review task",
            "",
            "## Skills",
            "- /skills — choose between listing skills and enabling or disabling them",
            "- /skills list — list visible skills from ~/.ownpi/skills and repo skills",
            "- /skills enable <name> — enable a visible skill",
            "- /skills disable <name> — disable a visible skill",
            "- /skills toggle <name> — toggle a visible skill",
            "- /skills install --from-path <path> [--name <name>] — install a local skill",
            "- /skills install --github-url <url> [--name <name>] — install a skill from GitHub",
            "- /skills remove <name> — remove a user-installed skill",
            "- /commands — list all slash commands",
            "- /commands status — list all slash commands with source metadata",
            "- /hooks status — list builtin/user/project/plugin hook sources",
            "- /extensions status — show extension discovery and trust status",
            "- /extensions reload — refresh skills, commands, hooks, plugins, and MCP prompt commands",
            "",
            "## Session controls",
            "- /mode or /mode <name> — switch UI mode: user or developer",
            "- /models — list configured LLM profiles and candidate models",
            "- /model or /model <name> — pick a model, then reasoning, or override the active model/profile",
            "- /reasonings — list candidate reasoning efforts",
            "- /reasoning or /reasoning <level> — pick or override reasoning effort",
            "",
            "## Routing behavior",
            "- Simple Q&A → direct answer",
            "- Engineering / research tasks → structured workflow",
        ]
    )
    return "\n".join(lines)


def _render_endpoint(config: LLMConfig) -> str:
    if config.base_url:
        return config.base_url
    if config.base_url_env:
        return f"env:{config.base_url_env}"
    if config.provider == "openai":
        return "https://api.openai.com/v1"
    return "<provider-default>"


def render_session_status_markdown(
    *,
    user_config: UserConfig,
    active_profile_name: str,
    active_llm_config: LLMConfig,
    model_override: str | None,
    reasoning_override: str | None,
    ui_mode: str,
    runtime_profile_selection: str | None = None,
) -> str:
    runtime_profile = resolve_runtime_profile(
        session_selection=runtime_profile_selection,
        config_selection=user_config.runtime.default_profile,
    )
    configured_profile = user_config.get_llm_config(active_profile_name)
    model_source = "session-override" if model_override else "profile-default"
    effective_reasoning, reasoning_source = _effective_reasoning_display(
        config=configured_profile,
        override=reasoning_override,
        override_applies=bool(reasoning_override),
    )
    from ownpi.runtime.limits.workflow import resolve_review_policy

    review_policy = resolve_review_policy(
        user_config.runtime.review_profile,
        stage_runtime_profile=runtime_profile.resolved,
    )
    lines = [
        "# Session Status",
        "",
        "## Runtime",
        f"- **UI mode:** {ui_mode}",
        f"- **Runtime profile:** {runtime_profile.resolved.value}",
        f"- **Runtime profile requested:** {runtime_profile.requested.value}",
        f"- **Runtime profile source:** {runtime_profile.source}",
        f"- **Active profile:** {active_profile_name}",
        f"- **Provider:** {active_llm_config.provider}",
        f"- **Configured model:** {configured_profile.model}",
        f"- **Effective model:** {active_llm_config.model} ({model_source})",
        f"- **Configured reasoning:** {configured_profile.reasoning_effort or '<unset>'}",
        f"- **Effective reasoning:** {effective_reasoning} ({reasoning_source})",
        f"- **Endpoint:** {_render_endpoint(active_llm_config)}",
        f"- **API style:** {active_llm_config.api_style}",
        f"- **Review profile:** {user_config.runtime.review_profile}",
        (f"- **Effective review policy (read-only):** {review_policy.to_dict()}"
         if runtime_profile.resolved.value == "research_level"
         else "- **Effective review policy:** Not applicable (Task direct)."),
        "",
        "## Overrides",
        f"- **Model override active:** {'yes' if model_override else 'no'}",
        f"- **Reasoning override active:** {'yes' if reasoning_override else 'no'}",
        "",
        "## Agent bindings",
    ]
    for agent in user_config.agents.get_enabled_pipeline():
        llm_name = user_config.resolve_llm_name(agent.llm)
        bound_config = user_config.get_llm_config(llm_name)
        effective_model = bound_config.model
        effective_reasoning, source = _effective_reasoning_display(
            config=bound_config,
            override=reasoning_override,
            override_applies=bool(reasoning_override and bound_config.provider == active_llm_config.provider),
        )
        source = "profile-default"
        marker = ""
        if llm_name == active_profile_name:
            effective_model = active_llm_config.model
            effective_reasoning, source = _effective_reasoning_display(
                config=bound_config,
                override=reasoning_override,
                override_applies=bool(reasoning_override),
            )
            source = f"active-session ({source})"
            marker = " _(active profile)_"
        elif reasoning_override and bound_config.provider == active_llm_config.provider:
            source = "session-override"
        elif source != "session-override":
            _, source = _effective_reasoning_display(config=bound_config)
        lines.append(
            f"- **{agent.id}** — role={agent.role}, llm={llm_name}, provider={bound_config.provider}, "
            f"model={effective_model}, reasoning={effective_reasoning}, source={source}{marker}"
        )
    lines.append("")
    return "\n".join(lines)


def render_available_models_markdown(
    *,
    user_config: UserConfig,
    active_profile_name: str,
    active_llm_config: LLMConfig,
) -> str:
    lines = ["# Configured LLM Profiles", ""]
    for profile_name in sorted(user_config.llms):
        config = user_config.get_llm_config(profile_name)
        is_active = profile_name == active_profile_name
        model = active_llm_config.model if is_active else config.model
        reasoning, _ = _effective_reasoning_display(
            config=config,
            override=active_llm_config.reasoning_effort if is_active else None,
            override_applies=is_active,
        )
        lines.extend(
            [
                f"## {profile_name}" + (" _(active)_" if is_active else ""),
                f"- **Provider:** {config.provider}",
                f"- **Model:** {model}",
                f"- **Reasoning:** {reasoning}",
                f"- **Endpoint:** {_render_endpoint(config)}",
                "",
            ]
        )
    return "\n".join(lines)


def render_session_banner(
    *,
    user_config: UserConfig,
    active_profile_name: str,
    active_llm_config: LLMConfig,
    model_override: str | None,
    reasoning_override: str | None,
    ui_mode: str,
    workspace_path: str | None = None,
    recent_activity: list[str] | None = None,
    theme_setting: str = 'auto',
    use_terminal_background: bool = True,
) -> str:
    tokens = _renderer_tokens(
        theme_setting=theme_setting,
        use_terminal_background=use_terminal_background,
    )
    configured_profile = user_config.get_llm_config(active_profile_name)
    effective_reasoning, _ = _effective_reasoning_display(
        config=configured_profile,
        override=reasoning_override,
        override_applies=bool(reasoning_override),
    )
    model_label = active_llm_config.model + ("*" if model_override else "")
    reasoning_label = effective_reasoning + ("*" if reasoning_override else "")
    workspace_label = _display_workspace_path(workspace_path)
    activity_lines = recent_activity or ["No recent activity"]
    lines = [
        _semantic_text(f"OwnPI v{__version__}", role='success', bold=True, tokens=tokens),
        _semantic_text("Welcome back!", role='primary', bold=True, tokens=tokens),
        f"{active_profile_name} · {model_label}",
        f"reasoning {reasoning_label}",
        f"{ui_mode} mode",
        workspace_label,
        "",
        "Tips for getting started",
        "- / for commands · $ for skills",
        "- /model to switch model and reasoning together",
        "- /status for runtime details · /help for the full command list",
        "",
        "Recent activity",
        *[f"- {item}" for item in activity_lines],
        "",
    ]
    return "\n".join(lines)


def render_session_banner_rich(
    *,
    user_config: UserConfig,
    active_profile_name: str,
    active_llm_config: LLMConfig,
    model_override: str | None,
    reasoning_override: str | None,
    ui_mode: str,
    workspace_path: str | None = None,
    recent_activity: list[str] | None = None,
    theme_setting: str = 'auto',
    use_terminal_background: bool = True,
):
    try:
        from rich import box
        from rich.align import Align
        from rich.console import Group
        from rich.panel import Panel
        from rich.rule import Rule
        from rich.table import Table
        from rich.text import Text
    except ImportError:
        return None

    configured_profile = user_config.get_llm_config(active_profile_name)
    effective_reasoning, _ = _effective_reasoning_display(
        config=configured_profile,
        override=reasoning_override,
        override_applies=bool(reasoning_override),
    )
    model_label = active_llm_config.model + ("*" if model_override else "")
    reasoning_label = effective_reasoning + ("*" if reasoning_override else "")
    workspace_label = _display_workspace_path(workspace_path)
    activity_lines = recent_activity or ["No recent activity"]
    tokens = get_ui_tokens(
        resolve_theme_setting(theme_setting),
        use_terminal_background=use_terminal_background,
    )
    panel_theme = build_rich_panel_theme(tokens, ui_mode=ui_mode)

    left_column = Group(
        Align.center(Text("Welcome back!", style=f"bold {tokens.text}")),
        Text(""),
        Align.center(Text("▗ ▗   ▖ ▖\n\n▘▘ ▝▝", style=f"bold {tokens.accent_soft}")),
        Text(""),
        Align.center(Text(f"{active_profile_name} · {model_label}", style=f"bold {tokens.text}")),
        Align.center(Text(f"reasoning {reasoning_label}", style=tokens.text)),
        Align.center(Text(f"{ui_mode} mode", style=tokens.text)),
        Align.center(Text(workspace_label, style=tokens.text, overflow="fold")),
    )

    tip_lines = [
        Text("Tips for getting started", style=f"bold {tokens.text}"),
        Text.assemble(
            ("Press ", tokens.text),
            ("/", f"bold {tokens.accent}"),
            (" to open commands and ", tokens.text),
            ("$", f"bold {tokens.text_title}"),
            (" to open skills.", tokens.text),
        ),
        Text.assemble(
            ("Use ", tokens.text),
            ("/model", f"bold {tokens.accent}"),
            (" to pick a model and reasoning together.", tokens.text),
        ),
        Text.assemble(
            ("Run ", tokens.text),
            ("/status", f"bold {tokens.accent}"),
            (" for runtime details and ", tokens.text),
            ("/help", f"bold {tokens.accent}"),
            (" for the full command list.", tokens.text),
        ),
    ]
    if workspace_path and Path(workspace_path).expanduser().resolve() == Path.home().resolve():
        tip_lines.append(
            Text(
                "Note: you launched ownpi in your home directory. For the best experience, launch it in a project directory instead.",
                style=tokens.text_muted,
            )
        )
    activity_renderables = [Text(item, style=tokens.text) for item in activity_lines]
    right_column = Group(
        *tip_lines,
        Text(""),
        Rule(style=tokens.border),
        Text("Recent activity", style=f"bold {tokens.text}"),
        *activity_renderables,
    )

    layout = Table.grid(expand=True, padding=(0, 3))
    layout.add_column(ratio=2)
    layout.add_column(ratio=4)
    layout.add_row(left_column, right_column)

    return Panel(
        layout,
        title=Text(f"OwnPI v{__version__}", style=panel_theme.title_style),
        title_align="left",
        border_style=panel_theme.border_style,
        box=box.ROUNDED,
        padding=(1, 2),
    )


def render_runs_list(runs: list[dict[str, object]]) -> str:
    lines = ["# Recent Runs", ""]
    if not runs:
        lines.append("_No runs found._")
    for index, item in enumerate(runs, start=1):
        lines.extend([
            f"## {index}. {item.get('run_id', '<unknown>')}",
            f"- **Run dir:** {item.get('run_dir', '<unknown>')}", "",
        ])
    return "\n".join(lines)


def render_inline_round_artifact(
    *,
    round_label: str,
    agent_id: str,
    role: str,
    artifact_type: str,
    payload: dict[str, object],
    file_path: str,
    theme_setting: str = 'auto',
    use_terminal_background: bool = True,
) -> str:
    tokens = _renderer_tokens(
        theme_setting=theme_setting,
        use_terminal_background=use_terminal_background,
    )
    trace_role = 'info' if role == 'core' else 'warning' if role == 'reviewer' else 'accent'
    prefix = _semantic_text(
        f"[trace] {round_label} {agent_id}/{role}",
        role=trace_role,
        bold=True,
        tokens=tokens,
    )
    if artifact_type == "stage_review":
        return (
            f"{prefix} review record={payload.get('record_id', '<unknown>')} "
            f"round={payload.get('review_round', '<unknown>')} file={file_path}"
        )
    return f"{prefix} artifact={artifact_type} file={file_path}"


def render_event_message(
    message: str,
    *,
    theme_setting: str = 'auto',
    use_terminal_background: bool = True,
) -> str:
    lowered = message.lower()
    trace_message = _compact_trace_message(message)
    tokens = get_ui_tokens(
        resolve_theme_setting(theme_setting),
        use_terminal_background=use_terminal_background,
    )
    color, bold = _event_style_for_message(message, lowered=lowered, tokens=tokens)
    return style_text(trace_message, color=color, bold=bold)


def render_event_rich_text(
    message: str,
    *,
    theme_setting: str = 'auto',
    use_terminal_background: bool = True,
    tokens: UISemanticTokens | None = None,
):
    try:
        from rich.text import Text
    except ImportError:
        return render_event_message(
            message,
            theme_setting=theme_setting,
            use_terminal_background=use_terminal_background,
        )
    resolved_tokens = tokens or get_ui_tokens(
        resolve_theme_setting(theme_setting),
        use_terminal_background=use_terminal_background,
    )
    trace_message = _compact_trace_message(message)
    color, bold = _event_style_for_message(
        message,
        lowered=message.lower(),
        tokens=resolved_tokens,
    )
    style = f"{'bold ' if bold else ''}{color}".strip()
    return Text(trace_message, style=style)


def _event_style_for_message(
    message: str,
    *,
    lowered: str,
    tokens: UISemanticTokens,
) -> tuple[str, bool]:
    if message.startswith('[sub] '):
        if 'status=failed' in lowered or 'timed_out' in lowered or 'status=cancelled' in lowered:
            return tokens.warning, True
        if 'status=completed' in lowered:
            return tokens.success, True
        return tokens.accent, True
    if message.startswith('[ctx] '):
        return tokens.accent, True
    if message.startswith('[tool] '):
        return tokens.focus, True
    if message.startswith('[tier-upgrade] '):
        return tokens.text_title, True
    if "artifact_written" in lowered:
        return tokens.focus, True
    if "stage_review_written" in lowered or "gate_decision" in lowered:
        return tokens.warning, True
    if "stage_started" in lowered or "stopping:" in lowered:
        return tokens.text_title, True
    if "running agent=" in lowered:
        return tokens.text_muted, False
    return tokens.text_muted, False


def _compact_trace_message(message: str) -> str:
    if message.startswith('[sub] '):
        return f"sub {message[len('[sub] '):].strip()}"
    if message.startswith('[ctx] '):
        return f"ctx {message[len('[ctx] '):].strip()}"
    if message.startswith('[tool] '):
        return f"tool {message[len('[tool] '):].strip()}"
    label, remainder = _split_trace_label(message)
    if label is None:
        return message
    if remainder == 'stage_started' or remainder.startswith('stage_started '):
        return f"→ {label} {remainder}"
    if remainder.startswith('running agent='):
        return f"· {label} {remainder.replace('running ', '')}"
    if remainder in {'artifact_written', 'stage_review_written'}:
        return f"✓ {label} {remainder}"
    if remainder.startswith('gate_decision='):
        return f"⇒ {label} {remainder}"
    return message


def _split_trace_label(message: str) -> tuple[str | None, str]:
    match = re.match(r'^\[(?P<label>[^\]]+)\]\s*(?P<body>.*)$', message)
    if match is None:
        return None, message
    return match.group('label'), match.group('body').strip()


def _compact_preview(items: list[str], *, limit: int) -> str:
    if not items:
        return "[]"
    preview = items[:limit]
    if len(items) > limit:
        preview.append(f"+{len(items) - limit}")
    return "[" + ",".join(preview) + "]"
