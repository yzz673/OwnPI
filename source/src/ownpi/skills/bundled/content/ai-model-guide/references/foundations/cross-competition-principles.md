# Cross-Competition Principles

This file extracts common operating principles across the curated Kaggle evidence set. Use it before reading domain-specific topic files.

The goal is not to memorize competition tricks. The goal is to recognize the same competition machinery across audio, speech, keypoints, text, scientific regression, imaging, matching, optimization, and behavior detection.

## Use this when

- Starting a new Kaggle-like competition.
- Translating a tactic from one domain into another.
- Deciding whether a top-solution idea is generally reusable or domain-specific.
- Auditing whether a plan is missing validation, data, inference, or postprocess contracts.

## Do not use this when

- You need exact implementation details for a single competition.
- You have not read the competition rules, metric, data schema, and submission format.
- You are trying to justify a tactic that has not been tested in the target competition.

## Core idea

Across competitions, winning systems usually converge to the same structure:

```text
competition contract
  -> data and split contract
  -> metric-to-loss contract
  -> representation and model-role contract
  -> training and validation ledger
  -> inference and submission contract
  -> postprocess and constraint contract
  -> final evidence package
```

Model architecture is only one part of this structure. Many top solutions win because the contracts around the model are stronger than the backbone itself.

## Principle 1: Define the competition contract before modeling

Every competition should be reduced to units:

| unit | question | common failure |
|---|---|---|
| task unit | What object is the model asked to solve? | training on a unit that does not match evaluation |
| label unit | What object owns the label? | leaking labels across related objects |
| metric unit | What object is scored? | optimizing the wrong aggregation level |
| submission unit | What object appears in the submission file? | postprocess fixes one unit but breaks another |
| validation unit | What object must be split together? | public/private mismatch |
| inference unit | What object is processed at inference time? | timeout, missing context, unstable aggregation |
| constraint unit | What rule makes outputs legal? | invalid labels, impossible geometry, duplicate assignments |

Examples from the evidence set:

- `[BIRD24-AUC]` separates audio clip, five-second window, soundscape, species score, and inference runtime.
- `[BENGALI-WER]` separates audio chunk, transcript, normalized text, language-model output, and WER scoring.
- `[MAP-K]` separates problem statement, candidate misconceptions, ranked outputs, and MAP@K scoring.
- `[IMC-GEOMETRY]` separates image pair, scene graph, local feature match, clustering, and reconstruction score.
- `[CMI-BEHAVIOR]` separates sensor frame, sequence, subject, behavior event, and legal assignment.
- `[SANTA]` and `[CODE-GOLF]` show that some competitions are not predictive modeling tasks at all; the central unit is a search or optimization state.

Transfer rule:

Do not transfer a method until the source and target competition have compatible units. A BirdCLEF windowing trick does not transfer to MAP@K unless the target has an analogous inference aggregation unit. A CMI Hungarian assignment does not transfer unless the target has an explicit conflict or uniqueness constraint.

## Principle 2: Treat validation as a simulator

Validation is not a score table. It is a simulator for hidden evaluation.

A mature validation plan defines:

```text
split_unit
hidden_group_risk
time_or_subject_leakage_risk
metric_implementation
public_lb_reliability
fold_count
OOF_storage
shake_up_risk
decision_threshold
```

Common validation patterns:

- group split when subject, recording, source, patient, document, scene, or molecule family can leak;
- time split when future distribution differs from training distribution;
- scene or entity split for matching, reconstruction, behavior, and sports tracking tasks;
- OOF prediction storage for pseudo-labeling, ensembling, calibration, and threshold search;
- public-LB risk tags when local validation is weak or impossible.

Evidence:

