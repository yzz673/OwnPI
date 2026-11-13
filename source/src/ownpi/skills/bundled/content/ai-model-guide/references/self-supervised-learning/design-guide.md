# Self-Supervised Learning Design Guide

Use this guide to design, adapt, or diagnose self-supervised representation learning and pretraining objectives. Follow the decisions relevant to the current task; an existing model or training recipe can be the starting point. This is method guidance, not a replacement for task requirements, role instructions, or the current research workflow.

The main reference is the SSL Cookbook, supplemented by original vision, speech, and language papers. [Sources and limits](sources.md) identify the evidence behind the recommendations. Routine decisions do not require reading all those papers. Consult an original method when implementing its exact objective or changing a mechanism whose behavior depends on the complete recipe.

## 1. Define the Representation Goal

Describe how the representation will be used: frozen retrieval, a lightweight prediction head, limited-label adaptation, full fine-tuning, or sequence generation. Identify the unit that needs a representation: a whole sample, an image region, a time interval, or an individual token. This determines what information the pretraining task must preserve and how success should be measured.

Distinguish nuisance variation from useful variation. Color may be irrelevant to object identity but essential to a measurement; speaker identity may be unwanted for speech recognition but central to speaker verification. For segmentation or localization, collapsing spatial distinctions into one invariant vector can discard information needed later. When downstream tasks are not yet fixed, state a plausible set of uses and avoid imposing invariances that would rule them out without evidence.

Audit the pretraining corpus against these goals: coverage, duplicate samples, recording or acquisition artifacts, imbalance, and differences from deployment data. Large uncurated data does not automatically improve the desired representation. A loss that favors balanced assignments or dispersed embeddings can behave differently on long-tailed data; inspect what distinctions the model is actually learning instead of assuming a uniform semantic distribution.

Unlabeled does not mean exempt from evaluation boundaries. Define the independent sample or group before splitting correlated windows, documents, or near-duplicate images. Keep evaluation data out of pretraining and model selection unless the task explicitly permits a transductive protocol; disclose such use. Apply the relevant parts of [problem framing](../foundations/decoding-problem-framing.md) and [leakage and split protocol](../foundations/leakage-and-split-protocol.md), without importing label-dependent benchmark procedures into an unlabeled setting.

## 2. Choose an SSL Route

First compare reusing a suitable pretrained encoder, continuing its pretraining, and training from scratch. Choose a simple, supported starting recipe compatible with the data, intended use, and compute budget. New objectives should address an identified limitation, not merely increase the number of loss terms.

