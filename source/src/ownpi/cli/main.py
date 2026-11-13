from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from ownpi.app.bootstrap import scaffold_app_home
from ownpi.app.config_tools import set_review_profile
from ownpi.app.paths import AppPaths
from ownpi.app.setup import inspect_llm_readiness, run_interactive_setup
from ownpi.chat.answer_normalizer import normalize_human_answer_mapping
from ownpi.chat.interaction_renderer import (
    render_pending_human_interaction,
    render_pending_tool_permission,
)
from ownpi.controller.pending_interactions import (
    pending_human_interactions,
    pending_permission_interactions,
)
from ownpi.cli.runtime_commands import (
    add_runtime_parsers,
    dispatch_runtime_command,
)
from ownpi.config.env_loader import load_dotenv
from ownpi.config.loader import load_user_config
from ownpi.product.doctor import render_doctor_report, run_doctor
from ownpi.product.deployment import (
    inspect_deployment,
    update_installation,
)
from ownpi.product.managed_tools import (
    inspect_brainlib,
)
from ownpi.product.pack_installation import (
    install_or_update_neuroscience_pack,
    remove_neuroscience_pack,
    rollback_neuroscience_pack,
)
from ownpi.product.packs import inspect_neuroscience_pack, set_neuroscience_pack_enabled
from ownpi.product.resources import inspect_product_resources
from ownpi.schemas.runtime import UserConfig
from ownpi.schemas.workflow_plan import ExecutionTier, execution_tier_for_profile, resolve_runtime_profile
from ownpi.skills.manager import (
    list_installed_skills,
    remove_skill,
)


def render_final_package(*args, **kwargs):
    from ownpi.chat.renderer import render_final_package as _render_final_package

    return _render_final_package(*args, **kwargs)


def WorkflowEngine(*args, **kwargs):
    from ownpi.controller import WorkflowEngine as _WorkflowEngine

    return _WorkflowEngine(*args, **kwargs)


def InteractionController(*args, **kwargs):
    from ownpi.controller.interaction_controller import (
        InteractionController as _InteractionController,
    )

    return _InteractionController(*args, **kwargs)


def RespondPermission(*args, **kwargs):
    from ownpi.controller.interaction_actions import RespondPermission as _RespondPermission

    return _RespondPermission(*args, **kwargs)


def SubmitHumanAnswers(*args, **kwargs):
    from ownpi.controller.interaction_actions import SubmitHumanAnswers as _SubmitHumanAnswers

    return _SubmitHumanAnswers(*args, **kwargs)


def inspect_pdf_tool_status(*args, **kwargs):
    from ownpi.tools.pdf_reader import inspect_pdf_tool_status as _inspect_pdf_tool_status

    return _inspect_pdf_tool_status(*args, **kwargs)


def _safe_pdf_status() -> dict[str, object]:
    try:
        return inspect_pdf_tool_status().to_dict()
    except ModuleNotFoundError as exc:
        return {
            "available": False,
            "error": f"missing optional runtime dependency: {exc.name}",
        }


def _execution_root() -> Path:
    return Path.cwd()


def _resolve_project_root(user_config: UserConfig, raw_project_root: str | None = None) -> Path:
    runtime_config = getattr(user_config, "runtime", None)
    configured_root = getattr(runtime_config, "project_root", "") if runtime_config is not None else ""
    raw = str(raw_project_root or configured_root or "").strip()
    if not raw:
        return _execution_root().resolve()
    candidate = Path(raw).expanduser()
    if not candidate.is_absolute():
        candidate = _execution_root() / candidate
    return candidate.resolve()


def _add_project_root_argument(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--project-root",
        default=None,
        help=(
            "Explicit runtime project/workspace root for tools. "
            "Overrides runtime.project_root; defaults to the launch directory."
        ),
    )


def _pending_human_clarification_for_cli(run_dir: Path) -> dict[str, object]:
    pending = pending_human_interactions(run_dir)
    return pending[0] if pending else {}


def _pending_tool_permission_for_cli(run_dir: Path) -> dict[str, object]:
    pending = pending_permission_interactions(run_dir)
    return pending[0] if pending else {}


def _normalize_cli_answers(
    raw_answers: dict[str, object],
    *,
    pending: dict[str, object],
) -> dict[str, dict[str, object]]:
    return normalize_human_answer_mapping(raw_answers, pending=pending)


