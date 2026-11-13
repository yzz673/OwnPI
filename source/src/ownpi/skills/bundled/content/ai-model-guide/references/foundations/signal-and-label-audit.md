# Signal And Label Audit

Signal and label audit is a modeling step.

## Use this when

- Before baseline training.
- After validation and held-out/evaluator behavior diverge.
- Before adding external data, pseudo labels, or generated data.

## Do not use this when

- The current task is only to benchmark an already frozen model on a frozen dataset.

## Coverage note

Data-audit rules are anchored by `[BENGALI-WER]`, `[BIRD24-AUC]`, `[POLYMER-FEATURES]`, `[MDC-EXTRACT]`, and `[CSIRO]`. Long-tail audit checks should use `[PHYSIONET-ECG]`, `[RSNA-ANEURYSM]`, `[VESUVIUS]`, `[MABE]`, `[ARIEL]`, and `[MITSUI]`.

## Core knowledge

Do not treat data audit as a passive description. Convert it into decisions:

- which rows to trust;
- which labels to relabel or remove;
- which external sources to accept;
- which distribution shifts to model;
- which sample units require special inference handling.

## Audit dimensions

### `[BENGALI-WER]` Label quality

Check for noisy annotations, inconsistent labels, weak labels, ambiguous labels, and model-generated labels. Bengali.AI examples show that noisy audio/annotation pairs can make local WER diverge from public LB, and that filtering by WER or metadata can improve the training set.

### Sample validity

Check duration, length, missing modalities, invalid files, extreme feature values, impossible labels, corrupt images/audio, and sequence truncation. Local speech evidence includes removing very short audio and handling audio longer than model limits.

### `[POLYMER-FEATURES]` External data compatibility

External data can help, but only after source and distribution checks. Local BirdCLEF and Open Polymer examples show both external-data gains and cases where external data required harmonization or worsened public score.

### `[BIRD24-AUC]` Distribution shift

Look for train/public/private mismatch, domain shift, changed collection process, subject shift, scene shift, or synthetic-vs-real mismatch. BirdCLEF examples explicitly mention domain shift and public LB as a fallback CV signal.

### `[MDC-EXTRACT]` Metadata and side information

Metadata is not secondary. Local examples use MOS-like filters, published metadata, chemical descriptors, DOI/Accession patterns, subject/orientation information, and file-level aggregates.

## Reusable data-audit actions

- Build a file inventory and label schema.
- Produce length/duration/size histograms.
- Check duplicated rows, near duplicates, and repeated groups.
- Sample low-confidence and high-loss records.
- Compare external and host data distributions.
- Create a removal/relabel table with reason and metric effect.
- Keep a rejected-data log so failed cleaning can be reversed.

## Mature audit pattern

Do data audit as a decision table, not as screenshots:

| audit finding | affected unit | proposed action | evidence ID | validation check | rollback rule |
|---|---|---|---|---|---|

Examples:

- Short or broken audio -> remove, relabel, or route through special inference -> validate WER/AUC delta.
- External molecular descriptors -> harmonize or isolate external data -> validate target-wise regression.
- Medical image metadata -> group by patient/study if applicable -> validate leakage-safe split.
- Behavior keypoints/sensors -> check missingness and impossible orientation/state transitions -> validate behavior metric.

## Data intervention rule

Every data intervention must be reversible. Store the pre-filter count, post-filter count, reason, and metric delta. If a cleaning rule cannot be reversed or audited, it should not become official pipeline knowledge.

## Operational contract

A data audit is complete only when every usable source has a status:

- `train`: safe for supervised training;
- `validation_only`: useful for analysis but not training;
- `pseudo_label_candidate`: usable only after teacher filtering;
- `external_candidate`: needs harmonization and rule check;
- `exclude`: too noisy, leaking, duplicated, or incompatible.

The audit must produce an action, not only descriptive statistics.

## Data ledger

Track source decisions as:

```text
source_name
source_type
schema_match
distribution_match
label_quality
duplicate_risk
leakage_risk
intervention
validated_effect
final_status
```

The ledger should be updated whenever new external data, pseudo labels, synthetic data, or manual corrections are added.

## Failure rule

Reject a data intervention if it improves only one fold, only public LB, or only a metric proxy while worsening the competition metric. Extra data that changes the target distribution is a risk until proven otherwise.

## Transfer boundary

Transfer data-audit tactics only at the level of source quality logic. Do not transfer a source itself unless the competition rules, label semantics, and distribution match.

## Case-derived rules

- If annotations are noisy, train a cleaner model first, then use it to identify unreliable records.
- If external data has distribution mismatch, harmonize or isolate it instead of blindly concatenating.
- If data is sequence-like, audit length limits before training and inference.
- If metadata encodes sample quality, test it as a filtering feature.
- If a public writeup reports a failed cleaning or rescaling method, keep it in the risk log.
- `[PHYSIONET-ECG]`: for digitization tasks, audit both image quality and reconstructed signal validity.
- `[RSNA-ANEURYSM]`: for medical imaging, audit patient/study identity, image acquisition, label reliability, and class imbalance before modeling.
- `[MABE]`: for behavior detection, audit keypoint/sensor quality and whether semi-supervised labels actually improve behavior-level metric.
- `[ARIEL]`: for scientific signal tasks, audit noise model and uncertainty-related targets.

## Minimum viable workflow

1. List every official, external, synthetic, and pseudo-labeled source.
2. Assign each source a status: train, validation_only, pseudo_label_candidate, external_candidate, or exclude.
3. Check schema, distribution, label quality, duplicate risk, and leakage risk.
4. Propose one reversible intervention at a time.
5. Validate the intervention with the competition metric.
6. Keep rejected cleaning and rescaling attempts in the risk log.

## Evidence routing

- Positive evidence: `[BENGALI-WER]`, `[DEEP-PAST]`, `[CSIRO]`, `[MDC-EXTRACT]`, `[POLYMER-FEATURES]`.
- Negative evidence: `[BIRD24-AUC]`, `[MABE]`, `[POLYMER-FEATURES]`.
- Contrast evidence: `[PHYSIONET-ECG]`, `[RSNA-ANEURYSM]`, `[VESUVIUS]`.

## Source evidence

### Positive evidence
- [BENGALI-WER]
- [DEEP-PAST]
- [CSIRO]
- [MDC-EXTRACT]
- [POLYMER-FEATURES]

### Negative evidence
- [BIRD24-AUC]
- [MABE]
- [POLYMER-FEATURES]

### Contrast evidence
- [PHYSIONET-ECG]
- [RSNA-ANEURYSM]
- [VESUVIUS]
