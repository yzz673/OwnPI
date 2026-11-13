# Semi-Supervised Label Expansion Audit Template

Use this before training on pseudo labels or any semi-supervised label expansion.

## Source

- Pseudo-label data source:
- Teacher model:
- Teacher validation score:
- Teacher saw validation rows: yes / no / unknown
- OOF generation used: yes / no

## Selection rule

- Confidence metric:
- Threshold:
- Agreement rule:
- Selected samples:
- Rejected samples:
- Class/target distribution change:

## Training use

- Added to train:
- Weighted:
- Stage:
- Mixed with original labels:
- Label smoothing or soft labels:

## Validation

| run | baseline score | pseudo-label score | delta | held-out/evaluator score | runtime | note |
|---|---:|---:|---:|---:|---:|---|

## Failure checks

- Leakage checked:
- Class imbalance checked:
- Easy-only selection checked:
- Distribution mismatch checked:
- Held-out-only or evaluator-only improvement risk:

## Source evidence

- Add evidence IDs and public-facing source notes here.
