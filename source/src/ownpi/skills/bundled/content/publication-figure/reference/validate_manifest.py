#!/usr/bin/env python3
"""Validate the required publication-figure delivery bundle."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--figure-name", required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    figure_dir = args.workspace / "output_synthesis" / "figures" / args.figure_name
    required = [
        f"{args.figure_name}_logical_design.md",
        f"{args.figure_name}_aesthetic_design.md",
        f"{args.figure_name}_implementation.md",
        f"{args.figure_name}_code_check.md",
        f"{args.figure_name}_script_check_report.md",
        f"{args.figure_name}_visual_check.md",
        f"{args.figure_name}_caption.md",
        f"{args.figure_name}.svg",
        f"{args.figure_name}.pdf",
        f"{args.figure_name}.png",
        "_workflow_state.md",
        "revision.json",
    ]
    missing = [name for name in required if not (figure_dir / name).is_file()]
    state_path = figure_dir / "_workflow_state.md"
    state = state_path.read_text(encoding="utf-8") if state_path.is_file() else ""
    report_path = figure_dir / f"{args.figure_name}_script_check_report.md"
    visual_path = figure_dir / f"{args.figure_name}_visual_check.md"
    failures = list(missing)
    if "Current state: DELIVER" not in state:
        failures.append("_workflow_state.md does not declare Current state: DELIVER")
    if "Verdict: PASS" not in (report_path.read_text(encoding="utf-8") if report_path.is_file() else ""):
        failures.append("render-check report does not declare Verdict: PASS")
    if "READY_FOR_DELIVERY" not in (visual_path.read_text(encoding="utf-8") if visual_path.is_file() else ""):
        failures.append("visual-check record does not declare READY_FOR_DELIVERY")
    revision_path = figure_dir / "revision.json"
    try:
        revision = json.loads(revision_path.read_text(encoding="utf-8"))
        if not revision.get("files") or not revision.get("source_sha256"):
            failures.append("revision.json lacks source_sha256 or files")
    except (OSError, json.JSONDecodeError):
        failures.append("revision.json is missing or invalid")
    payload = {
        "figure_name": args.figure_name,
        "figure_dir": str(figure_dir),
        "verdict": "PASS" if not failures else "BLOCKED",
        "missing_or_invalid": failures,
    }
    output = figure_dir / "manifest_validation.json"
    if figure_dir.is_dir():
        output.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(payload, indent=2))
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