- `[BIRD24-AUC]`, `[BIRD23-CMAP]`, and `[BIRD22-WCA]` show metric and public/private behavior can shift across BirdCLEF-like tasks.
- `[MAP-K]` shows that ranked-output validation must match MAP@K behavior, not only classification accuracy.
- `[POLYMER-FEATURES]` shows target-wise validation and feature handling matter in scientific regression.
- `[IMC-GEOMETRY]` shows pair/scene-level validation differs from ordinary image classification.
- `[CMI-BEHAVIOR]` shows subject and sequence constraints can dominate apparent metric movement.

Failure rule:

If validation cannot explain why a method improves, the result is not evidence yet. It is a leaderboard observation or a hypothesis.

## Principle 3: Data quality is a model component

Across competitions, data work is not a preprocessing footnote. It changes what the model can learn.

A reusable data-quality ledger should record:

```text
source
license_or_rule_status
distribution_match
label_noise_type
duplicate_or_near_duplicate_risk
missingness
outlier_policy
external_data_mapping
synthetic_data_mapping
sample_weight_or_filter_reason
validated_effect
```

Common interventions:

- remove or downweight noisy labels;
- normalize text, transcript, keypoints, molecular features, or sensor streams;
- harmonize external data to the competition schema;
- split high-confidence and low-confidence pseudo labels;
- detect records that should be validation-only, training-only, or inference-only;
- track whether an intervention helps OOF, public LB, private LB, or only one fold.

Evidence:

- `[BENGALI-WER]` uses noisy-label filtering and text normalization.
- `[DEEP-PAST]` uses silver/synthetic/back-translation style data expansion under quality constraints.
- `[CSIRO]` uses auxiliary targets, mislabeled-sample removal, and online pseudo-labeling ideas.
- `[POLYMER-FEATURES]` relies on external-data harmonization and descriptor quality.
- `[MABE]` provides negative evidence that pseudo/semi-supervised additions can fail.
- `[MDC-EXTRACT]` shows metadata and extraction rules can be as important as the classifier.

Transfer rule:

Extra data is not automatically useful. It must pass source compatibility, label compatibility, distribution compatibility, and validation compatibility.

## Principle 4: Convert the metric into a training and inference contract

The metric decides more than the loss. It affects representation, validation, aggregation, postprocess, and ensemble space.

Metric contract:

```text
leaderboard_metric
metric_unit
metric_direction
loss_proxy
calibration_need
threshold_need
ranking_need
sequence_decoding_need
uncertainty_need
postprocess_sensitivity
```

Examples:

- `[BENGALI-WER]`: WER needs sequence decoding, text normalization, language modeling, and punctuation decisions.
- `[BIRD24-AUC]`: AUC can tolerate threshold-free ranking, but inference aggregation and score calibration still matter.
- `[BIRD23-CMAP]`: buffered_cmAP creates threshold and time-window pressure.
- `[MAP-K]`: MAP@K needs ranked candidates, ambiguity handling, and ranking-aware outputs.
- `[ARIEL]`: scientific regression with uncertainty/log-likelihood needs output uncertainty, not only point estimates.
- `[CMI-BEHAVIOR]`: event constraints and assignment rules can affect metric more than raw frame-level probability.

Failure rule:

Do not call a model better unless the improvement is measured in the same output space the leaderboard uses. A better frame classifier may produce a worse event-level submission. A better single-label classifier may produce worse MAP@K ranking.

## Principle 5: Design models by role, not by architecture name

Across competitions, the architecture name is less reusable than the role it plays.

Use this role table:

| role | purpose | examples |
|---|---|---|
| representation model | make raw input learnable | mel spectrogram, keypoints, descriptors, tokens, embeddings |
| predictor | map representation to target | CNN/SED, ASR, tree ensemble, LLM, matcher |
| teacher | produce stronger or broader labels | ensemble teacher, pseudo-label teacher, distillation teacher |
| calibration/ranking layer | align scores with metric | sigmoid calibration, ranked logits, MAP@K candidate ordering |
| aggregator | combine smaller units into submission unit | window average, rank blend, coordinate merge, target-wise average |
| constraint layer | enforce legal output structure | Hungarian assignment, geometry, label uniqueness, text normalization |
| runtime layer | make inference feasible | ONNX, OpenVINO, vLLM, quantization, shortlist search |

