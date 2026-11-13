"""Execute a newly allocated background run through the normal fresh entry."""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import sys

from ownpi.app.paths import AppPaths
from ownpi.config.loader import load_user_config
from ownpi.controller.run_context import PreparedRunContext
from ownpi.controller.runtime_control import RuntimeControlService
from ownpi.runtime.sessions import SessionStore, parse_session_run_dir


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--run-dir', required=True, type=Path)
    parser.add_argument('--project-root', required=True, type=Path)
    parser.add_argument('--app-home', required=True, type=Path)
    parser.add_argument('--config', required=True, type=Path)
    args = parser.parse_args(argv)
    os.environ['OWNPI_HOME'] = str(args.app_home.resolve())
    paths = AppPaths.discover()
    identity = parse_session_run_dir(args.run_dir)
    if identity is None:
        raise ValueError('Fresh worker requires a session-managed run directory')
    context = PreparedRunContext(
        session_id=identity.session_id, task_id=identity.task_id,
        run_id=identity.run_id, run_dir=args.run_dir,
    )
    try:
        from ownpi.cli.main import _load_runtime_env

        _load_runtime_env(paths)
        config = load_user_config(args.config)
        service = RuntimeControlService(
            user_config=config, project_root=args.project_root, app_paths=paths,
        )
        from ownpi.runtime.rollout.business_reader import read_business_events
        from ownpi.runtime.rollout.direct_resume import is_direct_session
        from ownpi.runtime.rollout.run_events import run_events_path

        events_path = run_events_path(context.run_dir)
        if events_path.exists() and is_direct_session(read_business_events(events_path).events):
            service.resume(context.run_dir)
        else:
            with (context.run_dir / 'raw_user_message.txt').open(encoding='utf-8', newline='') as source:
                raw_message = source.read()
            service.start(raw_user_message=raw_message, run_context=context)
    except Exception as error:
        store = SessionStore(paths.app_home / 'sessions')
        store.update_run_status(context.session_id, context.task_id, context.run_id, status='failed')
        store.update_task_status(context.session_id, context.task_id, status='failed',
                                 active_run_id=context.run_id)
        print(f'ownpi: background start failed: {error}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
