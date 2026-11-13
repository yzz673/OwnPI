# Semi-Supervised Label Expansion

This strategy is separate from self-supervised learning. Apply it alongside the [shared guide](../README.md), especially data audit, split control, and validation. Record a proposed expansion with the [audit template](templates/semi_supervised_label_expansion_audit.md).

Pseudo-labeling or semi-supervised label expansion is a data-generation method. Treat it as optional and only use it when legal unlabeled/extra data, teacher quality, confidence rules, and leakage controls are explicit.

## Use this when

- There is unlabeled, external, or test-like data.
- A teacher or ensemble is strong enough to generate useful labels.
- The validation plan can prevent leakage.

## Do not use this when

- The teacher is weak, validation is unreliable, or selected pseudo labels cannot be audited.

## Coverage note

Pseudo-labeling is positively supported by `[BIRD24-AUC]`, `[BIRD25-PL]`, `[BENGALI-WER]`, `[DEEP-PAST]`, and `[CSIRO]`. Negative or cautionary evidence comes from `[MABE]`, `[MAP-K]`, and `[POLYMER-FEATURES]`.

## Core knowledge

Curated cases show pseudo-labeling can be decisive, especially in BirdCLEF-style audio and some text/scientific tasks. The same local corpus also contains failed pseudo-labeling or semi-supervised attempts. The difference is usually data match, teacher quality, confidence control, leakage control, and validation.

## When pseudo-labeling is plausible

Use pseudo-labeling when:

- unlabeled or weakly labeled data is close to the test distribution;
- the teacher is stronger than the baseline;
- confidence or filtering rules can be defined;
- leakage can be avoided through OOF or held-out generation;
- the metric benefits from broader distribution coverage;
- inference-time predictions can be converted back into training units.

## Pseudo-labeling workflow

1. Train a clean baseline or teacher ensemble.
2. Generate OOF predictions when training data is involved.
3. Generate test-like or external-data predictions separately.
4. Select pseudo labels by confidence, agreement, or metric-specific threshold.
5. Record selected count and class/target distribution.
6. Retrain with baseline data plus pseudo-labeled data.
7. Compare against baseline on the official metric.
8. Keep pseudo-labeled data versioned.

## Mature pseudo-label pattern

Pseudo labels need four ledgers:

1. `teacher ledger`: teacher model, folds, score, data seen, inference settings.
2. `selection ledger`: confidence rule, agreement rule, selected count, rejected count, class distribution.
3. `training ledger`: weight, stage, mixing ratio, hard/soft label choice, augmentations.
4. `validation ledger`: baseline delta, pseudo-label delta, public/private risk, failure sample.

Without these ledgers, pseudo-labeling becomes untraceable data mutation.

## Confidence policy

Confidence threshold should be task-specific:

- classification/ranking: probability, margin, entropy, or model agreement;
- sequence tasks: WER proxy, decoder confidence, length validity, punctuation validity;
- regression: uncertainty, ensemble variance, or target-range plausibility;
- behavior tasks: temporal consistency and impossible-action filters.

## Operational contract

Pseudo-labeling is complete only when it is treated as a controlled data release:

- teacher identity;
- unlabeled pool identity;
- confidence or consistency rule;
- class or target coverage;
- leakage check;
- student weighting;
- iteration number;
- validation and public-LB effect;
- rollback rule.

No pseudo-label set should enter training without a named teacher and an acceptance rule.

## Pseudo-label ledger

Track each generation as:

```text
iteration_id
teacher_model
target_pool
candidate_count
accepted_count
confidence_rule
balance_change
leakage_status
student_weight
validated_effect
next_action
```

This ledger is mandatory when pseudo labels are regenerated, filtered, mixed with official labels, or used for final training.

## Transfer boundary

Transfer pseudo-labeling only when the target competition has target-like unlabeled data and a teacher that is measurably better than the student. Do not transfer pseudo-labeling from audio or text to scientific regression without redefining confidence and leakage.

## Case-derived patterns

### `[BIRD24-AUC]` and `[BIRD25-PL]` BirdCLEF

Pseudo labels often appear with external data, 5-second windows, mel spectrograms, ensembling, TTA, and knowledge distillation. BirdCLEF+ 2025 includes writeups explicitly centered on pseudo labels and progressive pseudo-label fusion.

### `[BENGALI-WER]` Bengali.AI

Pseudo predictions are used to identify high-quality training examples and remove noisy records. This is pseudo-labeling as data cleaning, not only as extra training data.

### Deep Past silver data

Synthetic or silver data from back-translation and published texts can provide large gains when the task benefits from more text coverage.

### CSIRO online pseudo-labeling

Stage 2 online pseudo-labeling and test-time training appear as part of the training strategy.

### `[MAP-K]` MAP and LLM/ranking tasks

Pseudo labels, duplicate labels, rare-label synthetic data, and OOF soft labels can help ambiguity, but local evidence also reports distillation, rank loss, or synthetic data hurting in some configurations.

### MABe negative evidence

Pseudo-labeling and semi-supervised learning did not work in one local behavior-detection case. Do not generalize success across domains.

### Long-tail pseudo-label rules

- `[POLYMER-FEATURES]`: pseudo labels in scientific/tabular regression must be checked against target distribution and external-data harmonization.
- `[JIGSAW]`: text/community-rule tasks may use train-on-test or online distillation patterns, but they need leakage and policy checks.
- `[DEEP-PAST]`: silver/synthetic text can help low-resource translation when data quality dominates.

## Failure modes

- Teacher sees validation examples.
- Pseudo labels amplify class imbalance.
- Confidence threshold selects easy examples only.
- Synthetic data mismatches the private distribution.
- Pseudo labels improve public LB but hurt private-like validation.
- The pseudo-label pipeline changes multiple variables at once.

## Minimum viable workflow

1. Confirm that target-like unlabeled data exists and is legal to use.
2. Train or select a teacher with known validation behavior.
3. Generate candidate pseudo labels with saved teacher configuration.
4. Filter by confidence, agreement, uncertainty, or domain rule.
5. Train the student with explicit pseudo-label weights.
6. Stop or rollback if the gain is public-only, fold-fragile, or leakage-risky.

## Failure rule

Reject pseudo-labeling when teacher quality is unknown, confidence cannot be measured, target coverage is distorted, or validation cannot separate extra-data gain from leakage or public-LB overfitting.

## Evidence routing

- Positive evidence: `[BIRD25-PL]`, `[BIRD24-AUC]`, `[DEEP-PAST]`, `[CSIRO]`, `[JIGSAW]`, `[BENGALI-WER]`.
- Negative evidence: `[MABE]`, `[MAP-K]`.
- Contrast evidence: `[POLYMER-FEATURES]`, `[IMC-GEOMETRY]`, `[SANTA]`.

## Source evidence

### Positive evidence
- [BIRD25-PL]
- [BIRD24-AUC]
- [DEEP-PAST]
- [CSIRO]
- [JIGSAW]
- [BENGALI-WER]

### Negative evidence
- [MABE]
- [MAP-K]

### Contrast evidence
- [POLYMER-FEATURES]
- [IMC-GEOMETRY]
- [SANTA]
