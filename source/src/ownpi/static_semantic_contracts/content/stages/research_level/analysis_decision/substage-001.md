# Research View Building Workflow Contract

This contract governs the first block of Research-Level Analysis Decision. Its purpose is to preserve and organize the research record for research-logic reconstruction, scientific audit and routing, and subsequent paper planning.

Before writing any durable artifact, create `<workspace>/analysis_decision/` if it does not exist. Store this work, including its Durable Planning System, under `<workspace>/analysis_decision/research_view/`.

## Core Rule

Build a faithful, evidence-linked research view before making paper-selection or narrative decisions. Do not replace the underlying implementation record with a summary. Do not infer missing values, statuses, statistical results, or evidence from filenames or from an aggregate conclusion.

## Research View Structure

On first entry, create this complete skeleton under `<workspace>/analysis_decision/research_view/`:

```text
research_view/
├── README.md                         # audit scope, source roots, progress, and directory map
├── research_inventory/               # one record for every research direction
│   └── <direction_id>.md
├── evidence_records/                 # one record for every material experiment
│   └── <experiment_id>.md
├── quantitative_records/             # one record for every quantitative result
│   └── <result_id>.md
└── detailed_research_view.md         # integrated navigation and faithful research view
```

On first entry, create all listed directories and files before beginning the audit. On subsequent entries, retain stable IDs and still-valid verified records; do not clear or rebuild the view by default. Use stable lowercase kebab-case identifiers. Do not add separate `figure_data/` or `table_data/` directories; figures and tables consume source-linked records from `quantitative_records/`.

## Required Source Inspection

Read the Raw User Message and Task Spec first. Then locate the established research artifacts within `<workspace>`, including the Research Contract, Method Tree, Resource Basis, Experiment Framework, and the source records from the preceding research stages.

On first entry, perform a directory-level audit of `implementation_execution/`. Inspect every experiment record, execution, configuration, manifest, raw result, statistical result, analysis, audit, diagnostic, revision, and decision that is relevant to the research. Follow each locator to the underlying artifact. An implementation summary, if present, is only an index and never a substitute for this inspection.

On subsequent entries, inspect new, changed, deleted, invalidated, and superseded sources, and earlier records affected by dependency changes or newly discovered errors. Do not determine impact solely from new files or modification times. Reuse unaffected verified sources without rereading them; trace ambiguities or contradictions to their sources and expand reinspection when necessary.

## Audit Unit

Process the research in bounded units so that the audit can continue reliably over a long task. For each research direction, then for each experiment and execution within that direction:

1. Inspect the source artifacts and their relationships.
2. Record what was actually done, its purpose, inputs, configuration, status, and evidence locators.
3. Record raw quantitative evidence, derived statistics, uncertainty, sample definitions, comparisons, and significance results whenever available.
4. Record the scientific finding, its scope and relationship to the research question.
5. Mark the unit as audited before moving to the next unit.

Before batch-producing or revising records, validate a representative record for each distinct source or schema, including field meanings and evidence locators. Then apply the verified approach and check the full affected set. Keep validation read-only.

Include successful, failed, partial, diagnostic-only, rejected, retired, superseded, and unresolved work. Status labels never replace the underlying finding or evidence.

## Durable Research View

Build the initial view in this order. On subsequent entries, update affected inventory, evidence, quantitative records, and integrated view together; preserve historical attempts and their statuses, including failed and negative results. Keep `README.md` current throughout the audit; record scope, source roots, completed and remaining units, directory map, this entry's changes, reused records, reinspection scope, and unresolved issues.

### Research Inventory

For each research direction, create `research_inventory/<direction_id>.md`. Record its purpose, linked Research Contract and Method Tree elements, related experiments, current audit status, and open coverage gaps. This file indexes the direction; it does not replace its evidence records.

### Evidence Records

For each material experiment and execution, create or update `evidence_records/<experiment_id>.md`. Record what was actually done, purpose, inputs, configuration, execution variants, status, raw-result locators, analysis and audit locators, scientific findings, scope, limitations, and revision or parent relationships. Include successful, failed, partial, diagnostic-only, rejected, retired, superseded, and unresolved work. Status labels never replace the underlying finding or evidence.

### Quantitative Records

For each quantitative result, create `quantitative_records/<result_id>.md`. This is the single quantitative evidence layer for Results, figures, and tables. Preserve source-linked values at the finest available granularity needed to recompute or verify summaries; do not create separate figure or table data copies at this stage.

Each `quantitative_records/<result_id>.md` must contain this minimum schema:

```text
Result ID:
Experiment and execution IDs:
Research question:
Source data locator:
Raw result locator:
Data unit and granularity:
Sample definition and sample size:
Conditions and comparison groups:
Derived values and computation:
Statistical test and assumptions:
Effect size and uncertainty:
Significance result:
Figure and table use:
Limitations and exclusions:
Verification status:
```

Keep the finest available granularity needed to recompute or verify every reported value. Record both source values and derived values, and identify the transformation between them. `Figure and table use` records suitable variables, groups, panels, or columns and their source locators; it does not decide the final paper layout.

Every record must link to its real source artifact. Use exact paths or stable locators and distinguish source values from derived values. Preserve the source precision, sample scope, analysis method, and uncertainty required for later reporting.

### Integrated View

Update `detailed_research_view.md` only from the completed inventory, evidence records, and quantitative records. Use it as the integrated navigation layer and faithful overview; it is not the sole evidence store and must link to the detailed records below it.

## Completion Gate

Before completing this block:

- Reconcile `research_inventory/` against the Research Contract, Method Tree, Resource Basis, Experiment Framework, and the complete `implementation_execution/` tree.
- Confirm that every material experiment, execution, result, analysis, diagnostic, revision, and decision has an evidence record or an explicit exclusion reason.
- Confirm that all quantitative records identify their source, granularity, sample scope, computation or statistical procedure, and limitations.
- Confirm that the entire research view is currently valid, complete, and faithful. Full coverage reconciliation does not require rebuilding or rereading unaffected sources.
- Make no paper-selection, layout, research-maturity, or routing decision in this block.
