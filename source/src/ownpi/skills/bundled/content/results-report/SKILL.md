---
name: results-report
description: Use when completed experimental evidence needs to be synthesized into a structured, decision-oriented research report with findings, limitations, and next actions.
version: 0.1.0
tags: [Research, Reporting, Experiments]
---

# Results Report

Write a **complete post-experiment summary report** from the available experimental evidence.

Use this skill after the relevant results have been analyzed, whether the evidence comes from `results-analysis`, existing notes, logs, tables, figures, or other validated artifacts.

## Role boundary

### `results-analysis` does
- evidence validation,
- statistical analysis,
- scientific visualization,
- evidence-bounded interpretation.

### `results-report` does
- complete experiment summary,
- decision-oriented narrative,
- figure-by-figure interpretation inside a coherent structure,
- limitations, failure cases, and next actions,
- a durable internal report artifact.

When the task is to create or redesign paper-ready figures themselves, rely on `publication-figure` instead of expanding `results-report` into figure production.

Do not replace missing or weak evidence with confident prose. State which conclusions are supported, which remain uncertain, and which evidence is missing.

## Default output

The default report is an **internal research report**, not manuscript prose.

Use a clear, searchable filename that identifies the experiment and purpose. Include date and round when they are available:

```text
YYYY-MM-DD--{experiment-line}--r{round}--{purpose}.md
```

Example:
- `2026-03-18--freezing--r03--transfer-summary.md`
- `2026-03-18--contrastive-adversarial--r02--ablation-report.md`

Use a title that identifies the experiment, round, purpose, and date when those fields are available:

```text
{Experiment Line} / Round {N} / {Purpose} / {YYYY-MM-DD}
```

Read `references/report-naming.md` before finalizing the filename or note title.

## Experiment metadata

Record the experiment identity, date, round, purpose, and source evidence when those fields are available. Do not invent missing metadata.

## Default report structure

Use the following sections when they are relevant to the experiment:

1. **Executive Summary**
2. **Experiment Identity and Decision Context**
3. **Setup and Evaluation Protocol**
4. **Main Findings**
5. **Statistical Validation**
6. **Figure-by-Figure Interpretation**
7. **Failure Cases / Negative Results / Limitations**
8. **What Changed Our Belief**
9. **Next Actions**
10. **Artifact and Reproducibility Index**

Read `references/report-structure.md` before writing.

## Workflow

### 1. Confirm the report scope

Identify these fields first when available:
- date,
- experiment line,
- round,
- purpose,
- linked experiment note,
- linked durable result note if one already exists.

If a field is unknown, state that it is unavailable instead of inventing it.

### 2. Read the available evidence

Read the available analysis notes, tables, logs, figures, provenance, and other validated artifacts.

When analysis is incomplete, preserve the limitation in the report and avoid claims that the evidence cannot support.

### 3. Write the report as a decision object

This report is not a transcript of outputs.

Each section must answer a real question:
- What did we test?
- What changed numerically?
- What is actually supported?
- What failed or remains uncertain?
- What should we do next?

Read `references/decision-oriented-analysis.md` for the expected reasoning depth.

### 4. Interpret figures inside the report

Do not only attach figures.

For each main figure:
- introduce why it is included,
- state the key observation,
- explain the supported interpretation,
- explain the decision implication.

Read `references/figure-interpretation.md` and `references/statistical-completeness.md` as needed.

### 5. Choose the report target explicitly

Write the report to the output location defined by the current task. Do not invent an external knowledge-base destination.

### 6. End with explicit next actions

The report must end with operational decisions, for example:
- stop a weak branch,
- schedule one missing ablation,
- promote a stable finding into manuscript-facing writing,
- update the active plan.

## Required quality bar

- The report should be dateable, searchable, and attributable to an experiment line and round when those fields are available.
- The report must cite actual evidence from the available artifacts.
- The report must include negative results when they matter.
- The report must separate stable conclusion from tentative interpretation.
- The report must say what changed in project belief and what should happen next.
- The report must preserve Claim Candidate wording and must not promote `speculative` or `observed` claims into decisive conclusions.

## Reference files

Load only what is needed:
- `references/report-structure.md`
- `references/report-naming.md`
- `references/figure-interpretation.md`
- `references/statistical-completeness.md`
- `references/decision-oriented-analysis.md`
- `references/EVIDENCE-PROPAGATION.md`
- `examples/example-results-report.md`
