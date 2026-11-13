# SSL Route Details

Use the relevant section after selecting a route in the [design guide](design-guide.md). These are component-level checks, not mandatory full recipes or claims of universal superiority. [Sources](sources.md) identify the original settings. Preserve a verified implementation as the control when changing a coupled mechanism.

## Contrastive

Define positives by the relationship the representation should retain, not merely by file identity. Image crops can emphasize different objects; nearby audio segments can share noise without sharing content. CPC predicts future latent observations from context, so its positive relationship is temporal prediction rather than necessarily two augmentations of one image. Choose a horizon and sampling scheme that do not make persistent recording artifacts the easiest answer.

Specify the negative set: the current batch, a queue, or another explicit sampling mechanism. A queue increases the available examples but introduces older features; MoCo couples it to a slowly updated key encoder. A larger set can improve discrimination and also increase false negatives or cost. Report the actual simultaneous set, not just the optimizer's accumulated batch size.

Check similarity normalization, temperature, self-pair exclusion, positive inclusion in the denominator, and loss reduction against the chosen objective. These are not interchangeable implementation details. Changing temperature alters the emphasis on difficult comparisons; hard-negative mining should not be added without examining whether those negatives really should be separated.

Treat a small-batch recipe as an optimization and sampling design question, not an automatic failure. Retune compatible learning-rate and normalization settings before attributing a gap solely to batch size. Likewise, compare projector architecture and tuning budgets before attributing a gain to a new contrastive loss.

For an initial implementation check, inspect the pair indices and logits on a tiny batch, verify that no sample contrasts against itself unintentionally, and confirm gradients through the intended branches. Then evaluate transfer under at least one meaningful view variation. Good contrastive separation of training records is insufficient if channel, background, or document formatting explains that separation.

## Self-distillation

Write down the target computation and parameter updates separately. Stop-gradient prevents a particular loss from updating its target path; EMA updates target weights using an online network. Neither operation specifies the other. Inspect shared parameters and normalization buffers in the actual implementation rather than assuming that every target parameter has the same update rule.

In BYOL, the online encoder/projector and predictor match a normalized target projection from another view; the target encoder/projector use EMA, while the predictor is on the online side. In SimSiam, the encoder/projector are shared and there is no EMA teacher: a target projection is detached within each loss term, with the symmetric term still training the shared network. This is not a permanently frozen second encoder. Removing a predictor or gradient asymmetry is an ablation, not an innocuous simplification.

Original DINO matches teacher and student probability distributions across views. Center the teacher logits by subtracting their running mean before teacher-temperature softmax; student logits use their own temperature without this teacher centering. The center update and the EMA of teacher weights are separate operations. Centering and sharpening balance different degeneracies within this recipe, not a universal guarantee for arbitrary teachers. Original DINO's teacher sees global crops and its student also sees local crops; that is distinct from the masked-patch objectives in later methods.

For a contextual latent target, specify which layers and context contribute. data2vec predicts normalized, aggregated teacher representations from a masked student view. Its teacher's unmasked input can still be augmented. The framework spans modalities but does not mean a single jointly trained multimodal model; normalization and parameter sharing must follow the selected modality's implementation.

Check target diversity, student variance, and update behavior during a short run. If targets become constant or excessively concentrated, inspect preprocessing, temperature/normalization, and update dynamics before increasing model size. When testing a different teacher update, keep the rest of a verified recipe fixed initially and report whether the result is stability, better representation quality, or both.

## Redundancy Reduction

Distinguish matching views from keeping features informative. VICReg combines invariance with a per-dimension variance constraint and within-view covariance penalties. Barlow Twins instead regularizes the cross-correlation matrix between normalized views toward a diagonal structure. They are related designs, not permission to exchange their terms without changing statistics and scaling.

Record which axis is normalized, which samples estimate the statistics, and how diagonal and off-diagonal terms are reduced. Feature dimension, projector width, batch size, and loss weights can interact. A reduction changing from a mean to a sum can change the effective regularization when dimensions change, even if the named hyperparameters stay constant.

A stable covariance estimate needs enough varied observations for the chosen representation. Correlated windows or duplicated crops do not contribute the same information as independent examples. In distributed execution, define whether the intended statistic is local or global and verify aggregation and gradients. Accumulating optimizer gradients alone does not reproduce covariance over all accumulated examples.

Monitor both the retained encoder representation and the loss-space representation. Compare variance and spectra using consistent sample counts and normalization; finite samples limit observable rank. Do not maximize rank as a task-independent objective or erase covariance simply because it exists. Meaningful correlated factors can be useful downstream. Validate the regularizer and view assumptions on the intended task, particularly when data are imbalanced or fine-grained distinctions matter.

## Masked and Predictive

Specify three distinct choices: the corruption presented to the student, the target to predict, and the positions contributing to the loss. Predicting every position, only masked positions, or a future continuation creates different learning pressure. Keep positional alignment explicit when preprocessing, subsampling, or tokenizing sequences.

For image reconstruction, MAE's visible-patch encoder and lightweight decoder separate representation computation from pixel prediction. That design does not imply that every encoder should drop masked tokens or that the same masking rate fits every image task. Account for patch granularity and available local context, especially with hierarchical architectures. If pixel reconstruction emphasizes irrelevant detail, test a discrete or latent target rather than assuming a more powerful decoder will solve the representation problem.

Speech provides useful target contrasts:

- wav2vec 2.0 uses learned quantized targets in a contrastive masked prediction task; quantizer diversity and the negative set are part of that design.
- HuBERT supplies discrete targets from offline clustering and predicts at masked positions. Cluster consistency and granularity are different from correspondence to human phoneme labels.
- BEST-RQ generates target indices from a fixed random projection and codebook, not a learned or EMA tokenizer. Input standardization and normalization used for nearest-code assignment are distinct operations. Its streaming and non-streaming variants do not require identical context.
- data2vec uses continuous contextual teacher targets rather than code identities. Select the target layers, normalization, update scheme, and regression loss from a coherent modality-specific configuration.

These alternatives motivate target ablations; they do not establish that random targets are universally sufficient or that contextual targets always win. Time/frequency masking and noise interventions should preserve the information needed for the chosen speech task, which may differ from ASR.

For text, distinguish causal next-token prediction from bidirectional masked prediction and encoder-decoder span denoising. T5 replaces missing spans with distinct sentinels and generates the missing spans with their delimiters, rather than reconstructing the whole original sequence. Span length and corruption rate jointly affect context, difficulty, and decoder work. Teacher forcing is not an EMA teacher network.

ELECTRA provides a different corruption/target pairing: a generator supplies sampled token replacements, and a discriminator predicts whether each token matches the original at all positions. A sampled token equal to the original remains real. This is not token reconstruction, not restricted to corrupted positions, and not ordinary GAN training. Check discrete sampling and any shared parameters before reasoning about gradient paths.

When combining prediction with another SSL loss, state what missing information the second objective is intended to recover. Compare against the individual objectives with compatible readouts and budgets; otherwise report the result as a combined recipe rather than an isolated loss improvement.
