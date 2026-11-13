---
name: publication-figure
description: This skill should be used when the user asks to design, implement, check, revise, or deliver publication-quality scientific figures, including multi-panel figures, statistical plots, image panels, model comparisons, and paper-ready figure captions. It provides a mandatory design-to-delivery workflow with editable vector output, deterministic code checks, rendered visual inspection, and self-contained captions.
---

# Publication Figure

## STOP: This file is an execution controller, not the figure tutorial

### Do not draw from this file alone

Reading this file alone is insufficient to create or accept a figure. It only controls the workflow and routes the current action to the required reference. Do not substitute general plotting knowledge, a familiar template, or memory for the reference material. Every design decision, panel implementation, export, check, revision, and caption must be based on the applicable file under `reference/`.

### Work in short read-apply-record cycles

At the start of each action, identify the current stage and the exact next reference subsection. Read only that subsection, apply it immediately, and record the resulting decision, artifact, or check evidence before continuing. Do not read all references once and then work from memory. Re-read a subsection when the requirement is unclear, the context has been compressed, or a revision may have invalidated its previous result. If the applicable reference was not read for the action, that action is not compliant.

### Use closed stage gates

Each stage below has an entry condition, a next action, a required artifact, an exit condition, and a failure route. Attempting an action does not close a gate. Advance only when the named artifact or verdict proves the exit condition. A failure or uncertainty sends the work back through the stated route; preserve the failed evidence and rerun affected downstream checks after correction.

### Keep one active figure state

Choose one stable `figure_name` and work only in:

`<workspace>/output_synthesis/figures/<figure_name>/`

Maintain exactly one active version of each artifact. Mark superseded versions in an archive or remove them. Every artifact must identify its source revision and the next artifact it hands off to. Do not accept a figure because a script ran or files exist; accept it only when the final checklists and delivery artifact are closed.

## Start here

Before making a design decision, do these actions in order:

1. Choose `figure_name` and create `<workspace>/output_synthesis/figures/<figure_name>/`.
2. Read the minimum starting subsections: `reference/design/logical-design.md` sections 1, 3, and 5 for the figure claim, panel records, and structure; then `reference/design/aesthetic-design.md` sections 1, 2, and 3 for composition, visual language, and panel treatment. Read each subsection immediately before applying it.
3. Create `<figure_name>_logical_design.md`, `<figure_name>_aesthetic_design.md`, and `_workflow_state.md`.
4. Set `_workflow_state.md` to `DESIGN` and use the state format below.

Do not start implementation, write plotting code, or inspect complete examples during Start here. Those actions belong to later states.

## Execution State

At any moment, record the current state in the active artifact or working note:

`DESIGN` -> `IMPLEMENT` -> `CODE_CHECK` -> `RENDER_CHECK` -> `VISUAL_CHECK` -> `DELIVER`

Revision routes:

- `DESIGN` failure: stay in Design and revise the logical or aesthetic artifact.
- `IMPLEMENT` failure: return to Design for role/form/layout mismatches; otherwise stay in Implement.
- `CODE_CHECK` failure: return to Implement.
- `RENDER_CHECK` failure or unauditable result: return to Implement.
- `VISUAL_CHECK` failure: return to Implement for local rendering defects or Design for intended visual changes.
- Any revision invalidates affected downstream artifacts and requires the checks after that revision to be rerun.

The arrows describe an ordered entry point with revision loops, not a one-pass process. `DELIVER` is forbidden until every applicable gate is closed for the same final source revision.

Use this exact file and field format:

`<workspace>/output_synthesis/figures/<figure_name>/_workflow_state.md`

```text
Current state: DESIGN | IMPLEMENT | CODE_CHECK | RENDER_CHECK | VISUAL_CHECK | DELIVER
Source revision: <identifier or hash>
Required next reference: <exact reference path>#<subsection title>
Gate verdict: OPEN | PASS | FAIL | BLOCKED
Evidence: <artifact path, checklist row, or report path>
Last action: <one concrete completed action>
Next action: <one concrete action>
```

Update this file whenever the state changes, a gate fails, the source revision changes, or the required next reference changes.

Use a reproducible source revision. Prefer SHA-256 hashes of the active source script and input files; record the Git commit as an additional identifier when the source is tracked. A timestamp may be recorded for audit context but is not sufficient by itself. Store the same values in `revision.json` and reference that file from the workflow state and final manifest.