def _ensure_default_scaffold(app_paths: AppPaths) -> list[Path]:
    if app_paths.config_path.exists():
        return []
    return scaffold_app_home(app_paths)


def build_parser(app_paths: AppPaths) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ownpi", description="OwnPI CLI")
    subparsers = parser.add_subparsers(dest="command", required=True)

    init_parser = subparsers.add_parser("init", help="Scaffold ~/.ownpi config, env example, and demo task")
    init_parser.add_argument("--force", action="store_true", help="Overwrite generated template files")
    subparsers.add_parser("setup", help="Interactive setup for API key, base URL, and default models")

    config_parser = subparsers.add_parser("config", help="Read or update user config values")
    config_subparsers = config_parser.add_subparsers(dest="config_command", required=True)
    config_show_parser = config_subparsers.add_parser("show", help="Show current runtime + llm config summary")
    config_show_parser.add_argument(
        "--config",
        default=str(app_paths.config_path),
        help="Path to user config yaml",
    )
    config_review_profile_parser = config_subparsers.add_parser(
        "set-review-profile",
        help="Set runtime.review_profile without rerunning full setup",
    )
    config_review_profile_parser.add_argument(
        "value", choices=["auto", "standard", "rigorous", "exhaustive"]
    )
    config_review_profile_parser.add_argument(
        "--config",
        default=str(app_paths.config_path),
        help="Path to user config yaml",
    )

    skills_parser = subparsers.add_parser("skills", help="List or remove OwnPI skills")
    skills_subparsers = skills_parser.add_subparsers(dest="skills_command", required=True)
    skills_list_parser = skills_subparsers.add_parser("list", help="List installed skills")
    skills_list_parser.add_argument("--json", action="store_true", help="Print machine-readable JSON")
    skills_remove_parser = skills_subparsers.add_parser("remove", help="Remove a skill from ~/.ownpi/skills")
    skills_remove_parser.add_argument("name", help="Installed skill directory name in ~/.ownpi/skills")
    skills_remove_parser.add_argument("--json", action="store_true", help="Print machine-readable JSON")

    tools_parser = subparsers.add_parser("tools", help="Inspect and maintain managed external tools")
    tools_subparsers = tools_parser.add_subparsers(dest="tools_command", required=True)
    tools_list_parser = tools_subparsers.add_parser("list", help="List managed external tools")
    tools_list_parser.add_argument("--json", action="store_true", help="Print machine-readable JSON")
    tools_doctor_parser = tools_subparsers.add_parser("doctor", help="Diagnose a managed external tool")
    tools_doctor_parser.add_argument("tool", choices=["brainlib"])
    tools_doctor_parser.add_argument("--json", action="store_true", help="Print machine-readable JSON")

    pack_parser = subparsers.add_parser("pack", help="Install or manage an OwnPI capability Pack")
    pack_subparsers = pack_parser.add_subparsers(dest="pack_command", required=True)
    for action in ("install", "update", "rollback", "remove", "enable", "disable", "status"):
        action_parser = pack_subparsers.add_parser(action, help=f"{action.title()} an OwnPI capability Pack")
        action_parser.add_argument("pack_id", choices=["neuroscience"])
        if action == "status":
            action_parser.add_argument("--json", action="store_true", help="Print machine-readable JSON")

    add_runtime_parsers(subparsers, app_paths)
    clarify_parser = subparsers.add_parser("clarify", help="Inspect or answer paused human clarification questions")
    clarify_subparsers = clarify_parser.add_subparsers(dest="clarify_command", required=True)
    clarify_show_parser = clarify_subparsers.add_parser("show", help="Read-only view of pending clarification questions for a paused run")
    clarify_show_parser.add_argument("run_dir", help="Path shaped as <app_home>/sessions/<session_id>/tasks/<task_id>/runs/<run_id>")
    clarify_answer_parser = clarify_subparsers.add_parser("answer", help="Submit human clarification answers for the next Start")
    clarify_answer_parser.add_argument("run_dir", help="Path shaped as <app_home>/sessions/<session_id>/tasks/<task_id>/runs/<run_id>")
    clarify_answer_parser.add_argument("--answers-file", required=True, help="JSON file with {\"answers\": {...}}")
    clarify_answer_parser.add_argument("--json", action="store_true", help="Print machine-readable JSON")
    clarify_answer_parser.add_argument(
        "--config",
        default=str(app_paths.config_path),
        help="Path to user config yaml",
    )
    clarify_answer_parser.add_argument("--llm-profile", help="Temporarily select an LLM profile for this resume")
    _add_project_root_argument(clarify_answer_parser)
    permission_parser = subparsers.add_parser("permission", help="Show or answer paused tool permission requests")
    permission_subparsers = permission_parser.add_subparsers(dest="permission_command", required=True)
    permission_show_parser = permission_subparsers.add_parser("show", help="Show pending tool permission request for a paused run")
    permission_show_parser.add_argument("run_dir", help="Path shaped as <app_home>/sessions/<session_id>/tasks/<task_id>/runs/<run_id>")
    permission_approve_parser = permission_subparsers.add_parser("approve", help="Approve the pending tool permission request")
    permission_approve_parser.add_argument("run_dir", help="Path shaped as <app_home>/sessions/<session_id>/tasks/<task_id>/runs/<run_id>")
    permission_approve_parser.add_argument(
        "--scope",
        choices=["once", "session"],
        default="once",
        help="Approval scope for this run/session only",
    )
    permission_approve_parser.add_argument(
        "--config",
        default=str(app_paths.config_path),
        help="Path to user config yaml",
    )
    permission_approve_parser.add_argument(
        "--json",
        action="store_true",
        help="Print the full final package JSON instead of the terminal summary",
    )
    permission_approve_parser.add_argument("--llm-profile", help="Temporarily select an LLM profile for this resume")
    _add_project_root_argument(permission_approve_parser)
    permission_deny_parser = permission_subparsers.add_parser(
        "deny",
        help="Deny required execution and terminalize the paused run",
    )
    permission_deny_parser.add_argument("run_dir", help="Path shaped as <app_home>/sessions/<session_id>/tasks/<task_id>/runs/<run_id>")
    permission_deny_parser.add_argument(
        "--config",
        default=str(app_paths.config_path),
        help="Path to user config yaml",
    )
    permission_deny_parser.add_argument(
        "--json",
        action="store_true",
        help="Print the full final package JSON instead of the terminal summary",
    )
    permission_deny_parser.add_argument("--llm-profile", help="Temporarily select an LLM profile for this resume")
    _add_project_root_argument(permission_deny_parser)
    permission_deny_command_parser = permission_subparsers.add_parser(
        "deny-command",
        help="Reject only the current tool command so the agent can try an alternative on the next resume",
    )
    permission_deny_command_parser.add_argument("run_dir", help="Path shaped as <app_home>/sessions/<session_id>/tasks/<task_id>/runs/<run_id>")
    permission_deny_command_parser.add_argument(
        "--config",
        default=str(app_paths.config_path),
        help="Path to user config yaml",
    )
    permission_deny_command_parser.add_argument(
        "--json",
        action="store_true",
        help="Print the full final package JSON instead of the terminal summary",
    )
    permission_deny_command_parser.add_argument("--llm-profile", help="Temporarily select an LLM profile for this resume")
    _add_project_root_argument(permission_deny_command_parser)

    doctor_parser = subparsers.add_parser("doctor", help="Diagnose product installation, app-home, and runtime readiness")
    doctor_parser.add_argument("--json", action="store_true", help="Print machine-readable JSON")
    doctor_parser.add_argument(
        "--install-check",
        action="store_true",
        help="Run the subset intended for installers and release smoke tests",
    )

    subparsers.add_parser("update", help="Update OwnPI Core and Dependency Bundle")

    subparsers.add_parser("info", help="Show app-home and runtime status")
    return parser


