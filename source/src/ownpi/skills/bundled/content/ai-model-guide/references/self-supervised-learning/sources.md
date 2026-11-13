# SSL Sources and Limits

These sources support the [design guide](design-guide.md) and [route details](method-recipes.md). Read a source when checking an exact mechanism, experimental condition, or a proposed change; routine use does not require reading the entire collection. The summaries are decision guidance, not reproduced paper text or claims that every cited work has been exhaustively reviewed.

## Main Framework

[Balestriero et al., A Cookbook of Self-Supervised Learning (2023), v2](https://arxiv.org/abs/2304.12210v2) supplies the structure: method families in Section 2; data and imbalance in Sections 2.7/3.3; views in 3.1; projectors in 3.2; teacher/student design in 3.4; optimization in 3.5; masked modeling in 3.6; evaluation in 3.7; distributed statistics in 3.8; modality and localization differences in Section 4.

Its recipes primarily reflect vision experiments available at publication. Preserve their assumptions rather than treating ImageNet hyperparameters, rankings, architecture choices, or augmentation menus as universal. Where explanatory prose and a method definition are ambiguous, use the original paper and implementation; original DINO, for example, centers teacher logits.

## Decision Evidence

| Topic | Original source and checked scope | Interpretation limit |
| --- | --- | --- |
| Views and information retention | [Tian et al., What Makes for Good Views for Contrastive Learning? (2020), v3](https://arxiv.org/abs/2005.10243v3), InfoMin/view-selection analysis | Reducing shared information is conditioned on retaining task-relevant information, not a recommendation for maximal corruption |
| Loss comparisons and projector confounds | [Garrido et al., On the duality between contrastive and non-contrastive self-supervised learning (2022), v3](https://arxiv.org/abs/2206.02574v3), Sections 3-5 | Relations under specified normalizations do not make arbitrary implementations or training dynamics equivalent |
| Temporal contrastive targets | [van den Oord et al., Representation Learning with Contrastive Predictive Coding (2018), v2](https://arxiv.org/abs/1807.03748v2), Sections 2.1-2.3 | Useful predictive information depends on context, horizon, and negative sampling; temporal adjacency alone need not identify semantic content |
| EMA target and online predictor | [Grill et al., Bootstrap your own latent (2020), v3](https://arxiv.org/abs/2006.07733v3), Section 3.1 | BYOL's normalized target projection and online predictor are part of a coupled recipe |
| Shared encoder with detached target | [Chen and He, Exploring Simple Siamese Representation Learning (2020), v1](https://arxiv.org/abs/2011.10566v1), Section 3 | No EMA does not mean no asymmetry; stop-gradient is not a universal collapse-prevention theorem |
| Distributional self-distillation | [Caron et al., Emerging Properties in Self-Supervised Vision Transformers (2021), v2](https://arxiv.org/abs/2104.14294v2), Section 3.1 and Algorithm 1 | Teacher-logit centering, separate temperatures, global/local crops, and teacher-weight EMA have distinct roles; not a masked-patch recipe |
| Visible-input encoder and reconstruction | [He et al., Masked Autoencoders Are Scalable Vision Learners (2021), v3](https://arxiv.org/abs/2111.06377v3), Section 3 | Pixel targets, masking, and decoder choices are established in a particular vision setup, not cross-modal defaults |
| Learned speech quantization | [Baevski et al., wav2vec 2.0 (2020), v3](https://arxiv.org/abs/2006.11477v3), Sections 2-3 | The contrastive objective and diversity mechanism accompany the quantizer; ASR results do not cover every speech task |
| Offline clustering targets | [Hsu et al., HuBERT (2021), v1](https://arxiv.org/abs/2106.07447v1), Sections II and IV-B | Consistency, granularity, and label accuracy are different notions; avoid general claims that target quality is irrelevant |
| Fixed random targets | [Chiu et al., BEST-RQ (2022), v2](https://arxiv.org/abs/2202.01855v2), Sections 3-3.2 | Frozen random projection/codebook is not a universal replacement for learned targets; normalization and streaming context matter |
| Contextual latent prediction | [Baevski et al., data2vec (2022), v3](https://arxiv.org/abs/2202.03555v3), Sections 3.1-3.4 and 4.1-4.3 | Shared learning principle, not identical preprocessing, parameter updates, regression settings, or a joint multimodal model |
| Text span corruption | [Raffel et al., T5 (2019), v4](https://arxiv.org/abs/1910.10683v4), Sections 3.1.4, 3.3.4, 3.7 | Missing-span targets and sentinels differ from full reconstruction; reported span lengths and rates are experimental settings |
| Replaced-token discrimination | [Clark et al., ELECTRA (2020), v1](https://arxiv.org/abs/2003.10555v1), Section 2 | Generator sampling, dense discrimination, and parameter sharing must be distinguished; not an EMA teacher or GAN recipe |

## Applying the Evidence

Use the paper title and relevant section when explaining a non-obvious recommendation. Distinguish a paper's empirical observation, a theoretical statement with assumptions, an engineering check inferred from the objective, and an untested adaptation to the current task. The benchmark collection's competition IDs and ranking-based evidence grades are not required for this branch.

Diagnostics such as effective rank indicate properties of representations, not guaranteed downstream utility. Claims about individual components require appropriate ablations; if several components or budgets change, limit the claim to the combined system. No single source here establishes that one SSL family, mask ratio, batch size, or teacher mechanism is optimal across modalities.
