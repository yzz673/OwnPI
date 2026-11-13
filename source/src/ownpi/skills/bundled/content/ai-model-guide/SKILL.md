---
name: ai-model-guide
description: Guidance for any task that uses AI models to solve a problem, including problem formulation, model selection and design, training, evaluation, and diagnosing poor performance. Routes decisions to shared modeling foundations, prediction and postprocessing guidance, and the relevant learning strategy. Current strategies include supervised benchmark training, self-supervised representation learning and pretraining method design, and semi-supervised label expansion.
---

# AI Model Guide

Reference this skill for any task using AI models to solve a problem. Select the available strategy guide that matches the current modeling decision. These are method references, not replacements for the current role, stage instructions, or task requirements.

## Shared Foundations

Select the learning strategy or shared reference needed for the current decision; [the guide introduction](references/README.md) explains the available reading paths. Read [benchmark evidence interpretation](references/evidence/README.md) when applying benchmark-derived recommendations, not as a prerequisite for every strategy. Shared foundations are organized by modeling concern, not restricted to supervised learning.

The current examples largely come from benchmark solutions. Keep their metric, label, validation, and data assumptions: a shared location does not make every tactic universal. Apply label-dependent procedures, OOF recipes, and leaderboard rules only when the task has the corresponding setup.

| Decision | Reference |
| --- | --- |
| Transfer a tactic across tasks | [Cross-competition principles](references/foundations/cross-competition-principles.md) |
| Define the problem and objective | [Problem framing](references/foundations/decoding-problem-framing.md), [metric alignment](references/foundations/metric-objective-alignment.md) |
| Audit data and validation splits | [Signal and label audit](references/foundations/signal-and-label-audit.md), [leakage and split protocol](references/foundations/leakage-and-split-protocol.md) |
| Establish baselines | [Baseline ladder and EDA](references/foundations/baseline-ladder-and-eda.md) |
| Choose model roles | [Model design](references/foundations/model-design.md) |
| Diagnose training and define controls | [Training diagnosis and controls](references/foundations/training-principles.md) |
| Diagnose generalization and validate gains | [Validation robustness](references/foundations/validation-robustness.md) |

## Learning Strategies

| Scenario | Strategy |
| --- | --- |
| Supervised training with labels and benchmark evaluation | [Training methods](references/supervised-learning/training-methods.md) |
| Designing, adapting, or diagnosing self-supervised representation learning or pretraining objectives | [Self-Supervised Learning Design Guide](references/self-supervised-learning/design-guide.md) |
| Optional teacher-generated labels or semi-supervised data expansion | [Semi-supervised label expansion](references/semi-supervised-learning/semi-supervised-label-expansion.md), [audit template](references/semi-supervised-learning/templates/semi_supervised_label_expansion_audit.md) |

Select a strategy only when its assumptions match the task. These branches reference the shared foundations; they do not redefine them. Semi-supervised label expansion is not a self-supervised learning guide: an SSL teacher or clustering target alone does not imply downstream pseudo-label expansion. The SSL branch covers method design, not all pretraining systems or post-training workflows. No dedicated reinforcement-learning guide is currently provided.

## Prediction and Postprocessing

For model use after training, consult [prediction protocol](references/prediction/prediction-and-test-time-protocol.md), [averaging and robustness](references/prediction/model-averaging-and-robustness.md), and [postprocessing and calibration](references/prediction/prediction-postprocessing-and-calibration.md) as relevant to the output and evaluation protocol.

## End-to-End Workflows

For a benchmark task spanning problem framing, baseline construction, experiments, evaluation, and delivery, read [Benchmark Modeling Workflow](references/workflows/benchmark-modeling.md). This cross-topic workflow is not limited to neural decoding or one learning paradigm; apply it only when its benchmark assumptions match the task.

## Evidence and Templates

For benchmark-derived tactics, use [the case index](references/evidence/case-index.md) and [coverage matrix](references/evidence/coverage-matrix.md) for supporting, contrasting, and negative evidence. For SSL recommendations, use [paper sources and their limits](references/self-supervised-learning/sources.md). Benchmark artifacts are the [benchmark brief](templates/decoding_benchmark_brief.md), [validation plan](templates/decoding_validation_plan.md), and [final benchmark package](templates/final_benchmark_package.md).

Read only the resources needed for the current decision, rather than loading every file. For tasks outside the collection's detailed coverage, apply only relevant supported principles; do not force an unrelated strategy onto the task.