def _load_runtime_env(app_paths: AppPaths) -> None:
    load_dotenv(app_paths.env_path)
    cwd_env = _execution_root() / ".env"
    if cwd_env != app_paths.env_path:
        load_dotenv(cwd_env)


def _resolve_config_path(raw_path: str, app_paths: AppPaths) -> Path:
    candidate = Path(raw_path).expanduser()
    if candidate == app_paths.config_path and not candidate.exists():
        _ensure_default_scaffold(app_paths)
    return candidate


def _llm_profile_status(user_config: UserConfig) -> dict[str, object]:
    return {
        "active": user_config.selected_llm_profile or user_config.default_llm_name,
        "fallback": user_config.fallback_llm_profile,
        "default_alias": user_config.default_llm_name,
        "available": list(user_config.selectable_llm_profiles),
    }


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    app_paths = AppPaths.discover()
    _load_runtime_env(app_paths)

    parser = build_parser(app_paths)
    args = parser.parse_args(argv)

    try:
        return _dispatch(args, app_paths)
    except (OSError, ValueError, RuntimeError) as exc:
        expected_error_command = args.command in {'clarify', 'permission'} or (
            args.command == 'skills' and args.skills_command == 'remove'
        )
        if not expected_error_command:
            raise
        code = 1 if isinstance(exc, RuntimeError) else 2
        if bool(getattr(args, 'json', False)):
            print(json.dumps({'error': {'type': type(exc).__name__, 'message': str(exc)}},
                             ensure_ascii=False))
        else:
            print(f'ownpi: {exc}', file=sys.stderr)
        return code