Evidence:

- `[BIRD24-AUC]` combines representation, predictor, pseudo-label teacher, inference aggregator, and runtime acceleration.
- `[BENGALI-WER]` combines ASR predictor, language model, punctuation, chunking, and decoding.
- `[POLYMER-FEATURES]` shows feature-centric regression where descriptors and target-wise tree models can dominate.
- `[IMC-GEOMETRY]` uses retrieval, local features, matching, clustering, and geometry rather than a single classifier.
- `[MAP-K]` uses LLM/classifier/ranker behavior under runtime constraints.
- `[SANTA]` and `[CODE-GOLF]` show solver/search roles can replace predictive modeling.

Transfer rule:

When copying a solution, copy the role if the role exists in the target competition. Do not copy the architecture if the target lacks the same data unit, metric pressure, or constraint layer.

## Principle 6: Pseudo-labeling is a data intervention, not a trick

Pseudo-labeling appears in many strong systems, but it is conditional.

Pseudo-label contract:

```text
teacher_source
target_unlabeled_pool
confidence_rule
coverage_count
class_or_target_balance
leakage_check
iteration_id
student_training_weight
validation_delta
failure_reason
```

Reusable pattern:

1. Train a trustworthy teacher or ensemble.
2. Generate candidate labels on a target-like pool.
3. Filter by confidence, consistency, or domain rule.
4. Train a student with explicit pseudo-label weighting.
5. Validate against OOF or a held-out split.
6. Stop if the iteration improves only public LB or only one fold.

Evidence:

- `[BIRD25-PL]` shows progressive pseudo-label and self-distillation behavior.
- `[BIRD24-AUC]` shows pseudo-labeling integrated with audio inference and ensemble systems.
- `[DEEP-PAST]` shows silver/synthetic/back-translation style data expansion.
- `[CSIRO]` shows online pseudo-labeling and scientific-regression data intervention.
- `[JIGSAW]` shows online distillation/train-on-test style evidence in text classification.
- `[MABE]` provides negative evidence that pseudo/semi-supervised approaches can fail.

Transfer rule:

Pseudo-labeling should not be used before there is a validation simulator, teacher quality estimate, and leakage policy. Without those, it is uncontrolled data contamination.

## Principle 7: Inference is part of the solution

Many competitions are won or lost after training.

Inference contract:

```text
input_unit
window_or_chunk_policy
checkpoint_set
fold_set
TTA_policy
runtime_backend
batching_policy
aggregation_policy
postprocess_policy
timeout_budget
submission_file_check
```

Common inference decisions:

- chunk long audio or text while preserving context;
- shift windows to reduce boundary artifacts;
- use checkpoint medians or fold averages;
- choose ONNX/OpenVINO/vLLM/quantization only after measuring speed and metric impact;
- reduce output at the same unit the metric evaluates;
- run final submission validation before upload.

Evidence:

- `[BENGALI-WER]` uses chunking, language model decoding, punctuation, and beam-search decisions.
- `[BIRD24-AUC]` uses window aggregation, shifted TTA, and acceleration choices.
- `[BIRD23-CMAP]` shows inference limit and backend tradeoffs.
- `[MAP-K]` shows runtime-aware LLM inference and model-size tradeoffs.
- `[WAVEFORM-TTA]` shows checkpoint prediction medians and inference-time aggregation.
- `[IMC-GEOMETRY]` shows retrieval/shortlist and geometry pipeline choices at inference.

Failure rule:

Do not approve a model plan without a submission-time inference plan. Training success without inference feasibility is not a complete solution.

## Principle 8: Ensembling is risk management

Ensembling is not "average everything." It is controlled risk reduction.

Ensemble contract:

```text
blend_space
member_role
member_validation
member_correlation
weighting_rule
fold_or_seed_diversity
runtime_cost
private_lb_risk
fallback_submission
```

