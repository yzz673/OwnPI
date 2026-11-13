# Paper Architecture and Evidence Planning Workflow Contract

This contract governs the first workflow block of Research-Level Output Synthesis. Its purpose is to transform the complete Detailed Research View into a paper architecture and evidence plan whose sections, figures, tables, and Supplementary material are ready for the next block to consume.

The primary deliverable of this block is the set of complete, source-linked evidence packages. The paper architecture is not complete until every planned section, figure, table, and Supplementary item has an evidence package that can be consumed directly. The next block must be able to begin writing, drawing, or constructing a table from these packages without searching the research record, recomputing statistics, locating raw data, or guessing what content is required.

## Core Rule

Build the paper story at the macro level and prepare the evidence at the delivery-unit level. Do not write manuscript prose or design figure panels in this block. Do not leave the next block to search for data, recompute statistics, identify tests, or infer what a figure should show.

Before writing any durable artifact, create `<workspace>/output_synthesis/` if it does not exist.

## Framework Structure

Create `<workspace>/output_synthesis/paper_architecture_and_evidence_plan/` with this structure before planning:

```text
paper_architecture_and_evidence_plan/
├── README.md                         # framework index and planning status
├── paper_story.md                    # central question, contribution, and scientific line
├── section_plan/
│   └── <section_id>.md               # purpose and evidence package for each section
├── figure_plan/
│   └── <figure_id>.md                # results to present and complete evidence package
├── table_plan/
│   └── <table_id>.md                 # quantities, comparisons, and evidence package
├── supplementary_plan/
│   └── <item_id>.md                  # supporting content and evidence
└── coverage_audit.md                 # research-view coverage and selection decisions
```

## Planning Procedure

Consume `<workspace>/analysis_decision/research_view/` and `<workspace>/analysis_decision/research_logic/` as the research record and action-line inputs, including their source maps and open questions. Use the Analysis Memo for audit context where helpful. Do not rebuild the research view; collect and bind the evidence needed for downstream packages. Keep unverified information and unresolved issues explicit rather than filling gaps by inference.

First read `academic-paper-writing` with `skills_read(package="academic-paper-writing")` and select the applicable writing profile. Then perform the planning below according to the skill's Universal Writing Principles and the selected profile's guidance on manuscript content, figures, and tables.

1. Establish the central contribution, scientific question, and story line from the actual findings and their relationships.
2. Define the macro role and order of the Introduction, Methods, Results, Discussion, figures, tables, and Supplementary material.
3. Create one `section_plan` record for every planned section or subsection. Do not write the section; specify its purpose, claims, result order, required numerical values, statistical tests, comparisons, sample scope, uncertainty, figure or table references, evidence locators, and limitations.
4. Create one `figure_plan` record for every planned figure. Specify the scientific purpose, claim or research question, and the results the figure must present. Bind the relevant experiments, executions, raw data, quantitative records, numerical values, statistical tests, comparisons, sample scope, uncertainty, limitations, and source locators. Do not prescribe panel count, panel arrangement, chart type, visual language, or implementation.
5. Create one `table_plan` record for every planned table with the quantities, comparison groups, sample scope, uncertainty, statistical reporting, source locators, and intended scientific purpose.
6. Create one `supplementary_plan` record for every supporting item and link it to its source evidence and the main-text content it supports.

Every `section_plan`, `figure_plan`, `table_plan`, and `supplementary_plan` file is an evidence package, not merely an outline entry. A plan file without complete evidence locators, required values, and reporting context is incomplete and must remain in this block until those materials are assembled or the item is explicitly marked unresolved.

Quantitative results are core empirical content. Preserve their complete evidence and required granularity rather than reducing them to qualitative summaries or status labels. Use Supplementary material for supporting detail instead of discarding valuable results merely to simplify the main narrative.

Main-text tables are not a default deliverable. Plan a main-text table only when the selected writing profile requires it or when a clear scientific evidence need justifies it. Assign detailed numerical content to Supplementary tables when appropriate, and do not create a table merely to satisfy a generic paper structure.

Select or omit content only after considering the full research view. For every material finding omitted, record a justification based on relevance, redundancy, or evidence quality. Do not omit valuable work merely because it is inconvenient to write or visualize. Mentioning an experiment's limitation does not present its findings.

## Completion Gate

Consider this block complete only when all of the following requirements have been fully satisfied:

* The plan must comply with the Universal Writing Principles and the selected writing profile in `academic-paper-writing`. Compliance means that the planned architecture and content satisfy the skill's requirements; it does not require drafting the manuscript or producing the figures and tables at this stage. The block is incomplete if any planned figure, table, or section conflicts with those requirements.

- every planned section, figure, table, and Supplementary item has a complete evidence package;
- every package identifies its source artifacts, required values, statistical context, sample scope, uncertainty, limitations, and exact locators;
- every planned figure identifies the results to present and can be handed to the relevant figure Skill without additional research or statistical computation;
- every planned table has the values and comparisons needed for direct construction;
- every planned section has the numerical and statistical evidence needed for direct manuscript drafting;
- every material research finding is included in a plan or has a recorded omission reason;
- the framework preserves completed, incomplete, failed, diagnostic, bounded, and limited work according to its evidence;

Persist the plan in `<workspace>/output_synthesis/paper_architecture_and_evidence_plan/`. The narrative architecture remains macro, but its evidence packages must be sufficiently detailed for direct downstream consumption.