## Minimum artifact schemas

The reference files define the full schemas. These minimum fields are mandatory at the top level and must be visibly present in the corresponding artifact:

| Artifact | Minimum fields |
|---|---|
| Logical design | figure name/version; figure-level claim; evidence boundary; evidence chain; one complete record per panel; selected chart family and exact implementation reference path plus subsection title; structure; reading order; logical review verdict |
| Aesthetic design | figure name/version; consumed logical artifact/version; final physical size; composition; visual language; per-panel final form, geometry, color, labels, and acceptance criteria; exact implementation reference path plus subsection title; aesthetic review verdict |
| Implementation | source revision; input lineage; complete-example inspection record; one handoff row per panel; exact reference path plus subsection title; drawing function; parameter decisions; deviations; export policy; implementation verdict |
| Code check | source/input revision; every applicable checklist ID; status; evidence; corrective action/rerun; final verdict |
| Render check | current PDF/manifest revision; executed command/interpreter; every rendered check result; input hashes; final verdict |
| Visual check | current revision; viewed files and scales; every applicable VI item; status/evidence/action; iteration log; final verdict |
| Caption | final figure revision; caption text matching every panel and visible encoding; source/data boundary; delivery verdict |

For every panel handoff, the reference field must use the form `reference/<path>.md#<exact subsection title>`. A broad file name such as `chart-details.md` without a subsection title is incomplete.

## State 1: DESIGN

**Entry:** task and source evidence are available.

**Read now, then act:** read the exact subsection needed for the next design decision, record its path and title in the artifact, and apply it immediately. Logical design must cover the claim, panel records, structure, and review gate from `reference/design/logical-design.md`; aesthetic design must cover composition, visual language, panel treatment, and review gate from `reference/design/aesthetic-design.md`.

**Required artifacts:**

- `<workspace>/output_synthesis/figures/<figure_name>/<figure_name>_logical_design.md`
- `<workspace>/output_synthesis/figures/<figure_name>/<figure_name>_aesthetic_design.md`

**Exit:** both artifacts contain their required fields, use one active version, agree on claim, evidence, panel roles, chart families, composition, visual language, and final size, and their review gates pass.

**Failure route:** revise the relevant design artifact. Do not invent panel structure while implementing.

## State 2: IMPLEMENT

**Entry:** Design exit is closed.

**Read now, then act:** read the applicable subsection of `reference/implement/global-settings.md`, `reference/implement/chart-details.md`, and `reference/implement/composition-layout.md`. Inspect `reference/implement/complete-examples/`; read a matching example immediately before adapting it. For each panel, read its named subsection from the active design artifacts, then read the matching implementation subsection, record the decision, implement that panel, and verify the handoff before starting the next panel.

**Required artifacts:**

- one active source script under the figure directory;
- `<figure_name>_implementation.md`, including one row per panel:
  `Panel | logical task | selected chart family | exact reference path#subsection title | drawing function | required encoding`;
- source, input reference, and SVG/PDF/PNG exports sharing one figure prefix.

The implementation artifact must also contain a complete-example inspection table:

`Example path | inspected sections | MATCHED or NOT MATCHED | adopted or rejected | reason`

`reference/implement/chart-details.md` alone is not a sufficient panel reference. Each panel row must name one exact file path and one subsection title. If one panel needs multiple references, list each exact path and title in the same row.

**Exit:** every panel maps from logical role to aesthetic form to implementation function; the active script preserves the selected chart families; global settings and export requirements are applied; no unresolved handoff exists.

**Failure route:** return to Design for logical or visual-form mismatch; otherwise remain in Implement and correct the source or handoff.

## State 3: CODE_CHECK

**Entry:** Implement exit is closed for the current source revision.

**Read now, then act:** read `reference/implement/code-check.md`. Create its complete checklist before checking. Execute one applicable item, record status and evidence, fix failures, and rerun affected items.

**Required artifact:** `<workspace>/output_synthesis/figures/<figure_name>/<figure_name>_code_check.md`.

**Exit:** every applicable item is `PASS` or justified `NOT APPLICABLE`; no `TODO`, `FAIL`, or unexplained `WARNING` remains; final verdict is `PASS`.

