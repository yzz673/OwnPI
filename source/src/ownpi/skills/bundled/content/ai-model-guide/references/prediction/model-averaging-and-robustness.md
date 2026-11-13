# Model Averaging And Robustness

Model averaging and ensembling are optional robustness tactics. Use them only after single-model baselines and validation behavior are understood, and design them around output space, metric, leakage, and runtime.

## Use this when

- Multiple validated models or folds exist.
- The metric rewards robustness, ranking, calibration, or file-level aggregation.
- Runtime budget still allows model diversity.

## Do not use this when

- A single model has not been validated.
- The ensemble output space is unclear.
- The full ensemble cannot finish under competition limits.

## Coverage note

Ensembling anchors are `[BIRD24-AUC]`, `[ASL-KP]`, `[POLYMER-FEATURES]`, and `[WAVEFORM-TTA]`. Long-tail ensemble checks should include `[ARIEL]`, `[MITSUI]`, `[IMC-GEOMETRY]`, `[MAP-K]`, and `[NFL-BDB]`.

## Core knowledge

An ensemble is not simply "average models." It needs:

- output-space choice;
- diversity source;
- calibration or normalization;
- validation rule;
- inference budget;
- fallback plan.

## Output spaces

Choose the blend space deliberately:

- logits before sigmoid or softmax;
- sigmoid probabilities;
- softmax probabilities;
- ranked lists;
- regression values;
- coordinates or masks;
- postprocessed file-level outputs.

Local BirdCLEF evidence shows that weighted logits before sigmoid can outperform probability-weighted averages in one solution. Other BirdCLEF solutions use probability means, min reductions, moving averages, and soundscape-level aggregation.

## Diversity sources

Use one or more:

- different folds;
- different seeds;
- different architectures;
- different input sizes or windows;
- different feature sets;
- different external data;
- different pseudo-label versions;
- different target-wise models;
- different postprocess assumptions.

## Mature ensemble contract

Write an ensemble contract:

```text
ensemble_contract = {
  members,
  diversity_source,
  output_space,
  normalization,
  weights,
  validation_delta,
  runtime_delta,
  failure_case
}
```

Do not call a blend an ensemble unless the diversity source is explicit. Ten checkpoints from the same failed training setup may be less useful than three models with different representations or data sources.

## Blend-space rule

- Use logit blending when calibration after sigmoid/softmax is harmful or source-backed.
- Use probability blending when models are similarly calibrated.
- Use rank blending when metric is rank-based.
- Use target-wise blending when targets differ in noise, scale, or data source.
- Use component blending for pipelines such as retrieval/matching only when downstream consistency is preserved.

ASL evidence shows diversity across seeds, layers, dimensions, max length, and features. Open Polymer evidence shows target-wise tree ensembles and feature-selection variants.

## Operational contract

An ensemble is complete only when each member has a reason to exist:

- different data slice;
- different split or seed;
- different architecture role;
- different representation;
- different error profile;
- different inference or postprocess behavior.

The ensemble must name the blend space and the unit being blended. Probability, logit, rank, target, coordinate, segment, and file-level blends are not interchangeable.

## Ensemble ledger

Track ensemble members as:

```text
member_id
role
validation_score
public_lb_score
error_overlap
blend_space
weight
runtime_cost
keep_drop_reason
```

Also store a fallback single-model or small-ensemble submission when runtime or private-LB risk is high.

## Failure rule

Reject an ensemble member if it adds the same errors, requires a different invalid postprocess, or consumes runtime without improving OOF or a metric-relevant diversity measure.

## Transfer boundary

Transfer ensemble strategy only by output space. A rank blend from MAP@K does not become a probability blend for AUC, and a target-wise scientific regression blend does not become a geometry merge.

## Weight search template

Use weight search only after every member has OOF predictions saved in the same output space.

```text
blend_experiment_id
members
blend_space
search_method
weight_constraints
fold_metric
public_lb_metric
runtime_cost
selected_weights
fallback_weights
```

Recommended search order:

1. equal weight baseline;
2. single-member ablation;
3. greedy add-one-member search;
4. constrained grid search over a small set of credible members;
5. optional per-target or per-class weights only when validation supports it.

Do not run unconstrained weight search directly on public LB. If public LB is used as a tie-breaker, label it as public-LB-assisted selection.

## Correlation analysis template

Before adding a member, compare errors:

```text
member_a
member_b
metric_unit
prediction_correlation
error_overlap
unique_correct_cases
unique_failure_cases
runtime_delta
ensemble_delta
decision
```

Useful members usually have one of these properties:

- different failure cases under the same metric unit;
- better performance on a rare class, target, scene, or sequence length;
- different representation or training data;
- lower runtime for a fallback submission;
- higher stability across folds.

Reject a member with high error overlap unless it improves calibration, runtime, or a specific target that the final metric rewards.

## Case-derived ensemble patterns

### `[BIRD24-AUC]` Audio event classification

Average probabilities, blend logits, add neighboring-window predictions, apply moving averages, and aggregate at file/soundscape level. Validate each aggregation separately.

### `[BENGALI-WER]` Speech recognition

Use ensemble or rescoring when it improves WER through ASR, language model, and punctuation components. The ensemble may operate on decoded text, beams, or model outputs, not only raw probabilities.

### `[POLYMER-FEATURES]` Tabular/scientific regression

Blend LightGBM, CatBoost, XGBoost, target-wise models, and feature subsets. Use CV and target-wise errors, not only public LB.

### `[IMC-GEOMETRY]` Matching/geometric tasks

Ensemble retrieval and matching components only if they improve cluster/pose metrics without increasing false matches.

### `[MAP-K]` LLM/ranking tasks

Average logits or ranked probabilities when runtime allows. Avoid adding large models if curated evidence shows they are too slow or overfit.

### Long-tail ensemble rules

- `[ARIEL]`: scientific ensembles should preserve uncertainty/log-likelihood behavior, not only mean prediction.
- `[MITSUI]` and `[NFL-BDB]`: time/event prediction ensembles should test temporal stability and avoid leakage through future-aware features.
- `[IMC-GEOMETRY]`: pipeline ensembles can combine retrieval/matching/clustering components, but false matches can damage geometry.

## Failure modes

- Ensembling poorly calibrated outputs.
- Adding models that improve public LB but hurt private-like validation.
- Ignoring runtime limits.
- Increasing model count without diversity.
- Averaging after incompatible postprocess steps.

## Minimum viable workflow

1. Choose the blend space from the metric and submission unit.
2. Add only members with a different role, representation, seed, fold, or error profile.
3. Store OOF predictions for every member.
4. Measure member correlation and runtime cost.
5. Tune weights on validation, not only public LB.
6. Keep a smaller fallback ensemble for runtime or private-LB risk.

## Evidence routing

- Positive evidence: `[BIRD24-AUC]`, `[BIRD23-CMAP]`, `[ASL-KP]`, `[POLYMER-FEATURES]`, `[MAP-K]`, `[WAVEFORM-TTA]`.
- Negative evidence: `[ASL-KP]`, `[MAP-K]`.
- Contrast evidence: `[IMC-GEOMETRY]`, `[ARIEL]`, `[SANTA]`.

## Source evidence

### Positive evidence
- [BIRD24-AUC]
- [BIRD23-CMAP]
- [ASL-KP]
- [POLYMER-FEATURES]
- [MAP-K]
- [WAVEFORM-TTA]

### Negative evidence
- [ASL-KP]
- [MAP-K]

### Contrast evidence
- [IMC-GEOMETRY]
- [ARIEL]
- [SANTA]
