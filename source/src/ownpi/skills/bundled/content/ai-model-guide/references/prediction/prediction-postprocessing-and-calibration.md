# Prediction Postprocessing And Calibration

Postprocess and calibration are metric-aware output construction. They should be validated like model components and never tuned across leakage boundaries.

## Use this when

- Raw model output is not the legal or metric-optimal benchmark output.
- Domain constraints, ranking rules, smoothing, assignment, extraction, or thresholds are source-backed.
- A final package needs postprocess documentation.

## Do not use this when

- There is no ablation for the postprocess.
- The postprocess threshold was tuned only on public LB without a risk note.

## Coverage note

Postprocess anchors are `[BIRD24-AUC]`, `[BENGALI-WER]`, `[CMI-BEHAVIOR]`, `[MDC-EXTRACT]`, `[IMC-GEOMETRY]`, `[POLYMER-FEATURES]`, and `[WAVEFORM-TTA]`. Long-tail postprocess checks should include `[PHYSIONET-ECG]`, `[VESUVIUS]`, `[RECODAI]`, `[ARIEL]`, `[SANTA]`, and `[CODE-GOLF]`.

## Core knowledge

Postprocess can encode:

- legal output constraints;
- temporal or file-level consistency;
- ranking rules;
- confidence thresholds;
- smoothing;
- assignment constraints;
- metadata extraction;
- domain-specific corrections.

It should not be used to hide an unvalidated model.

## Postprocess design procedure

1. Define raw model output.
2. Define legal submission output.
3. Identify source-backed constraints.
4. Choose one postprocess step.
5. Validate the step alone.
6. Record parameter values and risk.
7. Keep an ablation with postprocess disabled.

## Mature postprocess contract

Every postprocess step should have:

```text
postprocess_contract = {
  raw_output,
  transformed_output,
  rule,
  parameters,
  metric_reason,
  validation_delta,
  failure_mode,
  disable_switch
}
```

If `validation_delta` is unknown, the postprocess is a hypothesis. If `disable_switch` is missing, the pipeline is hard to debug.

## Postprocess categories

- `aggregation`: combine windows, chunks, folds, files, or soundscapes.
- `constraint repair`: enforce legal labels, assignments, geometry, sequence, or domain rules.
- `calibration`: adjust probabilities, logits, thresholds, or priors.
- `extraction`: convert text or metadata into structured labels.
- `smoothing`: stabilize temporal or spatial predictions.
- `compression`: repair or shorten output for optimization/code tasks.

## Case-derived patterns

### `[BIRD24-AUC]` BirdCLEF soundscape postprocess

Curated cases use moving averages, neighboring-window weighting, global averages, file-level averages, max/mean soundscape aggregation, confidence cutoffs, and min reduction. These convert noisy window-level predictions into soundscape-aware scores.

### `[BENGALI-WER]` Speech recognition postprocess

Bengali.AI cases use language-model decoding, punctuation restoration, beam width tuning, denoising selection, dynamic padding, and long-audio handling. These directly affect WER.

### `[CMI-BEHAVIOR]` Behavior detection constraints

CMI cases use subject/orientation constraints, Hungarian assignment, prediction-history logic, and hard probability rules. This converts local predictions into behavior sequences that satisfy domain constraints.

### `[MAP-K]` Ranking correction

MAP-style tasks use label priors, question-misconception maps, soft labels, and probability masking. The goal is to produce a ranked list that respects ambiguity and known invalid pairs.

### `[MDC-EXTRACT]` Retrieval/extraction rules

Make Data Count uses DOI/Accession extraction, metadata, retrieval plus classification routing, and heuristic postprocess. Rule-based extraction is part of the model system.

### `[IMC-GEOMETRY]` Matching geometry

Image Matching uses orientation thresholds, similarity graph pruning, clustering thresholds, outlier handling, and min-pair tuning. These steps affect both recall and precision of reconstruction.

### `[POLYMER-FEATURES]` Scientific regression corrections

Open Polymer includes both Tg postprocess and no-postprocess choices. Treat target-specific correction as a risky metric intervention, not a default.

### Long-tail postprocess rules

- `[PHYSIONET-ECG]`: postprocess should preserve waveform validity after digitization.
- `[VESUVIUS]`: scientific surface-detection postprocess should be evaluated against reconstruction/segmentation objective, not visual smoothness alone.
- `[RECODAI]`: image-forgery postprocess must not erase forensic evidence or create label leakage.
- `[SANTA]` and `[CODE-GOLF]`: postprocess is constraint repair or code/solution compression; validate final objective after repair.

## Failure modes

- Postprocess tuned only on public LB.
- Threshold selected without an ablation.
- Rule uses test information that would leak labels.
- Postprocess improves one class or target while damaging the aggregate metric.
- Runtime of postprocess is not included in submission timing.

## Operational contract

A postprocess is complete only when it states:

- the exact prediction field it changes;
- the unit affected;
- the rule source;
- the metric effect;
- the validation ablation;
- the rollback condition.

Postprocess is part of the model system. It must be versioned with inference and submission artifacts.

## Postprocess ledger

Track rules as:

```text
rule_id
input_field
output_field
target_unit
constraint_source
validation_delta
public_lb_delta
failure_case_fixed
failure_case_created
keep_drop_decision
```

This ledger should include rejected postprocess rules to prevent repeated public-LB overfitting.

## Transfer boundary

Transfer postprocess only when the constraint source matches. Text normalization, audio smoothing, Hungarian assignment, geometry filtering, and metadata repair solve different constraint problems.

## Minimum viable workflow

1. Identify which field, unit, or constraint the postprocess changes.
2. Write the rule source: metric, submission format, domain law, or empirical validation.
3. Run an ablation against the official-like metric.
4. Check whether the rule creates new invalid outputs.
5. Include postprocess runtime in final inference timing.
6. Store rejected rules to prevent repeated public-LB tuning.

## Failure rule

Reject postprocess when it is tuned only on public LB, uses hidden test information, fixes one subgroup while damaging the aggregate metric, or cannot be reproduced from the final inference config.

## Evidence routing

- Positive evidence: `[BENGALI-WER]`, `[BIRD24-AUC]`, `[ASL-FS]`, `[CMI-BEHAVIOR]`, `[MDC-EXTRACT]`, `[IMC-GEOMETRY]`.
- Negative evidence: `[BIRD24-AUC]`, `[POLYMER-FEATURES]`.
- Contrast evidence: `[PHYSIONET-ECG]`, `[VESUVIUS]`, `[RECODAI]`, `[SANTA]`, `[CODE-GOLF]`.

## Source evidence

### Positive evidence
- [BENGALI-WER]
- [BIRD24-AUC]
- [ASL-FS]
- [CMI-BEHAVIOR]
- [MDC-EXTRACT]
- [IMC-GEOMETRY]

### Negative evidence
- [BIRD24-AUC]
- [POLYMER-FEATURES]

### Contrast evidence
- [PHYSIONET-ECG]
- [VESUVIUS]
- [RECODAI]
- [SANTA]
- [CODE-GOLF]
