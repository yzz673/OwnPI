# Benchmark Modeling Workflow

This is a benchmark workflow, not a universal learning-paradigm recipe. Use the [shared guide](../README.md) for foundations and select this workflow when the task has the stated benchmark, metric, and validation setup.

This playbook turns a supervised learning or benchmark task into a reproducible, evidence-backed plan.

## Use this when

- Starting a new supervised learning or benchmark task.
- Auditing whether a plan covers metric, split, data, model, inference, ensemble, and postprocess.
- Converting local case evidence into an experiment queue.

## Do not use this when

- The task only needs one narrow tactic such as pseudo-label auditing or postprocess validation.
- The benchmark status, metric, target, or data unit is still unknown.

## Coverage note

This playbook is supported by all 30 indexed competitions through `../evidence/case-index.md` and `../evidence/coverage-matrix.md`. Use `[BIRD24-AUC]`, `[BENGALI-WER]`, `[POLYMER-FEATURES]`, `[MAP-K]`, `[IMC-GEOMETRY]`, and `[CMI-BEHAVIOR]` as common anchor cases, but check long-tail IDs such as `[SANTA]`, `[CODE-GOLF]`, `[RNA3D]`, `[ARIEL]`, `[MITSUI]`, and `[NFL-BDB]` before transferring the workflow.

## Core knowledge

A strong Kaggle plan is not a list of tricks. It is a sequence of decisions:

1. What is the metric really rewarding?
2. What is the independent data unit?
3. What local validation can be trusted?
4. What baseline reproduces the task?
5. What methods are source-backed for this task family?
6. What inference and postprocess are required by the submission format?
7. What public/private or runtime risks can invalidate a result?

## Full workflow

### 1. Build the competition brief

Record:

- competition status: ended or ongoing;
- domain and task type;
- metric and metric direction;
- data unit and submission unit;
- external data policy;
- inference/runtime limits;
- public/private leaderboard structure when available.

Do not claim final team solutions for ongoing competitions. The curated extractor explicitly separates ended competitions from ongoing competitions.

### 2. Read sources by reliability

Use this priority:

1. team-member discussion writeups;
2. normalized Kaggle knowledge cards parsed from writeups;
3. public team notebooks as fallback;
4. public notebooks not tied to a team only as baseline hints.

This priority matters because public notebooks can be useful but may not represent the final private-LB solution.

### 3. Create a baseline before adding complexity

A baseline should include:

- data loading;
- metric calculation;
- valid split;
- first submission format;
- runtime estimate;
- known source lineage if copied from a public notebook or prior solution.

In local evidence, many top solutions started from public notebooks or previous-year solutions, but the final gains came from task-specific changes such as cleaning, pseudo-labels, postprocess, or ensembling.

### 4. Extract method candidates

Convert cases into method candidates:

- `[BIRD24-AUC]` BirdCLEF-like audio: mel spectrograms, 5-second windows, SED/CNN backbones, pseudo-labels, external data, ensembling, TTA, OpenVINO/ONNX, and soundscape-level postprocess.
- `[BENGALI-WER]` Speech recognition: pretrained ASR, CTC, language model, punctuation model, dynamic padding, chunking/stride, denoising selection, and noisy-label filtering.
- `[ASL-KP]` Sign-language/keypoint tasks: pose/keypoint normalization, longer frame windows, CNN/MHSA/Transformer variants, augmentation, and model diversity.
- `[POLYMER-FEATURES]` Tabular/molecular regression: feature engineering, external-data harmonization, LightGBM/CatBoost/XGBoost, target-wise models, and feature selection.
- `[IMC-GEOMETRY]` Retrieval/matching tasks: retrieval shortlist, orientation correction, feature matching, clustering, thresholds, and outlier handling.
- `[MAP-K]` MAP/ranking tasks: soft labels, ambiguity handling, ranked output construction, special-token logits, and runtime-aware LLM inference.

### 5. Convert each method candidate into an experiment

Each experiment must state:

- expected metric effect;
- source case that motivates it;
- validation check;
- runtime cost;
- failure condition;
- rollback condition.

### 6. Make a final submission package

A final package should contain:

- selected model(s);
- validation table;
- public/private or CV/LB risk note;
- inference path and timeout guard;
- postprocess parameters;
- ensemble formula;
- source-backed reasons for final selection.

## Case-derived rules

