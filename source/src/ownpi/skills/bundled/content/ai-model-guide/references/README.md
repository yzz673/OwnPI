# Modeling Guide and Read Order

This collection in `ai-model-guide` separates shared modeling concerns from learning-strategy workflows, prediction guidance, evidence, and templates. It retains the original benchmark examples and their transfer boundaries.

Choose the path for the current modeling decision. For self-supervised representation learning or pretraining objectives, start with the [SSL design guide](self-supervised-learning/design-guide.md); its [paper sources](self-supervised-learning/sources.md) supply the relevant evidence without requiring competition cases first. For benchmark-derived recommendations, read [evidence interpretation](evidence/README.md). General principles can support different learning paradigms, but label-dependent tactics and supervised benchmark recipes apply only where their assumptions hold.

The existing benchmark rules use curated evidence IDs; the SSL branch uses research papers with explicit conditions and limits. Kaggle terms in older evidence sections are historical source context, not the default target workflow for new tasks. The directories organize the material; they do not broaden the validity of its evidence.

## What this skill reference is for

Use this reference set when OwnPI needs to:

- frame a modeling or benchmark ML problem;
- design validation, model, training, inference, ensembling, or postprocess plans;
- design self-supervised views, targets, objectives, and representation evaluation;
- extract practical lessons from prior top solutions;
- decide whether a modeling tactic is evidence-backed or speculative;
- explain why a method is suitable for a task instead of listing random tricks.

## How to use this folder

Use the SSL entry above for self-supervised decisions, reading only the chosen route's details. The following read order is for benchmark-derived modeling; it is not a prerequisite for the SSL branch.

Start with the benchmark task, target, signal unit, and metric, then read only the relevant topic files:

1. `evidence/case-index.md` to choose close and contrast evidence IDs from all 30 indexed high-quality-solution competitions.
2. `evidence/coverage-matrix.md` to see which competition supports which topic as positive, negative, or contrast evidence.
3. `foundations/cross-competition-principles.md` to identify shared benchmark machinery before copying domain-specific tactics.
4. `foundations/decoding-problem-framing.md` to identify target, prediction unit, metric pressure, and constraints.
5. `foundations/metric-objective-alignment.md` to connect the benchmark metric to loss, validation, and postprocess.
6. `foundations/signal-and-label-audit.md` and `foundations/leakage-and-split-protocol.md` before training.
7. `foundations/baseline-ladder-and-eda.md` to create a reproducible baseline.
8. `foundations/model-design.md` and `foundations/training-principles.md` for model roles and training controls; `supervised-learning/training-methods.md` for supervised benchmark training choices.
9. `semi-supervised-learning/semi-supervised-label-expansion.md`, `prediction/prediction-and-test-time-protocol.md`, `prediction/model-averaging-and-robustness.md`, and `prediction/prediction-postprocessing-and-calibration.md` only when the evidence supports those tactics.
10. `foundations/validation-robustness.md` before trusting local validation, held-out evaluation, or historical leaderboard movement.
11. `workflows/benchmark-modeling.md` for the full end-to-end workflow.

## Reusable templates

Use these templates when turning the knowledge into a concrete benchmark artifact:

- `../templates/decoding_benchmark_brief.md`: frame a new decoding benchmark before modeling.
- `../templates/decoding_validation_plan.md`: record split, leakage unit, metric, and robustness checks.
- `semi-supervised-learning/templates/semi_supervised_label_expansion_audit.md`: control pseudo-label source, threshold, leakage, and metric impact.
- `../templates/final_benchmark_package.md`: document final model, validation, inference, ensemble, postprocess, and risk.

## Strategy Decisions

For SSL, follow the six decisions in the [design guide](self-supervised-learning/design-guide.md). The following decisions describe the existing benchmark-derived strategy path.

- frame the target variable, prediction unit, input window, grouping unit, metric unit, validation unit, and claim boundary;
- choose evidence-backed modeling, validation, data, training, inference, optional ensembling, optional semi-supervised label expansion, and postprocess/calibration tactics;
- decide when to move from transparent baselines to stronger model families or model tiers;
- label weak or extrapolated tactics as hypotheses instead of confirmed advice;
- produce reusable artifacts such as a decoding benchmark brief, validation plan, semi-supervised label-expansion audit, or final benchmark package.

## Default Workflow

This is the benchmark-derived workflow. For SSL objective design or diagnosis, use the [SSL design guide](self-supervised-learning/design-guide.md) directly and reuse shared references only where their assumptions hold.

