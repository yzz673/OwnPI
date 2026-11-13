# Research-Level Implementation Execution Contract

## Introduction

Implementation Execution is the core and most work-intensive stage of the research process and the primary source of substantive research output. This stage requires sustained, long-duration work and broad exploration; follow every requirement in this Contract continuously throughout the stage. Its goal is to produce a rich body of high-quality scientific results through continuous experimentation, exploration, optimization, empirical investigation, iterative analysis, and refinement, pursuing the strongest feasible evidence rather than stopping at the first workable result.

Before writing any durable artifact, create `<workspace>/implementation_execution/` if it does not exist. Keep all durable artifacts owned by this stage, including its Durable Planning System, in that directory. Store large data, models, and remote execution artifacts in their designated durable locations and record their exact locators in the relevant implementation records.

## What To Do

### Produce Concrete Research Outcomes

Search within `<workspace>` for the Method Tree, Experiment Framework, and Resource Basis. Use them with the complete current-task context to instantiate the experimental directions, verify the relevant resources, and respect their recorded limitations.

When revisiting this stage, use the feedback and existing implementation records to continue from valid code, experiments, and results rather than starting over. Revise affected work and pursue remaining worthwhile investigations; repeat completed experiments only for a new scientific reason or a specific unresolved defect, not merely because their results were negative or null.

For every experimental direction in the Experiment Framework, repeat the following cycle:

1. **Experiment:** Design and run concrete experiments, obtain results, and review what the results mean.
2. **Revise:** When a result is unclear or does not provide the expected information, revise the concrete experiment and run it again. Revisions may include changing or improving the model, choosing a more appropriate metric, adjusting the experimental setting, improving training, or changing the statistical or evaluation procedure. These are examples rather than an exhaustive list: revisions are deliberately flexible, and Implementation may try any scientifically justified adjustment or optimization that could improve feasibility, validity, interpretability, or evidence quality.
3. **Explore:** Explore deeply and broadly, including divergent experiments, alternative methods, improvements, and extensions. Even after success, pursue scientifically meaningful, feasible avenues until they are substantially explored and further work is unlikely to add substantive scientific insight or stronger evidence.
4. **Conclude:** The direction must produce at least one clear and stable conclusion supported by sufficient evidence. The direction may also produce additional bounded or limited conclusions, but a bounded or limited conclusion does not by itself satisfy the stable-conclusion requirement; an unresolved possible conclusion is not sufficient.

### Working Practice

#### Durable Work

NOTE: Always use the structure below for `<workspace>/implementation_execution/`. Every listed directory and file is required. Before implementation starts, create this directory structure backbone in the workspace.

```text
implementation_execution/
├── README.md                         # stage index and directory map
├── plan/                             # active implementation planning system
│   └── <plan_id>.md
├── src/                              # implementation source tree
│   ├── code/                         # implementation source code
│   ├── tests/                        # test scripts
│   └── configs/                      # reusable configuration fragments
└── experiment_records/               # records for all concrete experiments
    └── <experiment_id>/              # one bounded investigation
        ├── README.md                 # experiment index and current status
        ├── metadata.md               # purpose, inputs, target, owner, and scope
        ├── executions/               # runs and configuration variants
        │   └── <execution_id>/       # one concrete execution
        │       ├── README.md         # command, configuration, status, and locators
        │       ├── command.txt       # exact execution command or procedure
        │       ├── config/           # immutable resolved configuration
        │       ├── manifest/         # environment and input manifest
        │       └── logs/             # execution logs
        ├── raw_results/              # raw and machine-readable outputs
        ├── statistics_and_analysis/  # results derived from raw_results/
        ├── audits/                   # validation and diagnostic records
        └── decision.md               # accepted, rejected, or unresolved outcome
```

Naming: `<plan_id>`, `<experiment_id>`, and `<execution_id>` use lowercase kebab-case with stable descriptive names. Use one experiment directory per purpose/input/evaluation/evidence target; use one execution directory per run or configuration variant. Record revisions through `parent_id` and `revision_reason` in the relevant README or metadata; never overwrite prior execution results.