- If local CV conflicts with public LB, write down why before using public LB as CV.
- If a method relies on external data, audit distribution and license/source first.
- If pseudo-labeling is used, log teacher, threshold, sample count, and leakage control.
- If ensembling is used, specify whether blending logits, probabilities, ranks, coordinates, or postprocessed outputs.
- If postprocess changes predictions, validate it independently from model training.
- If inference acceleration is used, benchmark the actual runtime backend; OpenVINO is not always faster than ONNX in the curated evidence set.
- `[SANTA]` and `[CODE-GOLF]`: for optimization/search competitions, define objective, constraints, search budget, and reproducibility before model-like abstractions.
- `[MITSUI]` and `[NFL-BDB]`: for market/sports prediction, treat time, group, and event chronology as part of the competition brief.
- `[RNA3D]`, `[PHYSIONET-ECG]`, and `[ARIEL]`: for scientific structure/signal tasks, include domain-specific output validity checks in the plan.

## Mature playbook pattern

Treat a Kaggle solution as a layered system:

1. `data layer`: file inventory, labels, metadata, external data, noisy samples, grouping units.
2. `metric layer`: official metric, local reproduction, ranking/calibration/sequence/geometric behavior.
3. `validation layer`: split, leakage unit, fold variance, public/private risk, source-quality risk.
4. `model layer`: representation, backbone, task head, auxiliary heads, teacher/student if used.
5. `training layer`: loss, schedule, augmentation, stage training, pseudo-label or silver-data handling.
6. `inference layer`: chunking, batching, acceleration, TTA, runtime budget, fallback.
7. `aggregation layer`: fold/seed/model ensemble, output-space choice, calibration.
8. `postprocess layer`: constraints, smoothing, ranking correction, rule-based extraction, geometry repair.
9. `selection layer`: final submission rationale, rejected alternatives, known risk.

Do not skip layers. If a layer is irrelevant, mark it as `not applicable` and explain why. This is how the curated top solutions should be converted into reusable project knowledge rather than copied as isolated tricks.

## Transfer rule

When transferring a tactic:

- First match by metric behavior.
- Then match by data unit.
- Then match by inference unit.
- Only then match by model family.

Example: `[BIRD24-AUC]` shifted-window TTA transfers better to windowed signal/audio tasks than to `[POLYMER-FEATURES]` tabular molecular regression, even if both use ensembles.

## Operational contract

An end-to-end competition plan is complete only when it contains:

- competition contract;
- validation contract;
- data and leakage audit;
- baseline and metric implementation;
- model-role map;
- training ledger;
- inference and submission contract;
- ensemble and postprocess decisions;
- final risk register.

The playbook should be used as a checklist for missing contracts, not as a rigid chronological order. Some competitions require early inference planning, while others require data audit before any model work.

## Competition ledger

Track the full competition state as:

```text
stage
main_decision
evidence_ids
artifact_path_or_id
metric_state
risk_state
next_action
stop_condition
```

The ledger should make it clear which decisions are source-backed, which are target-validated, and which are still hypotheses.

## Failure rule

Do not move to final submission when any of these are missing:

- metric implementation;
- leakage-safe validation or explicit no-CV risk label;
- final inference runtime;
- postprocess ablation;
- submission format validation;
- rollback or fallback submission.

## Minimum viable workflow

1. Fill the competition contract.
2. Build metric implementation and baseline submission.
3. Establish leakage-safe validation or mark no-CV risk explicitly.
4. Audit data sources and external-data legality.
5. Choose model roles and training stages.
6. Validate inference, ensemble, and postprocess as part of the same pipeline.
7. Prepare final submission package with risk register and fallback.

## Evidence routing

- Positive evidence: `[BIRD24-AUC]`, `[BENGALI-WER]`, `[POLYMER-FEATURES]`, `[IMC-GEOMETRY]`, `[MAP-K]`, `[CMI-BEHAVIOR]`.
- Negative evidence: `[BIRD24-AUC]`, `[MAP-K]`, `[MABE]`, `[ASL-KP]`.
- Contrast evidence: `[SANTA]`, `[CODE-GOLF]`, `[RNA3D]`, `[ARIEL]`.

## Source evidence

### Positive evidence
- [BIRD24-AUC]
- [BENGALI-WER]
- [POLYMER-FEATURES]
- [IMC-GEOMETRY]
- [MAP-K]
- [CMI-BEHAVIOR]

### Negative evidence
- [BIRD24-AUC]
- [MAP-K]
- [MABE]
- [ASL-KP]

### Contrast evidence
- [SANTA]
- [CODE-GOLF]
- [RNA3D]
- [ARIEL]
