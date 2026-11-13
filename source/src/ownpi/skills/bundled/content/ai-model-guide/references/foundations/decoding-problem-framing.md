# Decoding Problem Framing

Problem framing decides which supervised benchmark evidence applies. Do this before choosing a model.

## Use this when

- A new supervised benchmark or dataset needs an initial task frame.
- The model choice feels premature.
- The metric, prediction unit, or leakage unit is unclear.

## Do not use this when

- The task frame is already fixed and the next question is a specific training, inference, or postprocess choice.

## Coverage note

Primary frames come from `[BENGALI-WER]`, `[BIRD24-AUC]`, `[MAP-K]`, `[POLYMER-FEATURES]`, `[IMC-GEOMETRY]`, and `[CMI-BEHAVIOR]`. Long-tail frame checks come from `[RNA3D]`, `[SANTA]`, `[CODE-GOLF]`, `[PHYSIONET-ECG]`, `[ARIEL]`, `[MITSUI]`, `[NFL-BDB]`, and `[RECODAI]`.

## Core knowledge

Kaggle tasks with similar data can require different systems if their metrics, submission units, or leakage units differ. The curated evidence set shows that winning systems often come from matching the task frame rather than picking a stronger backbone.

## Required frame

Fill these fields:

- `domain`: audio, CV, medical imaging, NLP, tabular, time-series, signal, retrieval, matching, scientific, LLM, or hybrid.
- `prediction_unit`: row, file, chunk, window, subject, soundscape, document, image pair, molecule, sequence, coordinate, cluster, or ranked list.
- `metric`: exact leaderboard metric.
- `submission_unit`: probability, text, rank list, class id, coordinate, mask, cluster, regression value, or file-level aggregate.
- `leakage_unit`: subject, file, scene, source, molecule, sequence, time block, document, or unknown.
- `inference_constraint`: time limit, memory limit, offline package, external model limit, or no known constraint.
- `dominant failure`: noisy labels, domain shift, long sequences, public/private mismatch, class imbalance, ambiguity, runtime bottleneck, or postprocess constraints.

## Case-derived frames

### `[BIRD24-AUC]` Audio event classification

BirdCLEF-style tasks are not just audio classification. The prediction often happens on windows/chunks, while the final metric may reward soundscape-level ranking. This explains why curated solutions combine 5-second windows, 10-second context, mel spectrograms, SED/CNN models, chunk aggregation, TTA, and soundscape-level postprocess.

### `[BENGALI-WER]` Speech recognition

Bengali.AI is sequence prediction evaluated by WER. The curated cases show that ASR model choice alone is insufficient: language model decoding, punctuation restoration, dynamic padding, long-audio handling, denoising decisions, and noisy-label filtering all affect the metric.

### `[MAP-K]` Ranking disguised as classification

MAP@K tasks can look like ordinary classification but are scored as ranked outputs. Local MAP evidence uses ambiguity handling, OOF soft labels, special-token logits, and output ranking logic. Treat the ranked list as the true output, not the raw class id.

### `[POLYMER-FEATURES]` Feature-centric regression

Open Polymer examples show that a tabular/scientific regression task may be won by data harmonization, descriptor generation, target-wise models, and feature selection rather than a single large neural architecture.

### `[IMC-GEOMETRY]` Matching and reconstruction

Image Matching solutions are pipelines: retrieval, orientation correction, local matching, clustering, outlier handling, and reconstruction. The model is only one part of the frame.

### `[CMI-BEHAVIOR]` Sensor or behavior detection

CMI-style behavior tasks need temporal, subject, and orientation constraints. Postprocess can encode impossible transitions or assignments, so the problem frame must include domain constraints.

### Long-tail frame rules

