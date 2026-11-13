# Model Design

Model design should be chosen from the task frame, not from architecture preference.

## Use this when

- Selecting architecture families.
- Selecting model tiers for supervised learning or benchmark tasks.
- Deciding whether the system needs retrieval, postprocess, teacher models, or feature engineering.
- Translating source-backed benchmark evidence into a new domain.

## Do not use this when

- The metric, data unit, and inference unit have not been framed yet.

## Coverage note

Model-design anchors are `[BIRD24-AUC]`, `[BENGALI-WER]`, `[ASL-KP]`, `[POLYMER-FEATURES]`, `[IMC-GEOMETRY]`, and `[MAP-K]`. Long-tail model rules come from `[RNA3D]`, `[VESUVIUS]`, `[PHYSIONET-ECG]`, `[ARIEL]`, `[CSIRO]`, `[RECODAI]`, `[SANTA]`, and `[CODE-GOLF]`.

## Core knowledge

Local top solutions often combine several model roles:

- representation model;
- task head;
- calibration or ranking layer;
- external-data or pseudo-label teacher;
- inference-time aggregator;
- rule-based postprocess.

The best design is usually a system, not a single backbone.

## Design templates from curated cases

### `[BIRD24-AUC]` Audio event classification system

Use when the task resembles BirdCLEF:

- input representation: mel spectrogram and sometimes raw signal;
- unit: 5-second windows with optional longer context;
- models: SED/CNN/EfficientNet-like backbones, sometimes ViT-style models;
- training: CE/BCE, AdamW, cosine schedule, CutMix/masking/background augmentations;
- inference: shifted windows, chunk averaging, TTA, OpenVINO/ONNX, precomputed mels;
- postprocess: soundscape-level aggregation, moving average, cutoff, neighboring-window weighting.

### `[BENGALI-WER]` Speech recognition system

Use when the metric is WER-like:

- pretrained ASR backbone such as wav2vec/Whisper-style model;
- CTC or sequence-decoding head;
- language model;
- punctuation model;
- chunking and stride;
- dynamic padding;
- noisy-label filtering;
- optional denoising selection.

### `[ASL-KP]` Keypoint/sign-language system

Use when inputs are landmarks or temporal pose:

- keypoint normalization;
- frame-length choice;
- CNN/MHSA/Transformer blocks;
- augmentation;
- seed/layer/dimension/max-length diversity for ensembles;
- reject larger or more complex variants if validation does not support them.

### `[POLYMER-FEATURES]` Feature-centric scientific regression system

Use when structured features dominate:

- domain descriptors;
- external data harmonization;
- target-wise LightGBM/CatBoost/XGBoost;
- feature selection by importance or target;
- simple robust ensemble before deep architecture.

### `[IMC-GEOMETRY]` Matching/reconstruction system

Use when output depends on pairwise or scene-level geometry:

- global retrieval or shortlist;
- orientation correction;
- local feature detector/descriptor;
- matcher;
- graph clustering;
- outlier rejection;
- reconstruction/pose scoring.

### `[MAP-K]` LLM/ranking system

Use when task is text reasoning or ranked output:

- base classifier or LLM;
- special-token logits or ranked probabilities;
- vLLM/AWQ if runtime requires;
- soft-label or ambiguity-aware training;
- avoid over-large models if curated evidence shows runtime or overfit failure.

### Long-tail model rules

- `[RNA3D]`: include template/geometric reasoning when the output is a 3D biological structure.
- `[VESUVIUS]`: scientific/CV surface detection may need reconstruction-aware model design, not only image classification.
- `[PHYSIONET-ECG]`: image-to-signal tasks should separate visual extraction from signal reconstruction.
- `[ARIEL]`: scientific regression may need uncertainty-aware output design.
- `[SANTA]` and `[CODE-GOLF]`: optimization/code competitions require solver/search design, not only predictive modeling.

## Design decision rule

Choose the simplest design that covers:

1. input representation;
2. metric target;
3. validation unit;
4. inference unit;
5. postprocess constraints.

Only add another component when it fixes a named failure mode.

## Mature model-design pattern

Represent the model as roles:

| role | question | examples |
|---|---|---|
| representation | What makes raw data learnable? | mel spectrogram, keypoints, descriptors, text tokens, image embeddings |
| predictor | What maps representation to target? | CNN/SED, ASR, tree model, LLM, matching model |
| teacher | Is there a stronger model or external signal? | pseudo-label ensemble, distillation teacher |
| aggregator | How do multiple units become one submission? | window average, rank list, coordinate merge |
| constraint layer | What output rules must hold? | Hungarian assignment, geometry, valid labels |
| runtime layer | Can it finish inside the benchmark constraints? | OpenVINO, ONNX, vLLM, search budget |

This role-based design makes cross-domain transfer safer. `[BIRD24-AUC]` and `[PHYSIONET-ECG]` may both use image-like models, but their aggregator and constraint layers differ. `[SANTA]` and `[CODE-GOLF]` may not have a predictor at all; the core is solver/search design.

## Add-component rule

Add a component only if it has a named job:

- language model fixes sequence decoding;
- punctuation model fixes WER text format;
- pseudo-label teacher fixes data coverage;
- geometry clustering fixes scene consistency;
- feature engineering fixes missing scientific descriptors;
- acceleration fixes runtime.

## Operational contract

A model design is complete only when every component has a role, input, output, validation target, and failure mode.

Use this component record:

```text
component_name
role
input_unit
output_unit
metric_dependency
validation_signal
runtime_cost
replacement_if_failed
```

Do not add a model because it is strong in another competition. Add it only if it fills a missing role in the target system.

## Model-design ledger

For each proposed component, track:

```text
evidence_id
component_role
expected_gain
observed_gain
fold_stability
ensemble_value
runtime_cost
keep_drop_decision
```

This ledger should include failed variants, because negative design evidence often transfers across domains.

## Failure rule

Reject a model design when it increases complexity without changing a named failure mode. Bigger model, extra pretraining, distillation, reranking, or more features are not improvements unless the validation ledger shows what problem they solved.

## Transfer boundary

Transfer roles across competitions more readily than architectures. An ASR language model, a MAP@K ranker, and a geometry constraint layer are all downstream alignment components, but their implementations are not interchangeable.

## Minimum viable workflow

1. Start from the benchmark contract, not a preferred backbone.
2. List missing model roles: representation, predictor, teacher, aggregator, constraint, runtime.
3. Select the simplest component for each required role.
4. Attach validation target and runtime cost to every component.
5. Keep failed variants in the design ledger.
6. Add a new component only when it fixes a named failure mode.

## Evidence routing

- Positive evidence: `[BIRD24-AUC]`, `[BENGALI-WER]`, `[ASL-KP]`, `[POLYMER-FEATURES]`, `[IMC-GEOMETRY]`, `[MAP-K]`.
- Negative evidence: `[ASL-KP]`, `[MAP-K]`.
- Contrast evidence: `[RNA3D]`, `[VESUVIUS]`, `[PHYSIONET-ECG]`, `[ARIEL]`, `[SANTA]`, `[CODE-GOLF]`.

## Source evidence

### Positive evidence
- [BIRD24-AUC]
- [BENGALI-WER]
- [ASL-KP]
- [POLYMER-FEATURES]
- [IMC-GEOMETRY]
- [MAP-K]

### Negative evidence
- [ASL-KP]
- [MAP-K]

### Contrast evidence
- [RNA3D]
- [VESUVIUS]
- [PHYSIONET-ECG]
- [ARIEL]
- [SANTA]
- [CODE-GOLF]