def _dispatch(args: argparse.Namespace, app_paths: AppPaths) -> int:
    if args.command == "init":
        created = scaffold_app_home(app_paths, force=args.force)
        payload = {
            "app_home": str(app_paths.app_home),
            "created": [str(path) for path in created],
            "config_path": str(app_paths.config_path),
            "env_example_path": str(app_paths.env_example_path),
        }
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        return 0

    if args.command == "setup":
        result = run_interactive_setup(app_paths)
        print(json.dumps(result.to_dict(), indent=2, ensure_ascii=False))
        return 0

    if args.command == "update":
        try:
            return update_installation(app_home=app_paths.app_home)
        except (OSError, RuntimeError, ValueError) as exc:
            print(f"OwnPI update failed: {exc}", file=sys.stderr)
            return 1

    if args.command == "pack":
        try:
            if args.pack_command in {"install", "update"}:
                payload = install_or_update_neuroscience_pack(app_paths)
            elif args.pack_command == "rollback":
                payload = rollback_neuroscience_pack(app_paths)
            elif args.pack_command == "remove":
                payload = remove_neuroscience_pack(app_paths)
            elif args.pack_command in {"enable", "disable"}:
                payload = set_neuroscience_pack_enabled(
                    app_paths, enabled=args.pack_command == "enable"
                ).to_dict()
            else:
                payload = inspect_neuroscience_pack(app_paths).to_dict()
        except (OSError, RuntimeError, TypeError, ValueError) as exc:
            print(f"OwnPI Neuroscience Pack {args.pack_command} failed: {exc}", file=sys.stderr)
            return 1
        if args.pack_command == "status" and not args.json:
            print(f"neuroscience: {payload['status']}")
        else:
            print(json.dumps(payload, indent=2, ensure_ascii=False))
        return 0

    if args.command == "config":
        config_path = _resolve_config_path(args.config, app_paths)
        if args.config_command == "show":
            user_config = load_user_config(config_path)
            active_llm = user_config.get_llm_config()
            readiness = inspect_llm_readiness(active_llm)
            from ownpi.runtime.builders import build_effective_llm_snapshot
            from ownpi.runtime.limits.workflow import resolve_review_policy

            llm_snapshot = build_effective_llm_snapshot(active_llm)
            runtime_profile = resolve_runtime_profile(
                config_selection=user_config.runtime.default_profile
            )
            review_policy = resolve_review_policy(
                user_config.runtime.review_profile,
                stage_runtime_profile=runtime_profile.resolved,
            )
            payload = {
                "config_path": str(config_path),
                "runtime": {
                    "workspace_dir": user_config.runtime.workspace_dir,
                    "output_dir": user_config.runtime.output_dir,
                    "review_profile": user_config.runtime.review_profile,
                    "execution_tier": execution_tier_for_profile(runtime_profile.resolved).value,
                    "effective_review_policy": (
                        review_policy.to_dict() if execution_tier_for_profile(runtime_profile.resolved)
                        is ExecutionTier.FULL else None
                    ),
                },
                "default_llm": {
                    "provider": active_llm.provider,
                    "model": active_llm.model,
                    "reasoning_effort": active_llm.reasoning_effort,
                    "api_style": active_llm.api_style,
                    "web_search_execution_mode": llm_snapshot[
                        "web_search_execution_mode"
                    ],
                    "hosted_web_search_spec_digest": llm_snapshot[
                        "hosted_web_search_spec_digest"
                    ],
                    "ready_for_real_llm": readiness.ready_for_real_llm,
                },
                "llm_profile": _llm_profile_status(user_config),
                "pdf_status": _safe_pdf_status(),
            }
            print(json.dumps(payload, indent=2, ensure_ascii=False))
            return 0

        if args.config_command == "set-review-profile":
            payload = set_review_profile(config_path, args.value)
            print(json.dumps(payload, indent=2, ensure_ascii=False))
            return 0

    if args.command == "info":
        if app_paths.config_path.exists():
            user_config = load_user_config(app_paths.config_path)
            active_llm = user_config.get_llm_config()
            readiness = inspect_llm_readiness(active_llm)
            from ownpi.runtime.limits.workflow import resolve_review_policy

            runtime_profile = resolve_runtime_profile(
                config_selection=user_config.runtime.default_profile
            )
            review_policy = resolve_review_policy(
                user_config.runtime.review_profile,
                stage_runtime_profile=runtime_profile.resolved,
            )
            llm_status = {
                "profile": _llm_profile_status(user_config),
                "provider": readiness.provider,
                "model": readiness.model,
                "reasoning_effort": active_llm.reasoning_effort,
                "is_mock": readiness.is_mock,
                "has_api_key": readiness.has_api_key,
                "has_base_url": readiness.has_base_url,
                "ready_for_real_llm": readiness.ready_for_real_llm,
            }
            runtime_status = {
                "workspace_dir": user_config.runtime.workspace_dir,
                "output_dir": user_config.runtime.output_dir,
                "review_profile": user_config.runtime.review_profile,
                "execution_tier": execution_tier_for_profile(runtime_profile.resolved).value,
                "effective_review_policy": (
                    review_policy.to_dict() if execution_tier_for_profile(runtime_profile.resolved)
                    is ExecutionTier.FULL else None
                ),
            }
        else:
            llm_status = None
            runtime_status = None
        payload = {
            "name": "ownpi",
            "status": "runnable-v4",
            "deployment": inspect_deployment(app_paths.app_home).to_dict(),
            "neuroscience_pack": inspect_neuroscience_pack(app_paths).to_dict(),
            "app_home": str(app_paths.app_home),
            "config_path": str(app_paths.config_path),
            "env_path": str(app_paths.env_path),
            "execution_root": str(_execution_root()),
            "repo_root": str(app_paths.repo_root) if app_paths.repo_root else None,
            "source_repo_root": str(app_paths.source_repo_root) if app_paths.source_repo_root else None,
            "llm_status": llm_status,
            "runtime_status": runtime_status,
            "pdf_status": _safe_pdf_status(),
            "product_resources": inspect_product_resources().to_dict(),
        }
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        return 0

    if args.command == "doctor":
        report = run_doctor(app_paths, install_check=bool(args.install_check))
        if args.json:
            print(json.dumps(report.to_dict(), indent=2, ensure_ascii=False))
        else:
            print(render_doctor_report(report))
        return 0 if report.ok else 1

    if args.command == "skills":
        if args.skills_command == "list":
            installed = [item.to_dict() for item in list_installed_skills(app_paths)]
            if args.json:
                print(json.dumps(installed, indent=2, ensure_ascii=False))
            else:
                if not installed:
                    print("No skills installed.")
                else:
                    for index, item in enumerate(installed, start=1):
                        print(f"{index}. {item['name']} [{item['source']}]")
                        print(f"   path: {item['path']}")
                        print(f"   description: {item['description']}")
            return 0

        if args.skills_command == "remove":
            removed = remove_skill(app_paths, args.name)
            print(json.dumps(removed.to_dict(), indent=2, ensure_ascii=False))
            return 0

    if args.command == "tools":
        if args.tools_command == "list":
            payload = [inspect_brainlib(app_paths).to_dict()]
            if args.json:
                print(json.dumps(payload, indent=2, ensure_ascii=False))
            else:
                for item in payload:
                    print(f"{item['name']}: {item['status']} - {item['summary']}")
            return 0
        if args.tools_command == "doctor" and args.tool == "brainlib":
            payload = inspect_brainlib(app_paths).to_dict()
            if args.json:
                print(json.dumps(payload, indent=2, ensure_ascii=False))
            else:
                print(f"brainlib: {payload['status']} - {payload['summary']}")
                details = payload.get("details", {})
                if isinstance(details, dict) and details.get("command"):
                    print(f"command: {details['command']}")
            return 0 if payload["status"] == "ok" else 1
    if args.command == 'clarify' and args.clarify_command == 'show':
        run_dir = Path(args.run_dir)
        pending = pending_human_interactions(run_dir)
        if not pending:
            print("No pending questions.")
        for session in pending:
            print(render_pending_human_interaction(run_dir, session))
        return 0

    if args.command == 'permission' and args.permission_command == 'show':
        run_dir = Path(args.run_dir)
        pending = pending_permission_interactions(run_dir)
        if not pending:
            print("No pending permission requests.")
        for session in pending:
            print(render_pending_tool_permission(run_dir, session))
        return 0

    config_path = _resolve_config_path(args.config, app_paths)
    user_config = load_user_config(config_path, llm_profile=getattr(args, "llm_profile", None))
    project_root = _resolve_project_root(user_config, getattr(args, "project_root", None))

    if args.command == "run":
        try:
            return dispatch_runtime_command(
                args,
                user_config=user_config,
                project_root=project_root,
                app_paths=app_paths,
            )
        except (FileNotFoundError, PermissionError, ValueError) as exc:
            print(f"ownpi: {exc}", file=sys.stderr)
            return 2
        except RuntimeError as exc:
            print(f"ownpi: Runtime command failed: {exc}", file=sys.stderr)
            return 1
        except Exception as exc:
            print(f"ownpi: Runtime command failed: {exc}", file=sys.stderr)
            return 1

    if args.command == "clarify":
        config_path = _resolve_config_path(args.config, app_paths)
        user_config = load_user_config(config_path, llm_profile=getattr(args, "llm_profile", None))
        answers_path = Path(args.answers_file).expanduser()
        payload = json.loads(answers_path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict) or not isinstance(payload.get("answers"), dict):
            raise ValueError("answers file must be a JSON object with an `answers` mapping")
        run_dir = Path(args.run_dir)
        pending = _pending_human_clarification_for_cli(run_dir)
        if not pending:
            raise ValueError('No pending clarification is waiting for this run.')
        answers = _normalize_cli_answers(dict(payload["answers"]), pending=pending)
        engine = WorkflowEngine(
            user_config=user_config,
            project_root=project_root,
            app_paths=app_paths,
        )
        controller = InteractionController(engine=engine)
        try:
            result: Any = controller.apply_action(
                run_dir,
                SubmitHumanAnswers(
                    interaction_id=str(pending.get("session_id", "") or ""),
                    answers_by_question_id=answers,
                ),
            )
        finally:
            engine.close(wait=True)
        print(json.dumps(result.payload.to_dict(), indent=2, ensure_ascii=False))
        return 0

    if args.command == "permission":
        pending = _pending_tool_permission_for_cli(Path(args.run_dir))
        if not pending:
            raise ValueError(
                'No live tool approval is waiting. Resume the run first; '
                'a stopped invocation cannot receive an approval answer.'
            )
        engine = WorkflowEngine(
            user_config=user_config,
            project_root=project_root,
            app_paths=app_paths,
        )
        controller = InteractionController(engine=engine)
        try:
            result: Any = controller.apply_action(
                Path(args.run_dir),
                RespondPermission(
                    interaction_id=str(pending.get("session_id", "") or ""),
                    decision=str(args.permission_command).replace("-", "_"),
                    scope=getattr(args, "scope", "once"),
                ),
            )
        finally:
            engine.close(wait=True)
        payload = result.payload
        if bool(getattr(args, "json", False)):
            print(json.dumps(payload.to_dict(), indent=2, ensure_ascii=False))
        else:
            print(render_final_package(payload))
        return 0

    raise RuntimeError(f"Unhandled CLI command: {args.command}")


if __name__ == "__main__":
    raise SystemExit(main())