1. Read `evidence/README.md` first to understand the evidence base and evidence IDs.
2. Read `foundations/cross-competition-principles.md` before copying a domain-specific tactic.
3. Read `foundations/decoding-problem-framing.md` and `foundations/metric-objective-alignment.md` before selecting a model.
4. Read `foundations/signal-and-label-audit.md` and `foundations/leakage-and-split-protocol.md` before defining validation.
5. Read only the topic files needed for the current decision:
   - baseline or EDA: `foundations/baseline-ladder-and-eda.md`
   - model choice: `foundations/model-design.md`
   - training diagnosis and controls: `foundations/training-principles.md`
   - supervised benchmark training: `supervised-learning/training-methods.md`
   - semi-supervised label expansion: `semi-supervised-learning/semi-supervised-label-expansion.md`
   - prediction or test-time protocol: `prediction/prediction-and-test-time-protocol.md`
   - model averaging or robustness: `prediction/model-averaging-and-robustness.md`
   - prediction postprocessing or calibration: `prediction/prediction-postprocessing-and-calibration.md`
   - validation robustness: `foundations/validation-robustness.md`
6. Use `evidence/case-index.md` and `evidence/coverage-matrix.md` to pick close and contrast evidence IDs.
7. For end-to-end plans, read `workflows/benchmark-modeling.md`.

## Template Selection

Use these when the user needs an artifact rather than advice:

- `../templates/decoding_benchmark_brief.md`
- `../templates/decoding_validation_plan.md`
- `semi-supervised-learning/templates/semi_supervised_label_expansion_audit.md`
- `../templates/final_benchmark_package.md`

## Recommendation Discipline

The evidence IDs and grading below apply to benchmark-derived recommendations. SSL recommendations instead identify the paper, its applicable setting, and what remains a hypothesis; see [SSL sources](self-supervised-learning/sources.md).

- Cite evidence IDs such as `[BIRD24-AUC]`, `[MAP-K]`, or `[POLYMER-FEATURES]` when making a recommendation.
- Separate `strong`, `medium`, `weak`, and `hypothesis` evidence.
- Treat public leaderboard movement as historical competition-origin evidence, not target-task validation, unless supported by local validation, held-out evaluation, or source-backed justification.
- Do not claim final team solutions for ongoing competitions.

## Source-backed principles

### 0. Shared machinery before domain tactics

Across benchmark tasks, the reusable layer is usually not a specific model. It is the contract around the model: target unit, metric unit, validation unit, data intervention, prediction unit, postprocess constraint, and final package evidence. Read `foundations/cross-competition-principles.md` before adapting a BirdCLEF, MAP@K, ASL, Open Polymer, Image Matching, or behavior-detection tactic to a new task.

### 1. Metric first, not model first

Top solutions repeatedly adapt modeling, training loss, inference aggregation, and postprocess to the leaderboard metric. BirdCLEF ROC AUC solutions use CE/BCE surrogates but often rely on sigmoid/min reduction, chunk averaging, and soundscape-level operations at inference. Bengali.AI WER solutions use ASR models plus language-model and punctuation postprocess because the metric is sequence-level. MAP@K tasks require ranking behavior even when trained as classification.

### 2. Data quality is a solution component

Several curated cases report improvements from filtering noisy samples, relabeling, handling long/short records, harmonizing external datasets, or removing outliers. Data audit is therefore not a prelude to modeling; it is part of the model plan.

### 3. Public LB is evidence, not validation

Curated cases include public/private mismatch, domain shift, public-LB-as-CV tradeoffs, and shake-up warnings. Public leaderboard improvements must be tagged as `leaderboard-only` unless they survive local validation or are explicitly justified by source-backed validation failure.

### 4. Inference is part of the model

Chunk length, window overlap, TTA, acceleration backend, postprocess, and timeout guards repeatedly decide final performance. A training-only plan is incomplete for many competitions in the curated evidence set.

### 5. Ensemble works when diversity and metric alignment are real

Ensembling appears frequently, but the curated writeups use different output spaces: probability average, logit weighting, rank averaging, min/max reductions, file-level aggregation, seed/fold diversity, and target-wise tree ensembles. The blend must match the metric and submission unit.

### 6. Pseudo-labeling is powerful but conditional

Pseudo-labeling appears across BirdCLEF, Bengali.AI, Deep Past, CSIRO, and MAP examples. The same corpus also contains failed or harmful pseudo/semi-supervised attempts. Treat pseudo-labeling as a data-generation intervention with leakage, confidence, and validation controls.

## Common anti-patterns

- Treating public LB improvement as validation without a risk tag.
- Adding pseudo labels without teacher, threshold, count, and leakage controls.
- Building a large ensemble before measuring inference time.
- Applying postprocess without an ablation.
- Copying a method across domains without checking metric and data-unit compatibility.
- Claiming final team solutions for ongoing competitions.
