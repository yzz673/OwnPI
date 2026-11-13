#!/usr/bin/env python3
"""Run all deterministic rendered-figure checks through one stable interface."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path
from typing import Any


def run_check(
    check_id: str,
    command: list[str],
    output_path: Path,
) -> dict[str, Any]:
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    payload: dict[str, Any] = {
        "check_id": check_id,
        "command": command,
        "returncode": result.returncode,
        "stdout": result.stdout,
        "stderr": result.stderr,
        "status": "PASS" if result.returncode == 0 else "FAIL",
    }
    output_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return payload


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path)
    parser.add_argument("--figure-name")
    parser.add_argument("--figure-dir", type=Path, help="Developer fallback; prefer --workspace and --figure-name.")
    parser.add_argument("--pdf", type=Path)
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--min-pt", type=float, default=5.0)
    parser.add_argument("--skip-alignment", action="store_true")
    parser.add_argument("--skip-pdf-text", action="store_true")
    parser.add_argument("--skip-collisions", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if (args.workspace is None) != (args.figure_name is None):
        raise SystemExit("--workspace and --figure-name must be provided together")
    if args.workspace is not None:
        figure_dir = args.workspace / "output_synthesis" / "figures" / args.figure_name
    elif args.figure_dir is not None:
        figure_dir = args.figure_dir
    else:
        raise SystemExit("provide --workspace and --figure-name")
    output_dir = args.output_dir or figure_dir / "script_check"
    output_dir.mkdir(parents=True, exist_ok=True)
    pdf = args.pdf or figure_dir / (figure_dir.name + ".pdf")
    manifest = args.manifest or figure_dir / (figure_dir.name + ".alignment.json")
    results: list[dict[str, Any]] = []
    blocking: list[dict[str, Any]] = []
    visual_followups: list[str] = []

    if not figure_dir.is_dir():
        blocking.append({"category": "INPUT", "message": "figure directory does not exist"})
    if pdf is not None and not pdf.is_file():
        blocking.append({"category": "INPUT", "message": f"PDF does not exist: {pdf}"})

    script_dir = Path(__file__).resolve().parent
    if not args.skip_pdf_text and pdf is not None and pdf.is_file():
        results.append(run_check(
            "pdf_text",
            [sys.executable, str(script_dir / "audit_pdf_text.py"), str(pdf),
             "--min-pt", str(args.min_pt)],
            output_dir / "pdf_text.json",
        ))
    elif not args.skip_pdf_text:
        results.append({"check_id": "pdf_text", "status": "NOT_AUDITABLE",
                        "reason": "a real PDF was not provided"})
        visual_followups.append("Inspect rendered typography from the exported figure.")

    if not args.skip_collisions and pdf is not None and pdf.is_file():
        results.append(run_check(
            "collisions",
            [sys.executable, str(script_dir / "audit_figure_collisions.py"), str(pdf)],
            output_dir / "collisions.json",
        ))
    elif not args.skip_collisions:
        results.append({"check_id": "collisions", "status": "NOT_AUDITABLE",
                        "reason": "a real PDF was not provided"})
        visual_followups.append("Inspect text, marks, legends, and clipping in the rendered figure.")

    if not args.skip_alignment:
        if manifest.is_file():
            results.append(run_check(
                "alignment",
                [sys.executable, str(script_dir / "audit_panel_alignment.py"),
                 str(manifest), "--strict"],
                output_dir / "alignment.json",
            ))
        else:
            results.append({"check_id": "alignment", "status": "NOT_AUDITABLE",
                            "reason": "alignment manifest was not provided"})
            visual_followups.append("Inspect panel edges, gutters, and label alignment visually.")

    for result in results:
        if result["status"] == "FAIL":
            blocking.append({"category": "RENDERED_CHECK", "check_id": result["check_id"],
                             "message": result.get("stderr") or result.get("stdout", "")})
    verdict = "FAIL" if blocking else (
        "PASS" if results and all(r["status"] == "PASS" for r in results)
        else "NOT_AUDITABLE"
    )
    report = {
        "figure_dir": str(figure_dir),
        "pdf": str(pdf) if pdf else None,
        "manifest": str(manifest),
        "verdict": verdict,
        "blocking_failures": blocking,
        "visual_followups": visual_followups,
        "checks": results,
        "input_sha256": {
            str(path): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in (pdf, manifest) if path.is_file()
        },
    }
    report_path = figure_dir / (figure_dir.name + "_script_check_report.md")
    (output_dir / "report.json").write_text(
        json.dumps(report, indent=2) + "\n", encoding="utf-8"
    )
    lines = [f"# Render Check Report", "", f"Verdict: {verdict}", ""]
    lines.append("## Checks")
    lines.extend(f"- {r['check_id']}: {r['status']}" for r in results)
    lines.append("")
    lines.append("## Input revision")
    lines.extend(f"- `{path}`: `{digest}`" for path, digest in report["input_sha256"].items())
    lines.append("")
    lines.append("## Blocking failures")
    lines.extend(f"- {x.get('category')}: {x.get('message')}" for x in blocking)
    lines.append("")
    lines.append("## Visual follow-ups")
    lines.extend(f"- {x}" for x in visual_followups)
    report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Render check verdict: {verdict}")
    print(f"Report: {report_path}")
    return 0 if verdict == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
