# Evidence Base and Interpretation

This file describes the benchmark-derived evidence collection, not all evidence used by AI Model Guide. Its competition IDs and grading apply to recommendations derived from that collection. Self-supervised method guidance uses [paper sources and limits](../self-supervised-learning/sources.md); it does not require selecting a competition case first.

## Evidence base

The current source base contains:

- 177 Kaggle Featured and Research competitions from 2021-01-01 to 2026-04-25.
- 72 audited competitions in completed crawl batches.
- 1216 writeup records.
- 1186 leaderboard-solution records.
- 1220 normalized Kaggle knowledge-card records.
- 434 high-quality solutions across 30 competitions, restricted to usable leaderboard writeup/icon solutions with private/final rank <= 40.

The curated evidence set spans audio/bioacoustics/speech, CV/medical imaging, NLP/text/education, tabular/time-series/signals, bio/health/chemistry, recommender/retrieval/matching, LLM/reasoning/agents, scientific/geospatial/multimodal, and games/RL/optimization/security.

## Evidence strength

Use this order when choosing evidence:

1. `strong`: team-member writeup with rank, metric, and implementation detail.
2. `medium`: normalized local knowledge card derived from a writeup.
3. `weak`: public notebook or discussion clue not tied to final private-LB solution.
4. `hypothesis`: extrapolation from similar curated cases; must be labeled as not yet validated.

Do not promote a weak or hypothesis-level item into official advice unless it is clearly labeled.

## Evidence IDs

Use these IDs in new notes and experiment plans:

- `[BENGALI-WER]`: Bengali.AI speech recognition, WER, ASR/CTC, language model, punctuation, noisy-label filtering.
- `[BIRD21-FBETA]`: BirdCLEF 2021, F-score beta micro, birdcall classification, early BirdCLEF pipeline evidence.
- `[BIRD22-WCA]`: BirdCLEF 2022, weighted categorization accuracy, bird audio classification.
- `[BIRD23-CMAP]`: BirdCLEF 2023, buffered_cmAP, threshold pressure, inference limit, KD/distillation evidence.
- `[BIRD24-AUC]`: BirdCLEF 2024, Birdclef ROC AUC, 5-second windows, mel/SED/CNN, pseudo-labels, ensembles, OpenVINO/ONNX, soundscape postprocess.
- `[BIRD25-PL]`: BirdCLEF+ 2025, pseudo-labeling, self-distillation, progressive pseudo-label fusion.
- `[ASL-FS]`: ASL fingerspelling, sequence/keypoint recognition, CTC/Transformer-style evidence.
- `[ASL-KP]`: ASL keypoint/sign-language tasks, landmarks, frame length, CNN/MHSA/Transformer, augmentation, ensemble diversity.
- `[DEEP-PAST]`: Akkadian-to-English translation, data quality, synthetic/silver data, back-translation, OCR/text-pair evidence.
- `[MMANIA]`: March Machine Learning Mania, sports/tabular prediction evidence.
- `[RNA3D]`: Stanford RNA 3D Folding, structure prediction, template/geometric reasoning evidence.
- `[AIMO]`: AI Mathematical Olympiad progress prize, reasoning/LLM competition evidence.
- `[SANTA]`: Santa 2025 Christmas Tree Packing, optimization/packing evidence.
- `[VESUVIUS]`: Vesuvius surface detection, scientific/CV segmentation and surface reconstruction evidence.
- `[CSIRO]`: CSIRO Image2Biomass, scientific regression, DINOv3, auxiliary tasks, online pseudo-labeling.
- `[PHYSIONET-ECG]`: ECG image digitization, signal extraction from images.
- `[RECODAI]`: scientific image forgery detection, visual forensics evidence.
- `[MABE]`: mouse behavior detection, social action recognition, behavior/time-series constraints, negative pseudo-label evidence.
- `[NFL-BDB]`: NFL Big Data Bowl prediction, sports tracking/prediction evidence.
- `[CODE-GOLF]`: Google Code Golf, optimization/code-generation evidence.
- `[MITSUI]`: commodity prediction, tabular/time-series market prediction evidence.
- `[RSNA-ANEURYSM]`: intracranial aneurysm detection, medical imaging evidence.
- `[ARIEL]`: Ariel Data Challenge, scientific regression and Gaussian log-likelihood evidence.
- `[JIGSAW]`: community rules classification, text classification/distillation evidence.
- `[MAP-K]`: math misconception MAP@K, ranking, label ambiguity, OOF soft labels, special-token logits, runtime-aware LLM inference.
- `[CMI-BEHAVIOR]`: CMI behavior detection, subject/orientation constraints, Hungarian assignment, history-aware postprocess.
- `[MDC-EXTRACT]`: Make Data Count, DOI/Accession extraction, retrieval plus classification, metadata and heuristic postprocess.
- `[POLYMER-FEATURES]`: Open Polymer, feature-centric regression, external-data harmonization, RDKit/Mordred/SKFP, LightGBM/tree ensembles.
- `[IMC-GEOMETRY]`: Image Matching Challenge, retrieval, orientation correction, local feature matching, clustering, outlier handling.
- `[WAVEFORM-TTA]`: waveform inversion, synthetic data, staged training, checkpoint prediction medians, simulator/backprop-style postprocess.

## Evidence discipline

The benchmark topic files have a `Source evidence` section. Within this collection, if a future note cannot cite its evidence, do not present it as confirmed strategy. Put it in an experiment log or label it as a hypothesis. Other branches may use verified research papers rather than competition evidence IDs, while retaining their source conditions and distinguishing extrapolation from observation.

## Source evidence

- `curated extractor evidence` documents extractor fields and solution priority.
- `curated extractor evidence` documents structured output layout and writeup/rank matching.
- `curated competition index` documents curated competition coverage and domain counts.
- `curated writeup quality audit:3-20` documents audited corpus size and quality flags.
- `case-index.md` documents the public-facing evidence ID set.
