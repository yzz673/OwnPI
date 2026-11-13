# Top-Tier Scientific Journal Paper Writing

## Overview

A top-tier scientific journal paper usually builds a complete body of evidence around an important or novel scientific action. It asks not only whether a method outperforms a baseline, but what the research discovers, establishes, explains, or advances, and whether those conclusions withstand sufficient validation. Many result-centered original research articles use the following section order:

```text
Abstract -> Introduction -> Results -> Discussion -> Methods
```

These papers often follow the arc `scientific question -> findings/evidence -> interpretation`: first establish the question and scientific action, then build the conclusions through the Results, and finally explain their meaning in the Discussion. This is not a fixed rule. Different journal families, disciplines, and article types may require different structures. Top-tier journals may also impose strict word, figure, or article-type constraints; always follow the current official instructions.

## Abstract

- Follow the Universal Writing Principles for Abstract focus and paragraph structure.
- Write for a broad but informed readership. Use only the background needed to establish the scientific question, then state the scientific action, the most important evidence, and the broader meaning supported by that evidence.
- Do not turn the Abstract into a dataset-by-dataset or experiment-by-experiment report. Unless a dataset or cohort is itself a contribution, summarize the breadth of the evidence instead of listing names.
- Keep every claim consistent with the Results and study design. The Abstract should communicate the importance of the work without using promotional language to inflate novelty, generality, or translational readiness.

## Introduction

- Write the Introduction as several consecutive natural paragraphs without subheadings.
- Establish the scientific question, why it matters, and the knowledge or practical gap for informed readers outside the immediate subfield. Synthesize what prior work has established to define the precise gap; do not give a broad literature inventory or jump from thin background directly to `Here, we...`.
- State precisely what the paper discovers, reveals, establishes, validates, or enables. Build a coherent story from the research and evidence; rhetoric, promotional wording, complex architectures, new terminology, and branded naming cannot substitute for scientific importance or sufficient evidence.
- Identify the actual contribution type: a finding, mechanism, resource, relationship, validation, or method. Do not force every contribution into a method-paper narrative. A standard or simple method is not a weakness when it is appropriate, rigorous, and supports sufficient evidence.
- When the method is the core contribution, explain both its technical novelty and scientific value. Method-centered journals such as Nature Machine Intelligence may require substantial methodological innovation together with sufficient evidence; do not assume that scientific journals generally place little value on methods.
- Prepare the reader for the result-centered evidence that follows without placing every experimental result or technical detail in the Introduction.

## Results

### Organization

- Results should be sufficiently comprehensive, detailed, concrete, and logical. Their subsections should follow a coherent progression rather than being stacked without a clear relationship.
- Results must primarily present quantitative empirical findings. Organize each subsection around one concrete experimental question, comparison, or observed phenomenon, and report the analyzed data scope, evaluation unit, primary metric, baseline or comparator, effect estimate, uncertainty, and applicable statistical test before interpreting the result.
- A Results subsection is not a place to replace findings with a conceptual framework, resource inventory, eligibility label, claim taxonomy, audit narrative, or methodological rationale. Those materials may define the conditions of an experiment, but they must not substitute for the experiment's measured or computed results; place broader framing, detailed methods, and claim-boundary interpretation in the appropriate Introduction, Methods, Supplementary, or Discussion location.
- Main-text Results figures should primarily display observed or computed quantitative evidence, such as distributions, effect estimates, group or condition comparisons, uncertainty, calibration, robustness, or data-derived relationships. Schematic, workflow, resource-map, and claim-boundary diagrams may support a result but must not constitute the majority of the Results figures or replace the primary quantitative evidence.
- Distinguish direct observation, interpretation, mechanistic explanation, and broader implication. First state what the evidence directly shows, then explain what conclusion it supports.
- Do not add repetitive, purposeless analyses or analyses unrelated to the central argument merely to create the appearance of greater workload. The depth of the evidence package should follow the scientific question and claims, not the number of results.

### Statistical Evidence

- Quantitative results must, at minimum, include significance testing.
- Follow the Universal Writing Principles for numerical precision and notation.
- Do not report only qualitative status labels such as `supported`, `bounded`, `ineligible`, or `not established` when underlying quantitative evidence is available. Report the underlying measurements and uncertainty first, then state the evidential status and its scope.
- For quantitative claims, report effect sizes, confidence intervals, design-appropriate hypothesis tests, multiple-comparison handling, sample-size or power considerations, and necessary model assumptions as appropriate.
- Statistical significance alone does not establish importance, causality, generality, or deployment readiness. Interpret results in light of effect magnitude, uncertainty, design validity, and practical or scientific meaning.

## Discussion

- Write the Discussion as several consecutive natural paragraphs without subheadings.
- The Discussion should be sufficiently detailed and intellectually substantive. Explain what the complete body of evidence establishes about the original scientific question rather than repeating the Results figure by figure. Briefly recall earlier contributions, keeping them a small portion of the Discussion; let the question and evidence determine its length.
- Relate findings to prior work and explain which understandings they support, revise, or extend. Distinguish literature-grounded interpretation from new speculation.
- Explain the broader meaning, scope, and implications supported by the evidence. Address important alternative explanations, limitations, and unresolved questions, including how sampling, population, setting, measurement, and study design affect generalization.
- Connect every consequential claim to explicit evidence, an inference scope, and a source artifact. Distinguish direct demonstration, supported interpretation, consistency with an explanation, and possibilities requiring further validation. Choose terms such as `demonstrates`, `supports`, `is consistent with`, `may enable`, and `can be deployed in` accordingly. If evidence is insufficient, narrow the claim, add validation, or identify what remains unresolved rather than merely changing wording.
- Clinical or translational claims carry a particularly high evidential burden. Real-deployment claims usually require a clinical population or setting, a clinically relevant endpoint, an appropriate comparator and validation, and external or prospective evidence matched to the claim.
- Positive conclusions should correspond to the evidence in a reliable and auditable way. This does not require writing every result conservatively; do not make limitations and uncertainty the default center of the paper.
- Derive future directions from the actual boundaries of the work or the new questions it raises, not generic aspirations.

## Methods

- Make the study design, data, preprocessing, analysis, statistics, software, and critical operations auditable and reproducible. The later placement of the Methods section does not make it less important.
- Distinguish prespecified choices from exploratory decisions, and report exclusions, deviations, and failed procedures that affect interpretation.
- Provide enough detail to understand how the evidence was produced without allowing lengthy operational records to obscure the key design decisions. More detailed protocols, configurations, and reproducibility materials can be placed in the Supplementary materials.
- In a method-centered paper, clearly connect the design principle, implementation, and validation. In a discovery, resource, observational, or clinical paper, make the Methods support the validity of the corresponding evidence.

## Supplementary Information, Extended Data, and Source Material

- Use Supplementary Information or Extended Data for controls, robustness checks, secondary analyses, detailed Methods, and reproducibility materials that remain scientifically valuable but would interrupt the main narrative.
- Keep the evidence required for the central inference in the main text. Do not hide an essential result in the Supplement or remove valuable supporting evidence simply because it does not fit a main figure.
- Give every supplementary section, figure, and table a clear manuscript role, source lineage, and relationship to the main text. The Supplement should be an evidence-rich extension of the paper, not a repository for unorganized results.
- Follow the current official requirements of the journal and article type when deciding whether to use Supplementary Information, Extended Data, an appendix, source data, or a reporting summary.
