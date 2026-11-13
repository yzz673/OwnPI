# Baseline Ladder And EDA

A useful baseline is a reproducible measurement system, not just a weak model.

## Use this when

- Creating the first reproducible run.
- Checking whether the metric, split, artifact chain, and final package format work.
- Turning EDA into baseline decisions.

## Do not use this when

- The baseline is already frozen and the current task is only final package documentation.

## Coverage note

Baseline/EDA is anchored by `[BENGALI-WER]`, `[BIRD24-AUC]`, `[IMC-GEOMETRY]`, and `[POLYMER-FEATURES]`. Long-tail baseline checks should include `[AIMO]`, `[SANTA]`, `[CODE-GOLF]`, `[RECODAI]`, `[MITSUI]`, and `[PHYSIONET-ECG]`.

## Core knowledge

The baseline must answer:

- Can the data be loaded correctly?
- Can the metric be reproduced?
- Can the submission be created?
- What is the first reliable validation signal?
- Which public or historical solution is the baseline borrowing from?
- What obvious data issues appear before advanced modeling?

## Baseline deliverables

Create these artifacts:

- data schema summary;
- split rule;
- metric implementation;
- first model or heuristic;
- first submission;
- runtime estimate;
- baseline score table;
- EDA findings;
- known source lineage.

## Mature baseline pattern

A baseline is mature only if another user can rerun it and explain the result:

1. `load`: input files and target columns load without hidden manual steps.
2. `split`: fold generation is deterministic and leakage-aware.
3. `metric`: local score reproduces the official metric behavior.
4. `submit`: output format passes validation.
5. `runtime`: single-model inference time is known.
6. `lineage`: public notebook, previous-year solution, or source evidence ID is recorded.
7. `error sample`: at least a few failures are inspected.

If any part is missing, the baseline is a demo, not a foundation.

## Baseline maturity levels

- `level 0`: loads data and writes submission.
- `level 1`: reproduces metric and split.
- `level 2`: has first model plus EDA-driven risk log.
- `level 3`: has runtime measurement and error analysis.
- `level 4`: has source-backed next experiments and rollback conditions.

## Operational contract

A baseline is complete only when it proves the pipeline can move from raw data to valid submission:

- read competition data;
- build the validation split;
- train or fit the simplest reasonable model;
- compute the official-like metric;
- write a valid submission file;
- store OOF or validation predictions;
- record runtime and failure cases.

A baseline that cannot produce a valid submission is an EDA note, not a competition baseline.

## Baseline ledger

Track baseline state as:

```text
baseline_id
data_version
split_name
model_family
feature_set
metric_score
public_lb_score
runtime
submission_valid
main_error_modes
next_upgrade
```

Use this ledger to prevent later models from hiding pipeline regressions.

## Failure rule

Reject a baseline if it is too complex to debug. The first baseline should expose data, split, metric, and submission problems, not optimize leaderboard rank.

## Transfer boundary

Transfer baseline structure by task contract. A BirdCLEF baseline transfers to another windowed audio task, but only the pipeline skeleton transfers to MAP@K, image matching, or optimization tasks.

## EDA targets by task type

### `[BENGALI-WER]` and `[BIRD24-AUC]` Audio and speech

Inspect duration, sample rate, silence/noise, label distribution, long clips, short clips, transcript length, window/chunk policy, and noisy labels. Bengali.AI and BirdCLEF cases show that duration and chunking decisions affect both training and inference.

### `[IMC-GEOMETRY]` Image and matching

Inspect image size, orientation, duplicate scenes, image-pair count, retrieval shortlist quality, cluster structure, and outlier components. Image Matching evidence shows that orientation correction, clustering, and min-pair choices are baseline-level decisions.

### `[POLYMER-FEATURES]` Tabular and molecular

Inspect target distributions, target correlations, missing values, external data compatibility, feature count, descriptor generation cost, and per-target validation. Open Polymer evidence shows feature engineering and harmonized data as baseline foundations.

### `[MAP-K]` NLP and ranking

Inspect label ambiguity, class imbalance, duplicate text, target ranking format, prompt/runtime budget, and whether soft labels or OOF predictions are needed. MAP evidence shows the task can be classification-like but metric-ranked.

## Case-derived baseline rules

