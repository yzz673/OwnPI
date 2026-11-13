# Decoding Validation Plan Template

Use this before trusting a score.

## Metric

- Benchmark metric:
- Local implementation:
- Higher/lower is better:
- Postprocess included in validation: yes / no

## Split

- Independent unit:
- Grouping unit:
- Fold method:
- Number of folds:
- Random seeds:
- Leakage controls:

## Robustness labels

Use labels from `../references/foundations/validation-robustness.md`.

- Current experiment label: validated / held-out-backed / evaluator-only / unstable / runtime-risk / source-weak
- Reason:

## Score table

| run | data version | model | postprocess | fold score | held-out score | evaluator score if known | runtime | note |
|---|---|---|---|---:|---:|---:|---:|---|

## Decision

- Accept:
- Reject:
- Needs rerun:
- Risk note:

## Source evidence

- Add evidence IDs and public-facing source notes here.
