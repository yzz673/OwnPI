# Prediction And Test-Time Protocol

Prediction is part of the solution design.

## Use this when

- Designing benchmark prediction.
- Adding TTA, acceleration, chunking, windowing, or ensemble inference.
- Debugging timeout, memory, or public/private drift from inference aggregation.

## Do not use this when

- The model has not produced a valid baseline prediction yet.

## Coverage note

Inference/TTA anchors are `[BIRD24-AUC]`, `[BENGALI-WER]`, `[MAP-K]`, `[WAVEFORM-TTA]`, and `[IMC-GEOMETRY]`. Long-tail inference checks should include `[SANTA]`, `[CODE-GOLF]`, `[PHYSIONET-ECG]`, `[ARIEL]`, `[RNA3D]`, and `[MITSUI]`.

## Core knowledge

The model output is rarely the final submission. Kaggle inference often includes:

- chunking or windowing;
- overlapping context;
- TTA;
- model acceleration;
- ensemble aggregation;
- postprocess;
- timeout guards.

Design inference before final model selection.

## Inference planning checklist

- Define model input unit and submission output unit.
- Define chunk/window length and overlap.
- Define how multiple predictions map to one submission row.
- Benchmark runtime with the final ensemble and TTA.
- Check memory and disk footprint.
- Decide fallback path if the full ensemble cannot finish.
- Record whether acceleration actually helps on the target backend.

## Mature inference contract

Write an inference contract:

```text
inference_contract = {
  input_unit,
  model_unit,
  output_unit,
  aggregation_rule,
  tta_rule,
  acceleration_backend,
  batch_size,
  max_runtime,
  memory_budget,
  fallback_path
}
```

This contract should be tested before final model selection. A model that needs an unavailable backend or exceeds runtime is not a valid final model.

## TTA maturity levels

- `level 0`: no TTA, single deterministic prediction.
- `level 1`: simple flip/shift/resize or window shift.
- `level 2`: multiple windows/chunks with explicit aggregation.
- `level 3`: multiple checkpoints/folds/seeds with runtime guard.
- `level 4`: task-aware TTA tied to metric behavior and validated postprocess.

## Case-derived patterns

### `[BIRD24-AUC]` Audio windows and soundscapes

BirdCLEF solutions use 5-second windows, 10-second context chunks, shifted-window TTA, overlapping framewise averaging, neighboring-window weighting, soundscape-level aggregation, and global/file-level averages. These are not optional add-ons; they align window-level model outputs with soundscape-level scoring.

### `[BENGALI-WER]` Speech chunking

Bengali.AI solutions handle long audio, long token sequences, chunk length, stride, language model decoding, punctuation restoration, and dynamic padding. A direct full-file forward pass can fail or produce worse WER.

### `[BIRD24-AUC]` Acceleration

OpenVINO and ONNX appear in curated solutions. OpenVINO helps in some BirdCLEF cases, including INT8 quantization, but another BirdCLEF case reports OpenVINO was not always faster than ONNX. Benchmark instead of assuming.

### `[MAP-K]` LLM/ranking inference

MAP/LLM-style tasks use vLLM, AWQ, special-token logits, and model-size/runtime tradeoffs. Local evidence reports that a 70B model was too slow in one setting.

### `[WAVEFORM-TTA]` Checkpoint and prediction aggregation

Waveform inversion uses saved checkpoint predictions, medians, and run averaging under time pressure. This is TTA-like robustness over training checkpoints rather than input transforms.

### Long-tail inference rules

- `[SANTA]` and `[CODE-GOLF]`: inference is search or solver execution; track search budget and determinism.
- `[PHYSIONET-ECG]`: inference must preserve signal reconstruction quality after image extraction.
- `[ARIEL]`: inference must emit calibrated scientific predictions when the metric includes likelihood or uncertainty.
- `[MITSUI]`: time-series/market inference should preserve chronological feature availability.

## TTA decision rule

Use TTA only if it changes the metric-relevant evidence:

- shifted windows for audio;
- multiple image sizes or feature counts for matching;
- prompt/model diversity for LLM tasks when source-backed;
- checkpoint prediction medians when source-backed.

## Operational contract

Inference is complete only when the submitted prediction can be reproduced from a saved configuration:

- model checkpoints;
- input preprocessing;
- chunk or window policy;
- batch size and runtime backend;
- TTA transforms;
- aggregation rule;
- postprocess rule;
- timeout guard;
- final submission validation.

The inference contract should be tested before final training, because some training choices are invalid if the model cannot run under submission limits.

## Inference ledger

Track each inference variant as:

```text
inference_id
checkpoint_set
backend
precision
window_or_chunk_policy
TTA_policy
aggregation_rule
runtime
metric_delta
submission_status
```

This ledger should record both speed and score. Faster inference that changes the effective model must be treated as a separate method.

## Failure rule

Reject an inference variant if it improves validation only because validation used a different aggregation rule, exceeds time budget, or cannot reproduce the exact submission file.

## Transfer boundary

Transfer TTA and acceleration only when the target data has the same invariances and runtime constraints. OpenVINO, ONNX, vLLM, quantization, or checkpoint medians are tools, not universal improvements.

## Minimum viable workflow

1. Define the inference unit and submission unit.
2. Build the non-TTA inference path and measure runtime.
3. Add chunking, windows, checkpoint averaging, or backend acceleration one at a time.
4. Validate aggregation with the same metric unit used by the leaderboard.
5. Test final submission generation from saved config.
6. Keep a fallback inference path when TTA or acceleration is unstable.

## Evidence routing

- Positive evidence: `[BENGALI-WER]`, `[BIRD24-AUC]`, `[BIRD23-CMAP]`, `[MAP-K]`, `[WAVEFORM-TTA]`, `[IMC-GEOMETRY]`.
- Negative evidence: `[BIRD23-CMAP]`, `[MAP-K]`.
- Contrast evidence: `[SANTA]`, `[PHYSIONET-ECG]`, `[ARIEL]`, `[MITSUI]`.

## Source evidence

### Positive evidence
- [BENGALI-WER]
- [BIRD24-AUC]
- [BIRD23-CMAP]
- [MAP-K]
- [WAVEFORM-TTA]
- [IMC-GEOMETRY]

### Negative evidence
- [BIRD23-CMAP]
- [MAP-K]

### Contrast evidence
- [SANTA]
- [PHYSIONET-ECG]
- [ARIEL]
- [MITSUI]