Blend spaces differ:

- probability blend for classification;
- logit blend when calibration differs;
- rank blend for MAP-like metrics;
- target-wise blend for multi-target regression;
- coordinate or geometry merge for structure/matching;
- file-level or segment-level blend for audio/signal competitions.

Evidence:

- `[BIRD24-AUC]` and `[BIRD23-CMAP]` show audio ensemble and inference aggregation.
- `[ASL-KP]` shows seed, layer, dimension, and max-length diversity.
- `[POLYMER-FEATURES]` shows target-wise tree-model blending.
- `[MAP-K]` shows rank/output-space concerns.
- `[WAVEFORM-TTA]` shows checkpoint median aggregation.

Failure rule:

Do not ensemble models that fail the same validation unit for the same reason. Diversity must be measured in errors, folds, outputs, or target behavior.

## Principle 9: Postprocess is a constraint layer

Postprocess should encode the metric, submission format, or domain constraint. It should not be an unexplained score tweak.

Postprocess contract:

```text
rule_name
target_unit
constraint_source
metric_effect
validation_delta
public_lb_delta
private_risk
ablation_status
fallback_policy
```

Reusable postprocess categories:

- text normalization and punctuation restoration;
- threshold and cutoff selection;
- window or segment smoothing;
- assignment or uniqueness constraints;
- geometry and outlier rejection;
- metadata-based correction;
- rank-list completion;
- legal submission repair.

Evidence:

- `[BENGALI-WER]` uses punctuation, language model, and text-format decisions.
- `[BIRD24-AUC]` uses soundscape aggregation, moving average, cutoff, and neighboring-window logic.
- `[ASL-FS]` uses confidence and length-based replacement.
- `[CMI-BEHAVIOR]` uses subject/orientation/Hungarian/history constraints.
- `[MDC-EXTRACT]` uses extraction and metadata heuristics.
- `[IMC-GEOMETRY]` uses orientation, clustering, thresholding, and outlier handling.

Failure rule:

Postprocess without ablation is not evidence. It is a hidden model component and must be tracked like one.

## Principle 10: Preserve negative evidence

Negative evidence is often more transferable than the exact winning trick.

Record failures in this form:

```text
attempt
expected_gain
actual_result
validation_effect
public_lb_effect
likely_failure_mode
keep_drop_decision
transfer_warning
```

Examples:

- `[ASL-KP]` includes variants that did not help, including more landmarks, Conv1D, KD, model soup, larger models, and pretraining in one local setting.
- `[MAP-K]` includes failures from reranker, full finetuning, larger model, distillation, rank loss, and synthetic data.
- `[MABE]` includes negative pseudo/semi-supervised evidence.
- `[BIRD24-AUC]` includes public-LB and additional-data risk patterns.
- `[BIRD23-CMAP]` includes backend and inference-limit tradeoffs.

Transfer rule:

When a tactic fails in a source competition for a structural reason, it should be treated as a warning in target competitions with the same structure.

## Cross-competition checklist

Before committing to a solution direction, answer:

1. What is the competition contract?
2. What unit is split for validation?
3. What unit is scored by the metric?
4. What proxy loss is used, and what metric gap remains?
5. What data quality intervention is planned?
6. What evidence supports external or pseudo-labeled data?
7. What role does each model component play?
8. What inference unit and runtime budget will be used?
9. What postprocess rules are legal and ablated?
10. What negative evidence would make this plan unsafe?
11. Which close evidence IDs support the plan?
12. Which contrast evidence IDs warn against over-transfer?

## Minimum standard for a reusable knowledge item

A reusable Kaggle knowledge item should include:

- at least three positive evidence IDs;
- at least one negative evidence ID;
- at least one contrast-domain evidence ID;
- a transfer boundary;
- a failure diagnosis;
- a checklist or ledger schema.

If a note lacks these elements, it is probably a case note, not official reusable knowledge.
