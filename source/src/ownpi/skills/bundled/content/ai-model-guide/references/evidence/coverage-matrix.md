# Source-Backed Benchmark Knowledge Coverage Matrix

This matrix shows how the 30 curated high-quality-solution competitions connect to the 13 strategy topics. The Training topic now spans the shared training controls and the supervised training strategy.

Legend:

- `P`: positive evidence, directly supports reusable knowledge.
- `N`: negative evidence, documents a failure, risk, or non-default boundary.
- `C`: contrast evidence, broadens domain coverage and prevents over-transfer.
- `-`: not currently used by that topic.

| Evidence ID | Problem | Metric | Data | Split | Validation | Baseline | Model | Training | Pseudo | Inference | Ensemble | Postprocess | Playbook |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| `[BENGALI-WER]` | P | P | P | P | P | P | P | P/N | P | P | P | P | P |
| `[BIRD21-FBETA]` | C | C | C | C | C | C | C | C | - | C | C | C | C |
| `[BIRD22-WCA]` | C | C | C | C | C | C | C | C | - | C | C | C | C |
| `[BIRD23-CMAP]` | P | P | C | C | C | C | C | C | C | P/N | P | C | C |
| `[BIRD24-AUC]` | P | P | P/N | P/N | P/N | P | P | P | P | P/N | P | P/N | P/N |
| `[BIRD25-PL]` | P | C | P | C | C | C | P | P | P | P | P | P | P |
| `[ASL-FS]` | P | C | C | C | C | C | P/N | P | - | C | P/N | P | C |
| `[ASL-KP]` | P | C | C | C | C | C | P/N | P | - | C | P/N | C | C |
| `[DEEP-PAST]` | P | C | P | C | C | P | C | P | P | C | C | C | C |
| `[MMANIA]` | C | C | C | C | C | C | C | C | - | C | C | C | C |
| `[RNA3D]` | P | C | C | C | C | C | P | C | - | C | C | C | C |
| `[AIMO]` | P | C | - | - | C | C | P | C | - | C | C | - | C |
| `[SANTA]` | P | C | - | - | C | C | P | C | - | C | C | C | C |
| `[VESUVIUS]` | P | C | P | C | C | C | P | C | - | C | C | P | C |
| `[CSIRO]` | P | C | P | C | P | C | P | P | P | C | C | C | C |
| `[PHYSIONET-ECG]` | P | C | P | C | C | C | P | C | - | P | C | P | C |
| `[RECODAI]` | P | C | C | C | C | P | P | C | - | C | C | P | C |
| `[MABE]` | P | C | N | P | N | C | P | N | N | C | C | P | C |
| `[NFL-BDB]` | C | C | C | P | C | C | C | C | - | C | C | C | C |
| `[CODE-GOLF]` | P | C | - | - | C | C | P | C | - | C | C | - | C |
| `[MITSUI]` | P | C | C | P | C | C | C | C | - | C | C | C | C |
| `[RSNA-ANEURYSM]` | P | C | P | C | C | C | P | C | - | P | C | C | C |
| `[ARIEL]` | P | P | P | C | P | C | P | C | - | P | P | C | C |
| `[JIGSAW]` | P | C | P | C | C | C | P | P/N | P | C | C | P | C |
| `[MAP-K]` | P | P/N | P | P | P/N | P/N | P/N | N | P/N | P/N | N | P | P |
| `[CMI-BEHAVIOR]` | P | P | P | P | P | C | P | C | - | C | C | P | P |
| `[MDC-EXTRACT]` | P | C | P | C | C | C | P | C | - | C | C | P | C |
| `[POLYMER-FEATURES]` | P | P | P/N | C | P/N | P | P | C | N | C | P | P/N | P |
| `[IMC-GEOMETRY]` | P | C | C | P | C | P | P | C | - | P | P | P | P |
| `[WAVEFORM-TTA]` | P | C | P | C | P | C | P | P | - | P | P | P | C |

## Use rule

When writing a plan for a new competition:

1. Pick one `P` evidence ID close to the target domain.
2. Pick one `C` evidence ID from a different domain.
3. Check whether any `N` evidence ID warns against the planned tactic.

Do not use a topic file as official guidance if all relevant evidence IDs are only `C` or `-`.
