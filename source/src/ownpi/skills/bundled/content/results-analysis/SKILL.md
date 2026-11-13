---
name: results-analysis
description: Provides evidence-first methods for validating experiment data, designing comparisons, selecting statistical tests, estimating effects and uncertainty, creating scientific visualizations, and interpreting results without overstating evidence.
tags: [Research, Analysis, Statistics, Visualization, Scientific Reporting]
version: 0.2.0
---

# Results Analysis

Use evidence-first methods to analyze experimental results rigorously. The analysis should make clear what was measured, what was compared, what the data supports, and where uncertainty or missing evidence limits interpretation.

## Core Principles

1. **Validate the evidence before interpreting it.** Identify the available metrics, logs, tables, repeated runs, seeds, baselines, ablations, checkpoints, and evaluation metadata before drawing conclusions.
2. **Never fabricate statistics.** If sample size, seeds, raw metrics, or a valid comparison are missing, state the limitation clearly.
3. **Report complete statistics.** Do not report only best scores or only p-values.
4. **Separate evidence from interpretation.** Distinguish observed measurements, statistical results, supported claims, and hypotheses that require further checking.
5. **Prefer real scientific figures over figure specifications when the data is available.** Every important figure should support a concrete comparison or decision.

## Analysis Workflow

### 1. Inventory and Validate the Evidence

Identify:

- metric tables (`csv`, `json`, `tsv`, and equivalent structured data),
- training curves and checkpoints,
- seeds and repeated runs,
- baselines, ablations, and comparison families,
- evaluation protocol and metadata.

Validate:

- metric direction: whether higher or lower is better,
- unit of analysis: run, subject, fold, dataset, seed, trial, or another justified unit,
- number of runs or seeds,
- missing values, silent failures, and incomplete runs,
- comparability across methods, datasets, and evaluation conditions.

Do not treat repeated `subject × task` rows, folds, windows, trials, or seeds as independent units unless the experimental design justifies it. A `subject × task` summary table is usually a repeated-measure summary rather than an independent subject-level sample. If subjects have multiple task rows or missing task cells, state this before making significance or winner claims.

### 2. Define the Comparison Questions

Before running statistics, define the exact questions:

- Which method is compared to which baseline?
- What is the primary metric?
- What is the repeated-measure unit?
- Which ablation or robustness questions matter?
- Which findings would change a research decision?

Do not mix unrelated comparisons into one undifferentiated table.

### 3. Run Appropriate Statistics

Report, when justified:

- descriptive statistics such as `mean ± std`,
- `95% CI` or another clearly justified interval,
- run and seed counts,
- significance tests with assumptions stated,
- effect sizes,
- multiple-comparison handling when several contrasts are reported.

Check parametric assumptions first. Use a non-parametric fallback when assumptions fail, and state exactly what was tested and on which samples. Select the test according to the experimental design and unit of analysis rather than applying a default test mechanically.

Read the statistical references when the test choice, assumptions, or reporting standard requires more detail:

- `references/statistical-methods.md` for test selection and assumptions;
- `references/statistical-reporting.md` for minimum reporting standards.

### 4. Create and Examine Scientific Visualizations

Create real figures when the underlying data is available. A non-trivial analysis commonly benefits from a main comparison figure and a supporting figure for training dynamics, ablations, breakdowns, or error analysis, but the choice should follow the comparison questions and available evidence.

For every important figure, define:

- the purpose of the figure;
- the variables and comparison units being plotted;
- the meaning of error bars or uncertainty bands;
- the evidence needed for the caption;
- the exact observation the reader should notice;
- the interpretation and decision that the observation supports.

Read the visualization references when needed:

- `references/visualization-best-practices.md` for scientific figure design;
- `references/figure-interpretation.md` for evidence-based figure interpretation.

### 5. Interpret Results Without Overclaiming

For each important finding, record:

- the claim;
- the source evidence;
- the wording supported by the evidence;
- stronger wording that the evidence does not justify;
- uncertainty and limitations;
- the next check or decision that follows.

Do not turn a descriptive difference into a causal claim, a single best score into a general winner claim, or a statistically significant result into a practically meaningful improvement without supporting evidence.

Use `references/analysis-depth.md` when moving from an observation to a mechanism or research decision. Use `references/common-pitfalls.md` when checking for common analysis and interpretation failures.

## Quality Bar

Before considering an analysis complete, verify that:

- the primary comparison question is explicit;
- the analysis unit and sample or seed count are stated;
- inferential tests are justified;
- effect sizes and uncertainty are reported for major contrasts when appropriate;
- real figures exist when the data supports them;
- every important figure has an interpretation;
- limitations and blockers are explicit;
- supported claims preserve their evidence and uncertainty;
- stronger unsupported wording is excluded.

## Failure Conditions

When evidence is incomplete or invalid, do not replace it with confident prose:

- no seed-level data: descriptive summary only; inferential claims are blocked;
- no comparable baseline outputs: no significance or winner claim;
- unreadable or incomplete logs: training-dynamics conclusions are blocked;
- too few runs: effect-size estimates may be unstable and must be qualified;
- unclear unit of analysis: no winner claim or significance claim;
- contradictory statistics or provenance: quarantine the result until its source and interpretation are checked.
