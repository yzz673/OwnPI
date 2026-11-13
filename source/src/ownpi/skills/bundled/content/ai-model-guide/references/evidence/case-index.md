# Source-Backed Benchmark Case Index

This file indexes every competition currently represented in the curated Kaggle evidence set. Use it to broaden evidence coverage before applying a topic file to a new domain.

## Coverage rule

The topic files extract reusable principles. This case index preserves the source competition coverage so OwnPI does not overfit advice to only BirdCLEF, Bengali.AI, MAP, CMI, Open Polymer, and Image Matching.

## Indexed competitions

| Evidence ID | Competition | Primary knowledge use | Evidence type |
|---|---|---|---|
| `[BENGALI-WER]` | Bengali.AI Speech Recognition | WER, ASR/CTC, language model, punctuation, chunking, noisy-label filtering | curated top-solution evidence |
| `[BIRD24-AUC]` | BirdCLEF 2024 | AUC, mel/SED/CNN, pseudo-labels, ensembling, TTA, OpenVINO/ONNX, soundscape postprocess | curated top-solution evidence |
| `[BIRD25-PL]` | BirdCLEF+ 2025 | pseudo-labeling, self-distillation, multi-iteration noisy student, progressive pseudo-label training | curated top-solution evidence |
| `[ASL-FS]` | Google ASL Fingerspelling Recognition | sequence/keypoint recognition, CTC/Transformer, augmentation, confidence/length postprocess | curated top-solution evidence |
| `[ASL-KP]` | Google Isolated Sign Language Recognition | landmarks, keypoint normalization, CNN/MHSA/Transformer, ensemble diversity | curated top-solution evidence |
| `[BIRD23-CMAP]` | BirdCLEF 2023 | buffered_cmAP, threshold pressure, inference limit, distillation, ONNX/OpenVINO comparison | curated top-solution evidence |
| `[BIRD21-FBETA]` | BirdCLEF 2021 | F-score beta micro, birdcall classification, older BirdCLEF pipeline evidence | curated top-solution evidence |
| `[BIRD22-WCA]` | BirdCLEF 2022 | weighted categorization accuracy, bird audio classification and host/public-private behavior | curated top-solution evidence |
| `[DEEP-PAST]` | Deep Past Akkadian-to-English Translation | data quality, synthetic/silver data, back-translation, OCR/text pairs, post-training | curated top-solution evidence |
| `[MMANIA]` | March Machine Learning Mania 2026 | sports/tabular prediction, bracket/competition prediction evidence | curated top-solution evidence |
| `[RNA3D]` | Stanford RNA 3D Folding Part 2 | RNA structure prediction, template/geometric reasoning, coordinate transfer | curated top-solution evidence |
| `[AIMO]` | AI Mathematical Olympiad Progress Prize 3 | LLM/reasoning competition evidence | curated top-solution evidence |
| `[SANTA]` | Santa 2025 Christmas Tree Packing | optimization and packing-search evidence | curated top-solution evidence |
| `[VESUVIUS]` | Vesuvius Challenge Surface Detection | scientific/CV surface detection and segmentation evidence | curated top-solution evidence |
| `[CSIRO]` | CSIRO Image2Biomass Prediction | scientific regression, DINOv3 regression, auxiliary tasks, online pseudo-labeling, mislabeled-sample removal | curated top-solution evidence |
| `[PHYSIONET-ECG]` | PhysioNet ECG Image Digitization | ECG image digitization and signal extraction evidence | curated top-solution evidence |
| `[RECODAI]` | Recod.ai/LUC Scientific Image Forgery Detection | scientific image forensics evidence | curated top-solution evidence |
| `[MABE]` | MABe Mouse Behavior Detection | behavior detection, time-series/social action recognition, negative pseudo-label evidence | curated top-solution evidence |
| `[NFL-BDB]` | NFL Big Data Bowl 2026 Prediction | sports tracking and prediction evidence | curated top-solution evidence |
| `[CODE-GOLF]` | Google Code Golf Championship | optimization/code-generation competition evidence | curated top-solution evidence |
| `[MITSUI]` | MITSUI Commodity Prediction Challenge | commodity/time-series/tabular prediction evidence | curated top-solution evidence |
| `[RSNA-ANEURYSM]` | RSNA Intracranial Aneurysm Detection | medical imaging and clinical CV evidence | curated top-solution evidence |
| `[ARIEL]` | Ariel Data Challenge 2025 | scientific regression, uncertainty/log-likelihood, signal extraction evidence | curated top-solution evidence |
| `[JIGSAW]` | Jigsaw Agile Community Rules Classification | text classification, online distillation, train-on-test/prompt diversity evidence | curated top-solution evidence |
| `[MAP-K]` | MAP Charting Student Math Misunderstandings | MAP@K, label ambiguity, OOF soft labels, special-token logits, runtime-aware LLM inference | curated top-solution evidence |
| `[CMI-BEHAVIOR]` | CMI Detect Behavior with Sensor Data | behavior/time-series constraints, subject/orientation rules, Hungarian assignment, prediction history | curated top-solution evidence |
| `[MDC-EXTRACT]` | Make Data Count Finding Data References | DOI/Accession extraction, retrieval plus classification, metadata and heuristic postprocess | curated top-solution evidence |
| `[POLYMER-FEATURES]` | NeurIPS Open Polymer Prediction 2025 | feature-centric regression, external-data harmonization, RDKit/Mordred/SKFP, tree ensembles | curated top-solution evidence |
| `[IMC-GEOMETRY]` | Image Matching Challenge 2025 | retrieval, orientation correction, local feature matching, clustering, outlier handling, geometry | curated top-solution evidence |
| `[WAVEFORM-TTA]` | Yale/UNC-CH Geophysical Waveform Inversion | synthetic data, staged training, checkpoint medians, simulator/backprop-style postprocess | curated top-solution evidence |

## Domain coverage summary

- Audio/bioacoustics/speech: `[BENGALI-WER]`, `[BIRD21-FBETA]`, `[BIRD22-WCA]`, `[BIRD23-CMAP]`, `[BIRD24-AUC]`, `[BIRD25-PL]`.
- Keypoint/sign-language: `[ASL-FS]`, `[ASL-KP]`.
- NLP/text/LLM/reasoning: `[DEEP-PAST]`, `[AIMO]`, `[JIGSAW]`, `[MAP-K]`.
- Scientific/CV/medical imaging: `[VESUVIUS]`, `[PHYSIONET-ECG]`, `[RECODAI]`, `[RSNA-ANEURYSM]`, `[IMC-GEOMETRY]`.
- Scientific regression and signals: `[CSIRO]`, `[ARIEL]`, `[POLYMER-FEATURES]`, `[WAVEFORM-TTA]`.
- Tabular/time-series/sports/market: `[MMANIA]`, `[NFL-BDB]`, `[MITSUI]`.
- Optimization/search: `[SANTA]`, `[CODE-GOLF]`.
- Behavior/time-series: `[MABE]`, `[CMI-BEHAVIOR]`.

## Use rule

Before applying a topic file to a new competition, pick at least one close evidence ID from this index and one contrast evidence ID from another domain. This avoids over-transferring a BirdCLEF-style tactic to a non-audio task.

## Evidence use

The evidence IDs are the public-facing reference layer. Topic files should cite evidence IDs, not private file paths or private line references.
