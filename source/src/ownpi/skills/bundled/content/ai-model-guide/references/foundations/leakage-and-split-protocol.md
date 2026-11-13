# Leakage And Split Protocol

Validation is only useful when the split respects the task's true independence unit: subject, session, run, trial, stimulus, window, patient, recording, or dataset.

## Use this when

- Defining folds.
- Adding pseudo labels, soft labels, external data, or postprocess.
- Held-out/evaluator behavior and local validation disagree.

## Do not use this when

- There is no train/validation decision to make and only a fixed final inference run is being documented.

## Coverage note

Split knowledge is anchored by `[MAP-K]`, `[CMI-BEHAVIOR]`, `[BIRD24-AUC]`, `[BENGALI-WER]`, and `[IMC-GEOMETRY]`. Long-tail split checks should include `[MITSUI]`, `[NFL-BDB]`, `[PHYSIONET-ECG]`, `[RSNA-ANEURYSM]`, and `[ARIEL]`.

## Core knowledge

Kaggle leakage is often not a simple train/test duplicate. It can appear through:

- subject overlap;
- file or soundscape overlap;
- image scene overlap;
- repeated source documents;
- pseudo labels generated from the validation fold;
- metadata that directly encodes the target;
- public leaderboard probing;
- postprocess tuned on test-like artifacts.

## Split design procedure

1. Identify the independent unit.
2. Identify the grouping unit.
3. Identify whether labels are ambiguous or generated.
4. Decide whether pseudo labels must be out-of-fold.
5. Decide whether public LB is a validation signal or only a risk signal.
6. Write the split rule before training.

## Case-derived split rules

### `[CMI-BEHAVIOR]` Subject or sequence data

For behavior/sensor data, split by subject or sequence when records share temporal or subject-specific structure. CMI-style evidence uses subject/orientation constraints and history; validation that mixes subject-specific patterns can overstate performance.

### `[BIRD24-AUC]` Audio soundscapes

For BirdCLEF-style soundscapes, the model input may be a 5-second window while the logical unit is a longer soundscape. Validation should avoid treating correlated windows as independent evidence.

### `[MAP-K]` Ranking labels

For MAP@K or misconception tasks, OOF soft labels are safer than labels generated from a model trained on the same validation examples. Ambiguous labels should be handled as uncertainty, not leaked certainty.

### `[BIRD24-AUC]` Public-LB fallback

Some BirdCLEF cases use public LB because local CV failed under domain shift. This is a documented tradeoff, not a default rule. Mark it as `leaderboard-only` and reduce over-tuning.

### `[BENGALI-WER]` Noisy annotation tasks

Bengali.AI evidence shows that local validation can diverge when noisy annotations and overtraining dominate. Split design must be paired with data-quality filtering.

## Leakage red flags

- Validation examples are windows from the same file as training windows.
- Pseudo labels are generated with a teacher that saw validation rows.
- Soft labels are not OOF.
- Public LB is used repeatedly to tune thresholds or postprocess.
- Metadata filters are chosen after inspecting test behavior without validation.
- External data includes near duplicates of test examples.
- `[MITSUI]`: market/time-series validation should respect chronology and avoid future information in features or folds.
- `[NFL-BDB]`: sports tracking/prediction validation should respect game, season, play, or team grouping where applicable.
- `[RSNA-ANEURYSM]` and `[PHYSIONET-ECG]`: medical/signal tasks should avoid patient/study-level leakage.

## Mature split pattern

Define split as a contract:

```text
split_contract = {
  train_unit,
  validation_unit,
  forbidden_overlap,
  group_key,
  time_key,
  pseudo_label_policy,
  external_data_policy,
  public_lb_policy
}
```

This prevents hidden leakage when a row is not the true independent unit. `[BIRD24-AUC]` warns about window/soundscape correlation. `[CMI-BEHAVIOR]` warns about subject and temporal state. `[IMC-GEOMETRY]` warns about scene-level grouping. `[MITSUI]` and `[NFL-BDB]` warn about chronological or event grouping.

## Public leaderboard policy

Public LB can be recorded as evidence, but it should be tagged:

- `selection_signal`: used to select final submission.
- `diagnostic_signal`: used to detect domain shift.
- `unsafe_signal`: used only after local validation failed and risk is explicit.

Do not let public LB silently replace validation.

## Operational contract

A split is complete only when it protects the unit that can leak, not merely the row ID. The leakage unit may be subject, patient, recording, molecule scaffold, document source, scene, time block, team, or generated sample family.

The split contract must state:

- the protected group;
- why the group can leak;
- whether external or pseudo-labeled data shares the same group;
- whether validation predictions are stored as OOF artifacts;
- what public/private mismatch remains.

## Split ledger

Track split experiments as:

```text
split_name
protected_unit
fold_count
metric_impl
public_lb_delta
oof_delta
known_leakage_risk
distribution_gap
accept_reject_reason
```

Never compare model changes across different split definitions without labeling the comparison as non-equivalent.

## Failure rule

If a split allows the same hidden entity to appear in train and validation, the validation score is not evidence. If a split is too strict and no longer resembles the hidden test distribution, the score is also not evidence.

## Transfer boundary

Transfer split logic by leakage unit, not by domain. Patient grouping from medical imaging may be closer to subject grouping in behavior detection than to ordinary image classification.

## Minimum viable workflow

1. Identify the entity that can leak across rows.
2. Define the protected split unit before training.
3. Check whether external or pseudo-labeled data shares that unit.
4. Store OOF predictions for every validation split.
5. Label public LB as selection, diagnostic, or unsafe signal.
6. Re-run split checks whenever data source or submission unit changes.

## Evidence routing

- Positive evidence: `[BIRD24-AUC]`, `[BIRD23-CMAP]`, `[CMI-BEHAVIOR]`, `[IMC-GEOMETRY]`, `[MITSUI]`, `[NFL-BDB]`.
- Negative evidence: `[BIRD24-AUC]`, `[MABE]`.
- Contrast evidence: `[RSNA-ANEURYSM]`, `[POLYMER-FEATURES]`, `[PHYSIONET-ECG]`.

## Source evidence

### Positive evidence
- [BIRD24-AUC]
- [BIRD23-CMAP]
- [CMI-BEHAVIOR]
- [IMC-GEOMETRY]
- [MITSUI]
- [NFL-BDB]

### Negative evidence
- [BIRD24-AUC]
- [MABE]

### Contrast evidence
- [RSNA-ANEURYSM]
- [POLYMER-FEATURES]
- [PHYSIONET-ECG]
