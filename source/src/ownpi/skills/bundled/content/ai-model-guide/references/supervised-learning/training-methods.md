# Training Methods

For shared failure classification, training controls, and rejection criteria, read [training diagnosis and controls](../foundations/training-principles.md). This file retains the supervised and benchmark-specific sequence, examples, and training records, including OOF and label-based steps. They are not a mandatory starting sequence for every learning paradigm.

Training methods should be selected as controlled interventions against the metric and data failure mode.

## Use this when

- Designing optimizer, loss, augmentation, stage training, or checkpoint strategy.
- Training results diverge from the target metric.
- A larger model or longer training is being considered.

## Do not use this when

- The issue is purely prediction aggregation, postprocess/calibration, or final package documentation.

## Coverage note

Training-method anchors are `[BIRD24-AUC]`, `[BENGALI-WER]`, `[CSIRO]`, `[MAP-K]`, and `[WAVEFORM-TTA]`. Long-tail training checks should include `[DEEP-PAST]`, `[RNA3D]`, `[JIGSAW]`, `[MABE]`, `[AIMO]`, and `[ARIEL]`.

## Core knowledge

The curated evidence set supports a practical training sequence:

1. train a reproducible baseline;
2. fix data quality;
3. select loss and schedule from metric behavior;
4. add augmentation that matches the data unit;
5. stage training when data source or resolution changes;
6. save OOF predictions for pseudo-labeling, soft labels, stacking, and diagnostics;
7. record failed methods.

## Source-backed methods

### `[BIRD24-AUC]` Optimizer and schedule

AdamW and cosine annealing appear repeatedly in local top-solution cards, especially in audio and image-like tasks. Use them as reasonable candidates, not as required defaults.

### `[BIRD24-AUC]` CE/BCE and metric-aware classification

CE and BCE appear in BirdCLEF-style solutions. They are training surrogates; final metric behavior may depend on sigmoid use, min reduction, soundscape-level aggregation, or postprocess.

### `[BENGALI-WER]` and `[BIRD24-AUC]` Augmentation

Curated cases include CutMix, masking, SpecAug-like audio augmentation, noise mixing, speed changes, background sound, gain changes, and frame/window shifts. Augmentation must match the data unit; for example, audio augmentations are tied to clip/window handling.

### `[BENGALI-WER]` Loss weighting

Bengali.AI punctuation evidence reports that setting PAD loss weight to 0.0 improved CV. This pattern generalizes to: inspect ignored/padded/ambiguous labels and do not let them dominate the loss.

### `[WAVEFORM-TTA]` Multi-stage training

Local evidence includes stage training in waveform inversion, CSIRO, BirdCLEF pseudo-labeling, and Open Polymer workflows. Stage transitions should correspond to a real change: resolution, data source, pseudo labels, target, or validation focus.

### `[MAP-K]` Fine-tuning risk

MAP evidence includes full finetune overfitting and larger models hurting. Do not assume more parameters or more finetuning is better under ranking/runtime constraints.

### Long-tail training rules

- `[DEEP-PAST]`: synthetic/silver data and post-training can be central when low-resource text coverage is the bottleneck.
- `[JIGSAW]`: online distillation/train-on-test/prompt diversity should be treated as explicit training strategy, not incidental inference.
- `[MABE]`: negative semi-supervised evidence means behavior tasks need validation before adding unlabeled labels.
- `[AIMO]`: reasoning competitions require training/evaluation harness discipline; raw model size is not enough.

## Training log template

For each run, record:

- dataset version;
- split;
- loss;
- metric;
- augmentations;
- external data;
- pseudo-label source;
- schedule;
- checkpoint rule;
- OOF status;
- runtime;
- failure or improvement reason.

## Mature training pattern

Training should be staged by reason, not habit:

- `stage:data-clean`: train after removing or relabeling noisy data.
- `stage:representation`: train representation or feature extractor.
- `stage:supervised`: train on official labels.
- `stage:pseudo`: train with pseudo/silver data.
- `stage:resolution`: increase resolution, duration, frame count, or input size.
- `stage:finetune`: adapt final model with lower learning rate or selected data.
- `stage:submission`: train final folds/seeds/checkpoints under runtime constraints.

Each stage needs a different validation question. If two stages answer the same question, one of them is redundant.

## Training ledger

Track runs as:

```text
run_id
data_version
split_name
model_role
loss
augmentation
schedule
best_epoch
oof_metric
public_lb_metric
runtime_status
failure_or_keep_reason
```

The ledger should make it possible to compare two training changes without mixing data, split, and inference changes.

## Stage-to-failure examples

Use these examples to classify failed training runs before adding more methods.

| stage | likely failure | diagnosis | action |
|---|---|---|---|
| `stage:data-clean` | cleaning removes useful hard cases | fold gain is class-specific or disappears on public LB | keep original data baseline and isolate cleaning rule |
| `stage:representation` | representation loses metric-relevant detail | intermediate features look good but final metric does not move | compare downstream metric, not embedding quality |
| `stage:supervised` | model learns noisy or leaking labels | train score rises while OOF/public diverges | revisit data audit and split contract |
| `stage:pseudo` | teacher errors are amplified | pseudo-label gain is public-only or fold-fragile | raise confidence threshold or rollback pseudo set |
| `stage:resolution` | longer input improves local but breaks runtime | OOF improves but submission times out | reduce input, distill, or move gain into inference aggregation |
| `stage:finetune` | final finetune overfits selected folds | last checkpoint is worse than earlier checkpoint on hidden-like split | restore checkpoint selection rule |
| `stage:submission` | final training changes effective distribution | full-data model differs from fold models | compare full-data prediction distribution to OOF ensemble |

Do not add another training stage until the current failure has a named class. Otherwise the experiment ledger becomes a list of changes with no causal interpretation.

## Transfer boundary

Transfer training methods only when data noise, metric behavior, and inference unit are similar. A training trick for long noisy audio is not automatically valid for molecular descriptors, MAP@K ranking, or geometric reconstruction.

## Minimum viable workflow

1. Freeze data version, split version, and metric implementation.
2. Train the simplest supervised stage first.
3. Add data cleaning, augmentation, pseudo-labeling, or finetuning as separate stages.
4. Log fold score, public LB, runtime, and failure reason for every stage.
5. Select checkpoints with the competition metric after required inference aggregation.
6. Reject stages that erase earlier gains or cannot run at submission time.

## Evidence routing

- Positive evidence: `[BENGALI-WER]`, `[BIRD24-AUC]`, `[BIRD25-PL]`, `[ASL-KP]`, `[POLYMER-FEATURES]`, `[WAVEFORM-TTA]`.
- Negative evidence: `[BENGALI-WER]`, `[MAP-K]`, `[ASL-KP]`, `[MABE]`.
- Contrast evidence: `[SANTA]`, `[CODE-GOLF]`, `[IMC-GEOMETRY]`.

## Source evidence

### Positive evidence
- [BENGALI-WER]
- [BIRD24-AUC]
- [BIRD25-PL]
- [ASL-KP]
- [POLYMER-FEATURES]
- [WAVEFORM-TTA]

### Negative evidence
- [BENGALI-WER]
- [MAP-K]
- [ASL-KP]
- [MABE]

### Contrast evidence
- [SANTA]
- [CODE-GOLF]
- [IMC-GEOMETRY]