| Route | Consider it when | Main design risk | Read when developing this route |
| --- | --- | --- | --- |
| Contrastive learning | Positive relationships are meaningful and discrimination among examples supports the intended representation | False negatives, nuisance-based discrimination, unsuitable views | [Contrastive](method-recipes.md#contrastive) |
| Self-distillation | Different views or masked contexts should predict a stable target without explicit negative pairs | Collapsed or uninformative targets; incompatible branch asymmetry | [Self-distillation](method-recipes.md#self-distillation) |
| Redundancy reduction | Agreement across views can be coupled with explicit control of feature variance or correlation | Unreliable statistics; regularizing away meaningful structure | [Redundancy reduction](method-recipes.md#redundancy-reduction) |
| Masked or predictive modeling | Missing content, future context, or corrupted observations provide a useful prediction task | Trivial interpolation, excessive difficulty, targets dominated by irrelevant detail | [Masked and predictive](method-recipes.md#masked-and-predictive) |

These are design families, not mutually exclusive boxes. A masked predictor can use a self-distillation target; a contrastive method can use a momentum encoder. Explain what each component contributes rather than inferring the loss from the presence of a teacher.

Do not reject contrastive learning solely because the available batch is small, or select a reconstruction method solely because its pretext loss is easy to compute. Compare feasible configurations, including optimization and statistics. For continued text pretraining, consider compatibility with the existing causal or bidirectional architecture before choosing next-token, masked-token, or span-denoising objectives.

## 3. Design Views and Corruption

Specify what the student sees, what is hidden or altered, and what information is shared with its target or positive counterpart. An augmentation is an assumption about information that may be discarded. Test that assumption on actual samples before adding stronger transformations.

For positive pairs, ask whether both views still contain the intended common signal. Two crops from a multi-object image need not represent the same object. Two speech segments may share a recording channel but not linguistic content. Matching paired modalities requires valid alignment and an explicit account of where supervision comes from; paired captions are not equivalent to arbitrary image augmentations.

For negative pairs, identify examples that are distinct records but semantically similar. Increasing negative hardness can amplify false negatives. Avoid relying on easy acquisition artifacts to distinguish recordings or documents. In temporal prediction, check whether future targets can be solved using persistent background cues rather than useful dynamics.

Choose masking units and context together: pixels or patches, time spans or acoustic frames, tokens or text spans. A spectrogram's time and frequency axes are not interchangeable image coordinates, and reversing speech is not a generic semantics-preserving flip. In language, deletion or replacement can change meaning; that may be intentional corruption to reconstruct, not an invariance to enforce.

Tune difficulty around useful information. If a target is recoverable by local copying or interpolation, hide coherent regions or adjust context and target granularity. If little relevant information remains, reduce corruption or select a more predictable target. Do not transfer a fixed mask ratio between modalities or architectures. In hierarchical encoders, check that masking does not leave required local windows without input.

A minimal check compares representative original/corrupted samples and one weaker or structurally different corruption policy. Judge the resulting representation or transfer, not just which policy has lower pretext loss. Multi-crop adds positive relationships and cost; use it when the local/global relationship is meaningful, not as an automatic improvement.

## 4. Specify Targets and Components

Write a compact account of the information flow: input view, encoder output, any projection or prediction head, target construction, loss locations, gradient paths, and target update schedule. State which representation will be retained for downstream use. This often exposes an unspecified or contradictory design before a long experiment.

Choose the prediction target deliberately:

- **Raw observations or tokens:** simple and directly available, but reconstructing every acoustic or visual detail may reward irrelevant variation. Text tokens provide a discrete vocabulary; continuous signals do not automatically have an equivalent vocabulary.
- **Discrete derived targets:** clustering, learned quantization, or fixed random quantization define different partitions of the input. Their consistency, granularity, and usage matter; one method's success does not establish that target quality is irrelevant.
- **Continuous latent targets:** choose whether the teacher sees full context, which layers contribute, and how targets are normalized and updated. An unmasked local target is not necessarily contextualized.

The projector separates the loss space from a potentially more transferable representation. The predictor maps one branch toward its target; the decoder reconstructs or generates outputs. These roles are not interchangeable. Do not attach every head to every method, assume the projector alone prevents collapse, or always discard it without checking the intended readout. A backbone, intermediate layer, pooled representation, or projector output can differ in useful information.

Specify whether targets come from a frozen mapping, periodic offline clustering, or an online teacher. For online teachers, separate stop-gradient from the weight-update rule. An EMA teacher, a predictor, centering, and variance constraints are different mechanisms with recipe-dependent interactions; none is a universal stand-alone guarantee against collapse. Preserve the verified baseline before ablating one of them.

For mixed objectives, identify the complementary signal, shared parameters, loss scaling, and a way to test the contribution. Avoid describing a benefit as caused by the new loss if data, architecture, and target generation also changed. Teacher-generated SSL targets are not automatically downstream pseudo-label expansion; use the separate semi-supervised strategy only when that is actually the intervention.

## 5. Diagnose and Stabilize Training

Start with a short run that checks target alignment, mask indexing, gradient paths, and whether frozen or EMA parameters update as intended. Then distinguish these failure modes:

| Observation | Inspect first | Small discriminating check |
| --- | --- | --- |
| Similar outputs for unrelated inputs | Per-feature variance, pairwise similarity, target diversity | Compare varied inputs in the retained representation and the loss space |
| Many redundant dimensions | Singular-value spectrum or effective rank, centering and sample size | Compare the same diagnostic protocol across checkpoints and layers |
| Low pretext loss but weak transfer | Shortcuts, overly easy targets, destructive invariances, inappropriate readout | Change one view/target choice or probe layer while holding the rest fixed |
| Oscillating loss or sudden probe drops | Gradient scale, learning rate, normalization, teacher dynamics | Return to the verified recipe and isolate the changed component |
| Highly concentrated discrete targets | Code usage or assignment entropy, preprocessing, target updates | Inspect occupancy and stability across representative data groups |

High effective rank alone does not prove useful representations: random features can also have high rank. Concentrated assignments may reflect genuine imbalance rather than a bug. Interpret diagnostics with the data and objective instead of enforcing a universal numerical threshold. A frozen or untrained baseline can reveal whether an apparently healthy statistic is informative.

Batch size affects more than optimizer steps. For batch-dependent losses, identify the examples used for negatives, variance/covariance, and normalization. Gradient accumulation does not by itself enlarge the simultaneous contrastive set or reproduce full-batch feature statistics. When distributing training, verify the intended aggregation and gradient behavior using the current implementation; do not transplant old collective-operation code blindly.

Adjust learning rate, head dimensions, loss reductions, and regularization coherently when changing scale. Track preprocessing, crop count, target-generation cost, and evaluation cost before attributing slowness to the model. Reuse applicable [training controls](../foundations/training-principles.md); retain the distinction between a pretraining proxy and the downstream objective.

## 6. Validate the Design

Choose evaluation to match use. For frozen retrieval, evaluate retrieval with the intended readout and similarity function. A k-NN or linear probe can cheaply inspect discriminative accessibility when suitable labels exist. Full fine-tuning measures adaptability and can rank methods differently from linear probing. Dense prediction requires an evaluation that preserves and uses local information, not only image-level classification.

Keep probe data, splits, feature normalization, readout, and tuning budget comparable. Online probes are monitoring signals, not automatically substitutes for a fixed-checkpoint evaluation; detach their gradients when labels must not train the encoder. If testing label efficiency, keep label subsets and downstream optimization protocols controlled.

Compare against a relevant baseline under an explicit budget: pretrained initialization versus continued pretraining, or an established SSL recipe versus a proposed change. Record differences in pretraining data, model capacity, updates, wall-clock cost, and downstream adaptation. For a coupled redesign, use targeted ablations or a small factorial comparison to support component-level claims; otherwise report only the combined result.

Without downstream labels, continue with available unlabeled diagnostics, corruption checks, and representation consistency tests. State that these do not establish downstream performance. Do not demand labels or user approval merely to continue the analysis, and do not optimize a surrogate as though it guaranteed utility. Apply relevant [validation robustness guidance](../foundations/validation-robustness.md) when assessing transfer claims.

Before scaling, put the decisions and remaining uncertainty in the existing experiment or research record: representation goal, data boundary, route, views, target/loss, stability checks, and evaluation. Expand the experiment when the pilot supports the intended mechanism, not merely because the training loss decreases.
