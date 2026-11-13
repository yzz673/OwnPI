# Logical Design

Logical Design determines what the figure must communicate and why each panel is needed. Complete it before choosing final colors, typography, detailed layout, or plotting code.

The completed design must be saved as:
```text
`<workspace>/output_synthesis/figures/<figure_name>/<figure_name>_logical_design.md`
```

Later Design, Implement, and Check work must read and follow this file. It is the durable handoff between stages; do not rely on conversation memory to carry the figure plan forward.

## 1. Define the figure claim

Start from the scientific question rather than from a table, metric, or favorite chart type. Complete:
```text
Results-level question:
Figure-level claim:
Evidence boundary:
What could overturn the claim?
Decisive evidence:
```

Write the figure-level claim as one sentence with a verb. State the most conservative claim supported by the available evidence. Record what the figure does not establish so that visual emphasis does not imply a stronger conclusion.

### How to reason well

Start with the Results paragraph or scientific question that the figure must support. Replace descriptions such as `model results` with a falsifiable sentence that names the comparison and outcome. Ask what observation would weaken the claim, then make that observation part of the evidence plan. Do not derive the claim from the number of available tables or from a preferred chart.

Weak: `The model has good performance.`

Stronger: `The contextual model improves held-out target prediction over the matched baseline while the gain is reduced under the pre-word control.`

## 2. Build the evidence chain

Arrange the necessary evidence in the order the reader needs it. Use only the roles required by the claim:

- setup or experimental design;
- representative example;
- primary quantitative evidence;
- baseline or control;
- decomposition or attribution;
- stratification or subgroup comparison;
- orthogonal validation;
- perturbation or stress test;
- boundary or failure case;
- mechanistic evidence.

Describe the chain explicitly:
```text
evidence step 1 -> evidence step 2 -> evidence step 3 -> qualification or boundary
```

The chain may establish, compare, discriminate, broaden, and bound a claim. Do not force every figure to contain all of these steps. A panel belongs in the main figure when removing it removes a necessary inference, a conclusion- changing control, or an important limitation. Route supporting detail, alternative estimators, secondary metrics, and non-decisive robustness to Extended Data or Supplementary Information when appropriate.

### How to reason well

Write the evidence as a sequence of questions, not a list of datasets. A useful sequence is `define -> establish -> compare/control -> stress-test -> bound`. For each step, ask what the reader can conclude after seeing it and what the next step adds. If two steps lead to the same conclusion, merge them or route the weaker one to supporting material. A second metric, estimator, seed, or subgroup does not automatically create a new logical role.

## 3. Assign panel roles and expression tasks

For every proposed panel, complete this record. Every field is required; do not replace the record with a prose summary:
```text
Panel:
Panel question:
Evidence role:
Required comparison:
Data unit and sample definition:
Statistic and uncertainty:
Recommended expression task:
Selected chart family:
Implementation reference subsection:
Why this panel is necessary:
Destination: main figure | Extended Data | Supplementary Information | omit
```

`Recommended expression task` describes what the panel must make visible. Use terms such as comparison of centers and intervals, distribution and outliers, paired change, temporal trend, relationship between variables, spatial or matrix structure, composition, representative image, or workflow and information flow.

This is the point at which a broad chart family may be recommended, but do not yet lock the final visual treatment. For example:
```text
Recommended expression task: compare matched effects with uncertainty
Possible chart families: point-and-interval plot or paired dot plot
Reason: the reader must see direction, magnitude, and uncertainty together
```

Before handing the design to implementation, select one primary chart family for every panel and record it explicitly as `Selected chart family`. Also record the reference subsection that implementation must use. A broad expression task or a list of possible chart families is not an implementation handoff.

Before choosing panels, briefly inventory relevant evidence forms: summaries, individual or paired observations, distributions, temporal or spatial structure, and representative examples. Check linked sources before treating missing detail in a summary package as unavailable data. Choose expression tasks from evidence roles; do not hide important structure behind aggregate estimates or choose charts merely from a table's format.

Seek complementary chart families and modalities across panels, each answering a distinct necessary question. Do not add types merely for variety or invent observations. A uniform-chart exception must identify relevant alternatives checked, their sources, and why they lack data or add no useful evidence; route it for design review. A narrow selection of summary rows alone does not justify the exception.