- `[RNA3D]`: if the output is a structure or coordinates, frame the task around geometric validity and template/coordinate transfer, not only regression.
- `[SANTA]` and `[CODE-GOLF]`: if the task is optimization or code search, frame it around objective, constraints, search space, and reproducibility rather than train/validation split.
- `[PHYSIONET-ECG]`: if the task extracts a signal from an image, separate image understanding from downstream signal reconstruction.
- `[ARIEL]`: if the metric includes uncertainty or likelihood, frame prediction as value plus uncertainty, not a point estimate only.
- `[MITSUI]` and `[NFL-BDB]`: if data is chronological or event-based, include time/order and group leakage in the frame.

## Framing mistakes

- Treating the model input unit as the submission unit.
- Treating classification loss as enough for MAP@K or AUC without ranking/calibration checks.
- Ignoring subject/scene/file leakage when records are not independent.
- Designing training without planning inference aggregation.
- Using public LB as validation without marking the risk.

## Mature framing pattern

A competition should be represented as a typed contract:

```text
task_contract = {
  input_unit,
  label_unit,
  prediction_unit,
  submission_unit,
  metric_behavior,
  validation_unit,
  leakage_unit,
  inference_unit,
  postprocess_unit,
  runtime_limit
}
```

Do not allow the model plan to proceed until these fields are filled. Most cross-domain mistakes come from mixing these units. In `[BIRD24-AUC]`, the model sees windows but the solution reasons about soundscapes. In `[BENGALI-WER]`, the model emits tokens but the metric scores edit distance after decoding and punctuation. In `[IMC-GEOMETRY]`, pairwise matches are not the final object; scene-level geometry is.

## Transfer boundary

If two competitions share a model family but not a metric or output unit, transfer only low-level implementation patterns. If they share metric behavior and output unit, transfer validation and postprocess patterns more confidently.

## Operational contract

A problem frame is complete only when it names:

- the object that is predicted;
- the object that owns the label;
- the object used by the metric;
- the object written to the submission file;
- the hidden grouping unit that validation must respect;
- the constraint that can make an otherwise good prediction invalid.

The frame must be written before model selection. If the frame changes after EDA, update validation, baseline, inference, and postprocess plans together.

## Framing ledger

Record each framing decision as:

```text
decision
evidence_id
unit_affected
alternative_frame
reason_rejected
downstream_change
risk_if_wrong
```

Use this ledger when a competition has multiple plausible frames, such as frame-level versus event-level behavior detection, clip-level versus soundscape-level audio prediction, or candidate classification versus ranked retrieval.

## Failure rule

Treat a plan as unsafe if it cannot explain how predictions move from training unit to metric unit. Most cross-competition transfer errors start here: the model learns the source-domain unit, but the target competition scores a different unit.

## Minimum viable workflow

1. Read the metric, data schema, and submission format.
2. Name task unit, label unit, metric unit, validation unit, inference unit, and postprocess unit.
3. Pick one close evidence ID and one contrast evidence ID from `../evidence/case-index.md`.
4. Write the `task_contract` before choosing a model.
5. Reject any model plan that cannot explain how predictions move from training unit to submission unit.
6. Update downstream validation, baseline, inference, and postprocess plans if the frame changes.

## Evidence routing

- Positive evidence: `[BENGALI-WER]`, `[BIRD24-AUC]`, `[MAP-K]`, `[CMI-BEHAVIOR]`, `[POLYMER-FEATURES]`, `[IMC-GEOMETRY]`.
- Negative evidence: `[ASL-FS]`, `[MAP-K]`.
- Contrast evidence: `[RNA3D]`, `[SANTA]`, `[CODE-GOLF]`.

## Source evidence

### Positive evidence
- [BENGALI-WER]
- [BIRD24-AUC]
- [MAP-K]
- [CMI-BEHAVIOR]
- [POLYMER-FEATURES]
- [IMC-GEOMETRY]

### Negative evidence
- [ASL-FS]
- [MAP-K]

### Contrast evidence
- [RNA3D]
- [SANTA]
- [CODE-GOLF]
