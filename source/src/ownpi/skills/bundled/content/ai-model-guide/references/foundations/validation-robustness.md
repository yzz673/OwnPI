# Validation Robustness

Validation robustness means knowing whether an improvement is likely to survive held-out evaluation, subject/session variance, or benchmark evaluator pressure, not just whether one score went up.

## Use this when

- Deciding whether a change is real.
- Selecting final benchmark packages.
- Reviewing local/held-out mismatch or runtime risk.

## Do not use this when

- The only goal is exploratory EDA without model selection.

## Coverage note

Validation robustness is anchored by `[BIRD24-AUC]`, `[BENGALI-WER]`, `[MAP-K]`, and `[POLYMER-FEATURES]`, with long-tail contrast from `[ARIEL]`, `[MITSUI]`, `[NFL-BDB]`, `[WAVEFORM-TTA]`, `[SANTA]`, and `[CODE-GOLF]`.

## Core knowledge

The curated evidence set contains many public/private score pairs, local-CV failures, public-LB-as-CV tradeoffs, domain-shift notes, and shake-up warnings. A method should be labeled by evidence strength.

## Evidence labels

Use these labels in experiment reports:

- `validated`: improves offline validation metric and has no source-backed contradiction.
- `private-backed`: supported by historical private-LB evidence from similar tasks.
- `leaderboard-only`: improves public LB but lacks local validation.
- `unstable`: improves one split while worsening another, or conflicts with known public/private behavior.
- `runtime-risk`: works offline but may exceed Kaggle time/memory limits.
- `source-weak`: based on a short, empty, fetch-failed, or boilerplate-contaminated writeup.

## Robustness checklist

- Compare local CV, public LB, and private LB when available.
- Track fold variance, seed variance, model variance, and postprocess sensitivity.
- Record whether the improvement comes from model, data, inference, ensemble, or postprocess.
- Keep failed methods from writeups; they prevent repeated dead ends.
- Check source quality before trusting a writeup.

## Mature validation pattern

Use a robustness ledger:

| experiment | changed layer | local metric | public LB | private-like proxy | runtime | robustness label | decision |
|---|---|---:|---:|---:|---:|---|---|

The `changed layer` must be one of: data, split, model, training, inference, ensemble, postprocess. If an experiment changes more than one layer, mark it as mixed and do not over-interpret the result.

## Decision thresholds

- Accept a method only when it improves the competition metric under a leakage-safe split or has clearly labeled private-backed evidence.
- Keep a method as experimental when it improves public LB but conflicts with local validation.
- Reject or isolate a method when it increases runtime beyond the submission budget.
- Treat source-weak writeups as hypothesis generators, not rules.

## Operational contract

Validation is complete only when it can answer:

- what distribution the validation set simulates;
- which hidden group is protected;
- which metric implementation is trusted;
- which artifacts are stored as OOF evidence;
- how much disagreement between CV and public LB is acceptable;
- when to stop trusting a local score.

The validation contract must be created before model selection. Changing validation after model selection turns previous comparisons into historical notes, not current evidence.

## Validation ledger

Track each method as:

```text
experiment_id
split_name
fold_scores
aggregate_score
public_lb_score
metric_impl_version
seed_or_fold_variance
runtime_status
promotion_decision
reason
```

Store enough information to replay why a method was kept, dropped, or used only in the final ensemble.

## Failure rule

Do not promote a method when it improves the mean but fails most folds, improves public LB while CV is flat, or changes inference behavior in a way validation did not simulate.

## Transfer boundary

Transfer validation practices only when hidden-distribution risks are similar. A time-based market split, a subject-level behavior split, and a scene-level matching split solve different problems even if all are called cross-validation.

## Case-derived lessons

### `[BIRD24-AUC]` Public LB can mislead

BirdCLEF examples include local CV not helping, public LB as CV, reduced submissions to avoid overfitting public LB, and expected public/private reversal. Treat these as risk patterns, not as permission to ignore validation.

### `[BENGALI-WER]` Data quality can dominate validation

Bengali.AI examples show local WER/public LB mismatch when overtraining noisy annotation pairs. Validation robustness can require data cleaning before model selection.

### `[MAP-K]` Runtime can invalidate validation

BirdCLEF and LLM/MAP examples show inference acceleration, model-size limits, and timeout constraints. A high-validation ensemble is not robust if it cannot complete under competition limits.

### Source quality matters

The curated audit found empty texts, short records, fetch failures, and boilerplate candidates. Use higher-quality writeups for stronger conclusions.

### Long-tail robustness rules

- `[ARIEL]`: robustness includes uncertainty/log-likelihood behavior, not just point-prediction error.
- `[MITSUI]` and `[NFL-BDB]`: robustness must include temporal or event-group stability, because random splits can overstate performance.
- `[SANTA]` and `[CODE-GOLF]`: validation is final-objective reproducibility under search/compute constraints, not fold score.
- `[WAVEFORM-TTA]`: checkpoint medians and saved prediction aggregation are robustness tools, but they must be tied to validation and runtime.

## Minimum viable workflow

1. State what hidden evaluation distribution validation simulates.
2. Compute the official-like metric on each fold.
3. Store fold-level scores, aggregate score, and OOF predictions.
4. Compare CV and public LB movement without letting public LB replace validation.
5. Assign robustness labels: validated, public-only, fold-fragile, runtime-invalid, or rejected.
6. Promote a method only when its gain survives the relevant validation unit.

## Evidence routing

- Positive evidence: `[BIRD24-AUC]`, `[BENGALI-WER]`, `[MAP-K]`, `[CMI-BEHAVIOR]`, `[POLYMER-FEATURES]`, `[IMC-GEOMETRY]`.
- Negative evidence: `[BIRD24-AUC]`, `[MAP-K]`, `[MABE]`.
- Contrast evidence: `[SANTA]`, `[CODE-GOLF]`, `[ARIEL]`.

## Source evidence

### Positive evidence
- [BIRD24-AUC]
- [BENGALI-WER]
- [MAP-K]
- [CMI-BEHAVIOR]
- [POLYMER-FEATURES]
- [IMC-GEOMETRY]

### Negative evidence
- [BIRD24-AUC]
- [MAP-K]
- [MABE]

### Contrast evidence
- [SANTA]
- [CODE-GOLF]
- [ARIEL]
