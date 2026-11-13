# Training Diagnosis and Controls

These sections are shared training-diagnosis and control guidance extracted from the existing [training methods](../supervised-learning/training-methods.md). Their benchmark examples retain their original assumptions; references to labels, public/private scores, and submission budgets apply when those concepts exist in the task.

## Training failure diagnosis

When training fails, classify the failure:

- data failure: noisy labels, wrong grouping, distribution mismatch;
- objective failure: loss does not match metric;
- capacity failure: model too small or wrong representation;
- overfit failure: local improves but public/private worsens;
- runtime failure: model cannot run in submission budget;
- stage failure: later stage erases earlier gains.

## Operational contract

A training method is complete only when it specifies:

- data version;
- split version;
- objective and metric implementation;
- augmentation policy;
- optimization schedule;
- checkpoint selection rule;
- inference compatibility;
- rollback condition.

Training is not separate from inference. If the method requires a postprocess, TTA, calibration, or ensemble to score well, validation must include that dependency.

## Failure rule

Reject a training method when the gain cannot be attributed to one changed layer, when it depends on a validation split that no longer matches the current data contract, or when it cannot be reproduced in the final inference pipeline.

## Source evidence

The original positive, negative, and contrast evidence remains in [training methods](../supervised-learning/training-methods.md#source-evidence). This extraction does not add new evidence or change its transfer strength.