### How to choose the expression task

First identify the information the reader must recover, then choose a chart family that preserves it:

| Required information | Suitable expression families |
|---|---|
| centers, direction, and intervals | point-and-interval or forest plot |
| matched changes between two conditions | paired dots or slope plot |
| distribution, spread, and outliers | jitter, box, violin, or density plot |
| temporal or ordered change | line plot with uncertainty |
| relationship between two variables | scatter plot with an explicitly justified fit |
| matrix or spatial pattern | heatmap or image panel |
| composition across categories | grouped, stacked, or area plot |
| workflow, mechanism, or information flow | schematic or diagram |

These are recommendations, not automatic templates. Preserve the replicate unit, pairing, uncertainty, and denominator. For example, use a paired expression for matched subjects even when a grouped bar chart would be easier to draw. Prefer a distribution view when a mean-only plot would hide heterogeneity.

## 4. Check evidence and data boundaries

Before handing the plan to Aesthetic Design, verify:

- every panel uses an identified source and real observations or declared   summaries;
- sample size and replicate unit are defined;
- paired or matched structure is preserved when relevant;
- center, spread, interval, test, and correction are identified when applicable;
- comparisons use compatible denominators, cohorts, estimands, and scales;
- unsupported fits, missing intervals, and failed analyses remain visible;
- no panel implies causality, mechanism, generality, or significance beyond the   evidence;
- no panel duplicates another panel without adding a distinct inference;
- excluded material has a scientific reason and a recorded destination.

### How to perform the boundary check

Trace every plotted quantity to a source row, analysis output, or explicitly declared summary. Write down the unit of replication before choosing the visual summary. Compare denominators and estimands panel by panel. Mark unsupported results as unsupported; do not fill missing intervals with invented uncertainty. Use the question “what stronger statement could a reader infer from this panel?” to catch accidental claims about causality, generality, or significance.

## 5. Select the logical figure structure

Select a structure based on the evidence chain, not on a fixed panel count:

- `quantitative grid` for mainly numerical comparisons with compatible panels;
- `schematic-led composite` when readers need a system, method, or experiment   definition before interpreting quantitative evidence;
- `image plate + quantification` when representative or spatial images lead and   measurements validate them;
- `asymmetric mixed-modality` when one evidence role is substantially more   important or different modalities must be coordinated.

Record:
```text
Figure name:
Active design version:
Selected structure:
Reason:
Reading order:
Primary panel or evidence:
Supporting panels:
Main figure / Extended Data / SI decisions:
```

There must be exactly one active logical design and one active aesthetic design for a figure. If an earlier artifact exists, mark it as superseded or remove it before implementation. Implementation must consume the artifacts named by `Figure name` and `Active design version`.

The selected structure is a logical handoff to Aesthetic Design. It does not require a schematic, an asymmetric layout, or a particular number of panels when the evidence does not call for them.

### How to select the structure

Map the evidence chain onto a rough reading path before naming the structure. Use `quantitative grid` when panels are comparable and share a numerical argument; use `schematic-led composite` when readers must understand a system or workflow first; use `image plate + quantification` when an image is the evidence object and measurements validate it; use `asymmetric mixed-modality` when one role is decisive or modalities have different spatial needs. The structure should make the evidence hierarchy visible. It should not be selected because it resembles a favorite published figure.

## 6. Logical review gate

Do not continue if any answer is missing:

1. Can the figure-level claim be stated in one sentence?
2. Does every panel answer a different necessary question?
3. Can the evidence chain be read without relying on the caption alone?
4. Does the expression task reveal the important available data structure rather than only its aggregate estimate?
5. Are controls, uncertainty, boundaries, and unsupported results represented?
6. Would removing any proposed panel leave the claim unchanged?
7. Are main-figure and supporting-material decisions explicit?
8. Are chart families complementary, or is a uniform-chart exception supported by checked alternatives and source evidence?
9. Does every panel record all required fields, including `Selected chart family` and the implementation reference subsection?
10. Is there exactly one active design version for this figure, with superseded artifacts identified?

Record unresolved logical risks in the artifact. Return to the claim, evidence chain, or panel map when this gate fails. Only a completed logical design file may be handed to Aesthetic Design.