**Failure route:** return to Implement. Do not run the rendered-check runner as a substitute for this source/input checklist.

## State 4: RENDER_CHECK

**Entry:** Code Check is `PASS` for the current source revision.

**Read now, then act:** read `reference/visual-check/script-check/README.md`. Use the active interpreter to run the single documented entry point:

`<active-python> reference/visual-check/script-check/run_render_checks.py --workspace <workspace> --figure-name <figure_name>`

**Required artifact:** `<workspace>/output_synthesis/figures/<figure_name>/<figure_name>_script_check_report.md`, matching the current PDF and alignment manifest.

**Exit:** the report exists, is auditable and current, and its final verdict is `PASS`.

**Failure route:** return to Implement, regenerate the exports, and rerun this gate. Do not create a visual `PASS` while this gate is missing, stale, failed, or unauditable.

## State 5: VISUAL_CHECK

**Entry:** Render Check is `PASS` for the current export revision.

**Read now, then act:** read the relevant subsection of `reference/visual-check/visual-inspection/visual-check.md`. Create the complete visual checklist with every item initially `TODO`. Inspect the actual exported figure at final placement scale, the PDF-derived preview, and required crops or accessibility views. Record each result immediately after inspection.

**Required artifact:** `<workspace>/output_synthesis/figures/<figure_name>/<figure_name>_visual_check.md`.

**Exit:** every applicable item is `PASS` or justified `NOT APPLICABLE`; no `TODO`, `FAIL`, or `WARNING` remains; the checklist identifies the final revision and says `READY_FOR_DELIVERY`.

**Failure route:** return to Implement for local rendering defects or Design for intended changes to composition, typography, palette, or form. After any correction, rerun Code Check, Render Check, and affected visual inspection.

## State 6: DELIVER

**Entry:** Code Check, Render Check, and Visual Check are closed for the same final revision.

**Read now, then act:** read the applicable subsection of `reference/delivery/caption-writing.md`. Write the caption incrementally while reading its sections; do not write it from memory.

**Required artifact:** `<workspace>/output_synthesis/figures/<figure_name>/<figure_name>_caption.md`.

**Exit:** deliver the paper-ready PDF and caption, while preserving the SVG, PNG, source, input reference, metadata, and QA records. Confirm that the caption and all outputs correspond to the final revision.

## Final artifact manifest

Before `DELIVER`, verify this manifest against one source revision:

| Required path | Must prove |
|---|---|
| `<figure_name>_logical_design.md` | logical design gate is closed |
| `<figure_name>_aesthetic_design.md` | aesthetic design consumes the active logical version |
| `<figure_name>_implementation.md` | every panel has an exact reference handoff and complete-example inspection record |
| `<figure_name>.py` or the declared active source | source revision used for all checks and exports |
| `<figure_name>_code_check.md` | all applicable code checks are closed |
| `<figure_name>_script_check_report.md` | rendered checks ran on the current PDF/manifest and verdict is `PASS` |
| `<figure_name>_visual_check.md` | all visual checks are closed with `READY_FOR_DELIVERY` |
| `<figure_name>_caption.md` | caption matches the final visible figure |
| `<figure_name>.svg` | editable vector export |
| `<figure_name>.pdf` | paper-ready rendered export |
| `<figure_name>.png` | raster preview/export |
| `_workflow_state.md` | final state is `DELIVER`, source revision is current, and evidence paths are listed |
| `revision.json` | source and input hashes, export hashes, and optional Git commit |
| `manifest_validation.json` | successful machine validation of this manifest |

The manifest proves artifact closure, not scientific completeness. Before delivery, recheck the logical review gate against the final figure. When delivering a figure set, also check coverage of its planned main findings and repeated chart families; resolve omitted important evidence or justify repetition from the sources. Keep this content review in the existing logical design records, separate from rendered visual checks. Every required artifact must remain present, current, and auditable.

Run the bundled validator before declaring closure:

`<active-python> reference/validate_manifest.py --workspace <workspace> --figure-name <figure_name>`

The validator checks the required paths, the `DELIVER` workflow state, required report verdicts, and revision metadata. Preserve its machine-readable result at `<workspace>/output_synthesis/figures/<figure_name>/manifest_validation.json`. A nonzero exit code blocks delivery.