Placement: implementation code, tests, and reusable configuration go under `src/code/`, `src/tests/`, and `src/configs/`. Experiment-specific execution records go under `experiment_records/<experiment_id>/executions/<execution_id>/`. Raw outputs go under that experiment's `raw_results/`; derived statistical results and analyses go under `statistics_and_analysis/`; validation and diagnostic records go under `audits/`. Large or remote artifacts stay in their designated durable locations, with exact locators recorded in the relevant `manifest/` and README. Failed, rejected, diagnostic-only, retired, and superseded artifacts remain in place with an explicit status.

Record updates immediately in the nearest README or metadata file. Each execution README must identify its command, resolved configuration, manifest, logs, and raw-result locator. Each experiment README must identify its metadata, executions, raw results, analyses, audits, and decision. Keep `README.md`, `plan/`, `metadata.md`, `decision.md`, manifests, and directory contents consistent. The directory structure and explicit locators, rather than a retrospective aggregate map, are the durable record.

If Git is available, maintain a local-only repository rooted at `implementation_execution/`, reusing it on revisits; do not configure a remote or push. Track code, tests, configurations, plans, experiment records, manifests, and small result tables; commit coherent changes at meaningful experiment milestones. Use `.gitignore` to exclude credentials, datasets, model weights/checkpoints, environments, caches, temporary files, bulky logs, and large generated arrays, figures, or archives. Retain required artifacts and their locators even when ignored; Git history supplements the research records rather than replacing them.

#### Speed Up

- Implementation involves a large and broad workload. Advance independent lines of work in parallel when practical instead of waiting for one local experiment to finish before starting unrelated work. When subagents are available, actively use them to delegate independent work.
- When designing a concrete experiment, use a small-scale pilot to assess feasibility quickly before committing to a formal run. A successful pilot can justify proceeding to the formal experiment, but a failed small-scale pilot does not by itself establish that the concrete experiment is infeasible. Determine whether the failure is caused by insufficient scale; when that explanation is plausible and can be tested, adjust the scale or other concrete settings and continue the investigation.

#### Scientific Judgment and Boundaries

- Preserve the Research Main Line and the scientific intent of the Method Tree and Experiment Framework. Do not treat execution difficulty as permission to lower the research objective or replace substantive work with an easier proxy.
- An evidence-supported conclusion on one suitable resource is a valid scoped finding. Resource quality may differ substantially, and a weak or null result from one resource does not by itself downgrade conclusions supported by better resources. First exclude implementation, preprocessing, alignment, protocol, and other non-resource explanations; when the limitation is attributable to the resource itself, it may be excluded without requiring every available resource to support the same result.
- Downgrade a conclusion only when an unresolved flaw in the experimental design or evaluation or inferential procedure prevents the evidence from supporting a reliable conclusion. A negative, heterogeneous, dataset-specific, adaptively developed, or not-yet-cross-resource-replicated result is not by itself exploratory. Investigate it sufficiently and state the stable conclusion supported by the evidence.
- Devote the large majority of this stage to the research itself. During active experimentation, perform only the validation, provenance, and reproducibility work needed to keep the experiments correct, interpretable, and recoverable. Complete comprehensive validation, provenance closure, reproducibility audits, and packaging after the substantive research outcomes are largely complete; do not let this supporting work displace the research.

## What Not To Do

- Do not end the stage merely because results are acceptable or one local task is complete. Every direction must meet both the stable-conclusion and exploration requirements above: reasonable deeper and broader investigations must be substantially explored, with little expected scientific gain from continuing. An unresolved possibility is not a completed direction.
- Do not abandon a route as soon as it becomes difficult. Explore it deeply, try broadly, and solve problems flexibly before concluding that it cannot proceed.
- Do not write the paper or produce final publication figures during this stage.
