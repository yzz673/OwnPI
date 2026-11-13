from __future__ import annotations

import shlex
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class WorkflowCommandResult:
    message: str | None = None
    prompt: str | None = None


def handle_workflow_command(
    user_message: str,
    *,
    project_root: Path,
) -> WorkflowCommandResult | None:
    if not user_message.startswith('/'):
        return None
    tokens = shlex.split(user_message)
    if not tokens:
        return None
    command = tokens[0]
    if command == '/run-task':
        if len(tokens) != 2:
            return WorkflowCommandResult(message='Usage: /run-task <task.yaml>')
        task_path = Path(tokens[1]).expanduser()
        if not task_path.is_absolute():
            task_path = (project_root / task_path).resolve()
        return WorkflowCommandResult(
            message=f'Using structured task source {task_path}',
            prompt=(
                'Execute the task described by the following structured source file. '
                'Treat the file as source material, while this message remains the canonical request.\n\n'
                f'Structured task source: {task_path}'
            ),
        )
    if command == '/summarize-pdf':
        if len(tokens) < 2:
            return WorkflowCommandResult(message='Usage: /summarize-pdf <paper.pdf> [objective text]')
        pdf_path = Path(tokens[1]).expanduser()
        if not pdf_path.is_absolute():
            pdf_path = (project_root / pdf_path).resolve()
        objective = ' '.join(tokens[2:]).strip() or 'Summarize this paper with grounded evidence.'
        return WorkflowCommandResult(
            message=f'Using PDF source {pdf_path}',
            prompt=f'{objective}\n\nPDF source: {pdf_path}',
        )
    if command == '/review-task':
        objective = ' '.join(tokens[1:]).strip()
        if not objective:
            return WorkflowCommandResult(message='Usage: /review-task <objective>')
        return WorkflowCommandResult(
            message='Using review request from workflow command.',
            prompt=objective,
        )
    return None
