# Render Check Entry Point

Call `run_render_checks.py` as the only model-facing entry point for deterministic checks of a rendered figure. Use the active Python interpreter selected for the current runtime; do not assume a system `python3`. The runner executes the available alignment, PDF text, and collision checks, classifies their findings, and writes one report directory. Do not call the individual checker scripts directly unless debugging the checker itself.

Example:

    <active-python> run_render_checks.py --workspace <workspace> --figure-name <figure_name>

The runner derives the figure directory as `<workspace>/output_synthesis/figures/<figure_name>/`, expects `<figure_name>.pdf` and `<figure_name>.alignment.json` there, and writes the canonical model-facing report to `<workspace>/output_synthesis/figures/<figure_name>/<figure_name>_script_check_report.md`. Detailed JSON results are written under the figure's `script_check/` directory. Verify that the report is present and matches the current export revision; a previous report cannot close the current check. It returns exit code 0 only when every requested check passes or is explicitly not auditable without a blocking input error.

Inputs are the real exported PDF and, when available, the alignment manifest. The runner does not judge scientific hierarchy, visual balance, salience, or aesthetic quality; those belong to visual inspection.

The rendered script-check gate is closed only when the current canonical report exists, matches the current export revision, and has final verdict `PASS`. Any other verdict, including `FAIL`, `BLOCKED`, or `NOT_AUDITABLE`, blocks Visual Inspection and returns the workflow to Implement.
