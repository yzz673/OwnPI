# Metric Objective Alignment

The metric defines the target behavior. The loss is only a training surrogate.

## Use this when

- Choosing loss, validation metric, aggregation, or postprocess.
- A metric is ranking-like, sequence-like, or domain-specific.
- A training loss improves but benchmark or held-out metric does not.

## Do not use this when

- The official metric has not been identified.

## Coverage note

Metric knowledge is anchored by `[BIRD24-AUC]`, `[BIRD23-CMAP]`, `[BENGALI-WER]`, `[MAP-K]`, `[CMI-BEHAVIOR]`, and `[POLYMER-FEATURES]`. Add contrast from `[BIRD21-FBETA]`, `[BIRD22-WCA]`, `[PHYSIONET-ECG]`, `[ARIEL]`, `[WAVEFORM-TTA]`, `[SANTA]`, and `[CODE-GOLF]` before reusing a metric-objective rule.

## Core knowledge

Local high-quality solutions repeatedly separate:

- training loss;
- validation metric;
- inference aggregation;
- final postprocess.

The same loss can behave differently under AUC, WER, MAP@K, F-score, MAE, R2, or domain-specific metrics. Always connect them explicitly.

## Metric-to-objective procedure

1. Write the official metric name.
2. Identify the metric type:
   - ranking metric;
   - thresholded classification metric;
   - sequence edit metric;
   - regression error;
   - geometric or reconstruction metric;
   - domain-specific composite metric.
3. Define the training surrogate.
4. Define the validation metric implementation.
5. Define inference aggregation that matches the submission unit.
6. Define postprocess parameters and how they are validated.

## Case-derived patterns

### `[BIRD24-AUC]` AUC-like audio metric

BirdCLEF ROC AUC solutions train with CE/BCE surrogates but tune inference around ranked probabilities: sigmoid outputs, min reduction, chunk averaging, neighboring-window weighting, soundscape-level max/mean operations, and moving averages. The objective is not just lower CE/BCE; it is better ranking under the competition metric.

### `[BIRD23-CMAP]` buffered_cmAP metric

BirdCLEF 2023 evidence notes that buffered_cmAP changed threshold pressure. This means threshold tuning may be less central than ranking, aggregation, and efficient inference.

### `[BENGALI-WER]` WER metric

Bengali.AI WER requires sequence-level correction. Local solutions use CTC/ASR models, language models, punctuation restoration, beam search, chunking/stride, dynamic padding, and noisy-label filtering. A better acoustic model alone may not optimize WER if decoding and punctuation are weak.

### `[MAP-K]` MAP@K metric

MAP@K requires the correct answer to appear high in a ranked list. Local MAP examples use OOF soft labels, ambiguity handling, special-token logits, and ranked output construction. A classification loss can be used, but evaluation must be ranking-aware.

### `[POLYMER-FEATURES]` Regression metrics

Open Polymer and other scientific regression cases show target-wise modeling, feature harmonization, calibration, and ensembles. If targets differ in scale and data quality, one global loss can hide target-specific failure.

### `[CMI-BEHAVIOR]` Domain-specific behavior metrics

CMI behavior detection uses postprocess constraints such as subject/orientation logic, Hungarian assignment, and prediction history. A model output that violates domain constraints may score poorly even if raw probability loss improves.

### Long-tail objective rules

- `[ARIEL]`: when uncertainty or likelihood is part of the metric, model calibration and uncertainty output are metric objects, not optional diagnostics.
- `[PHYSIONET-ECG]`: for signal extraction from images, optimize downstream waveform/signal quality rather than only image-level recognition.
- `[BIRD21-FBETA]` and `[BIRD22-WCA]`: earlier BirdCLEF metrics show that audio competition objectives change across years, so do not transfer ROC AUC-specific postprocess blindly.
- `[SANTA]` and `[CODE-GOLF]`: search/optimization metrics need constraint satisfaction and final objective evaluation, not training loss.

## Objective design checklist

- For classification metrics, decide whether logits, sigmoid probabilities, softmax probabilities, or calibrated scores should be ensembled.
- For ranking metrics, validate top-k behavior, not only class accuracy.
- For sequence metrics, test decoding and postprocess as part of validation.
- For regression metrics, report per-target error and aggregate metric.
- For constrained metrics, validate postprocess as its own experiment.

## Mature objective pattern

Write objectives in four layers:

1. `optimization_loss`: what the model minimizes.
2. `validation_metric`: the local implementation of the leaderboard metric.
3. `selection_metric`: the score used to choose checkpoints, ensembles, and final submissions.
4. `diagnostic_metrics`: extra metrics that explain failure but do not select the final model.

Do not let `optimization_loss` silently become `selection_metric`. In `[BIRD24-AUC]`, CE/BCE can train useful models, but final selection depends on ranked probability behavior after chunk aggregation. In `[BENGALI-WER]`, acoustic loss is not enough without decoding quality. In `[MAP-K]`, class probability loss must be converted into ranked-list quality. In `[ARIEL]`, uncertainty-aware scoring means point error alone is incomplete.

## Objective failure pattern

If validation improves but public/private score does not, check:

- wrong aggregation unit;
- loss optimizes calibration but metric rewards ranking;
- postprocess tuned on different objective;
- class/target imbalance hidden by aggregate metric;
- runtime fallback changes the effective model at submission time.

## Failure rule

Reject an objective setup if the optimized loss, validation metric, inference aggregation, and final submission metric do not measure the same competition behavior. A lower training loss is not evidence unless it survives the metric-to-submission path.

## Operational contract

The objective is complete only when the metric is connected to four implementation choices:

- training loss;
- validation calculation;
- inference aggregation;
- postprocess or calibration.

Do not treat the loss as the objective. The objective is the full route from raw model output to leaderboard score.

## Objective ledger

Track every objective choice as:

```text
metric_name
metric_unit
loss_proxy
output_transform
validation_metric_impl
postprocess_dependency
public_lb_delta
private_or_oof_delta
known_metric_gap
```

This ledger is required before comparing losses. If two losses produce outputs that need different postprocess rules, compare the whole pipeline, not the loss curves.

## Transfer boundary

Transfer a loss only when the target metric has the same behavior. Transfer calibration, ranking, decoding, or uncertainty handling only when the target submission unit and metric unit match the source competition.

## Minimum viable workflow

1. Reimplement the official metric locally.
2. Identify the metric unit and submission unit.
3. Choose the proxy loss and write the expected metric gap.
4. Validate the whole route from model output to leaderboard-style score.
5. Add calibration, ranking, decoding, uncertainty, or postprocess only when the metric requires it.
6. Compare objectives by final metric behavior, not training loss alone.

## Evidence routing

- Positive evidence: `[BIRD24-AUC]`, `[BIRD23-CMAP]`, `[BENGALI-WER]`, `[MAP-K]`, `[CMI-BEHAVIOR]`, `[POLYMER-FEATURES]`.
- Negative evidence: `[BIRD24-AUC]`, `[MAP-K]`.
- Contrast evidence: `[BIRD21-FBETA]`, `[BIRD22-WCA]`, `[PHYSIONET-ECG]`, `[ARIEL]`.

## Source evidence

### Positive evidence
- [BIRD24-AUC]
- [BIRD23-CMAP]
- [BENGALI-WER]
- [MAP-K]
- [CMI-BEHAVIOR]
- [POLYMER-FEATURES]

### Negative evidence
- [BIRD24-AUC]
- [MAP-K]

### Contrast evidence
- [BIRD21-FBETA]
- [BIRD22-WCA]
- [PHYSIONET-ECG]
- [ARIEL]