- Reproduce a simple public baseline first when source-backed.
- Do not change model and postprocess in the same first experiment.
- Keep baseline inference time; later ensembles and TTA must be compared against it.
- If a prior-year solution is reused, record what changed in metric, data, and distribution.
- If EDA reveals noisy labels, create a cleaning baseline before adding larger models.
- `[AIMO]`: for reasoning competitions, the baseline must include prompt/runtime/evaluation harness, not only model selection.
- `[SANTA]` and `[CODE-GOLF]`: for search/optimization competitions, the baseline is a valid solver and objective evaluator.
- `[RECODAI]` and `[PHYSIONET-ECG]`: for scientific/CV tasks, first baseline should verify preprocessing and output validity before architecture tuning.

## Baseline archetypes

Use these archetypes to start a new competition quickly. Pick the closest one and adapt the units, metric, and submission format.

### Audio or long-signal baseline

Use for `[BIRD24-AUC]`, `[BIRD23-CMAP]`, `[BENGALI-WER]`, and related signal tasks.

Minimum baseline:

1. inspect duration, sample rate, silence/noise, and label distribution;
2. choose a fixed chunk or window policy;
3. train a small CNN/SED/ASR-style model or use a pretrained signal model;
4. aggregate window/chunk predictions to the metric unit;
5. run one inference-time aggregation rule;
6. store failure examples by duration, noise level, and label rarity.

Do not tune TTA, pseudo-labels, or soundscape postprocess until this baseline can produce valid OOF and submission predictions.

### Ranking or candidate-selection baseline

Use for `[MAP-K]`, `[MDC-EXTRACT]`, and other tasks where candidate ordering matters.

Minimum baseline:

1. create candidate set generation first;
2. train a simple classifier or scorer;
3. convert scores to ranked output;
4. compute the official ranking metric locally;
5. inspect examples where the correct answer is present but ranked too low;
6. store candidate recall separately from rank quality.

Do not interpret classification accuracy as ranking quality unless the candidate list and ranked metric also improve.

### Feature-centric regression baseline

Use for `[POLYMER-FEATURES]`, `[CSIRO]`, `[ARIEL]`, and related scientific regression tasks.

Minimum baseline:

1. build simple feature table with target-wise missingness audit;
2. fit LightGBM/CatBoost/XGBoost or ridge-style model per target or target group;
3. compute target-wise and aggregate metrics;
4. test descriptor/external-data additions one group at a time;
5. store feature importance and outlier failures;
6. keep a no-external-data baseline for rollback.

Do not start with a deep model until feature quality and target-specific noise are understood.

### Matching or geometry baseline

Use for `[IMC-GEOMETRY]`, `[RNA3D]`, `[PHYSIONET-ECG]`, and tasks where structure or reconstruction matters.

Minimum baseline:

1. define pair, scene, graph, or structure unit;
2. run a simple retrieval or template step;
3. apply a minimal matcher or reconstruction method;
4. score the final structure, not only intermediate classifier output;
5. inspect failed scenes or structures for missing constraints;
6. record runtime and failure mode by scene size or structure complexity.

Do not call a component good until downstream geometry or reconstruction improves.

## Minimum viable workflow

1. Load data and generate a valid sample submission.
2. Implement the official-like metric.
3. Build the simplest leakage-aware split.
4. Train or fit a minimal model that produces validation predictions.
5. Save OOF predictions, failure examples, and runtime.
6. Use EDA to choose the next experiment, not to decorate the report.

## Evidence routing

- Positive evidence: `[BENGALI-WER]`, `[BIRD24-AUC]`, `[IMC-GEOMETRY]`, `[MAP-K]`, `[POLYMER-FEATURES]`, `[CMI-BEHAVIOR]`.
- Negative evidence: `[MAP-K]`, `[ASL-KP]`.
- Contrast evidence: `[SANTA]`, `[CODE-GOLF]`, `[RNA3D]`.

## Source evidence

### Positive evidence
- [BENGALI-WER]
- [BIRD24-AUC]
- [IMC-GEOMETRY]
- [MAP-K]
- [POLYMER-FEATURES]
- [CMI-BEHAVIOR]

### Negative evidence
- [MAP-K]
- [ASL-KP]

### Contrast evidence
- [SANTA]
- [CODE-GOLF]
- [RNA3D]
