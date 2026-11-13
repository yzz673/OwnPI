# Conference Paper Writing

## Overview

A conference paper usually operates under a strict page limit and must establish a complete, coherent academic argument within limited space. For a typical ML/AI method paper, the central arc is often `problem -> proposed method -> evaluation`, with a common section order of:

```text
Abstract -> Introduction -> Related Work -> Method -> Experiments -> Discussion/Conclusion
```

This structure reflects a common method-centered research narrative, not a mandatory template for every conference paper. Empirical finding, benchmark or resource, analysis, theory, and interdisciplinary papers may require different argument structures and section arrangements. Whatever structure is used, preserve the information needed to understand the central contribution, claims, and supporting evidence. A page limit changes how material is allocated; it does not lower the standards for correctness, evidential completeness, or claim-evidence alignment.

## Abstract

- Follow the Universal Writing Principles for Abstract focus and paragraph structure.
- State the problem, core idea, most important result, and evidence-supported conclusion within the available space. The reader should be able to identify what problem the paper addresses and what it contributes.
- Do not turn the Abstract into a contribution list, dataset inventory, or experiment-by-experiment summary. Retain only the information needed to establish the central argument.
- Keep every conclusion within the evidence presented in the paper. Do not introduce unsupported claims of general capability, state-of-the-art performance, or deployment readiness.

## Introduction

- Write the Introduction as several consecutive natural paragraphs without subheadings.
- Connect the problem, its importance, the limitation of prior work, the paper's core idea, its main contributions, and its key evidence through one clear narrative. By the end of the Introduction, the reader should understand why the work was needed, what it did, and how the rest of the paper will support it.
- Maintain high information density without compressing the Introduction into a mixture of background notes, Related Work, and contribution bullets. Every part should serve the same research gap and central argument.
- Build a strong end-to-end story, but do not use narrative coherence as a substitute for scientific importance, technical novelty, or empirical evidence. Do not exaggerate claims, hide conflicting results, or manufacture a gap to make the story appear stronger.
- Center the Introduction on method novelty only when the method is the actual core contribution. For empirical finding, resource, analysis, or theory papers, organize the Introduction around the corresponding scientific action.
- The Introduction often ends with three or four bullet points summarizing the paper's main contributions. This is a common writing convention, not a required format.

## Related Work

- Use Related Work to establish the paper's position: what prior work has resolved, what limitation remains, and how the current work relates to the most relevant studies. Do not organize it as a chronological or author-by-author inventory.
- Compare prior work along dimensions that genuinely establish the current gap and contribution. Do not select only favorable comparison criteria or weaken related work to manufacture novelty.
- Compress background coverage when required by the page limit, but retain work that materially affects novelty assessment, baseline selection, or claim boundaries.

## Method

- Explain the method's design principle and the specific limitation it addresses before presenting its architecture, algorithm, objective, or training procedure. Make the purpose of each major component clear instead of presenting only a component list.
- Identify where the technical novelty actually lies, such as the problem formulation, representation, architecture, objective, optimization, or training strategy. Do not manufacture apparent novelty through new names, complex diagrams, or unnecessary component stacking.
- A simple method is not inherently a weak contribution. When clear analysis and controlled evidence establish why a simple design is sufficient, explain that reasoning directly instead of adding decorative complexity.
- Do not force every conference paper into a Method-centered narrative. Novel findings, mechanisms, resources, and important validations should not be reframed as model or framework contributions.

## Experiments and Results

- Organize each experiment around a clear question, such as whether the method works, whether a design choice matters, whether the conclusion is robust, where the approach fails, or whether the result transfers to another setting. Do not present the section as a collection of unrelated benchmarks.
- The Experiments and Results section must primarily present measured or computed empirical findings. For each experiment, state the data and evaluation scope, comparison or baseline, primary metric, effect or performance estimate, uncertainty or variability, and applicable statistical test before explaining what the result means.
- Do not replace empirical results with a conceptual framework, resource inventory, eligibility label, claim taxonomy, audit narrative, or methodological rationale. These may establish the conditions or limitations of an experiment, but the section must still show the underlying quantitative evidence whenever it exists.
- Include the main baselines, controls, ablations, robustness checks, efficiency results, failure analyses, or boundary analyses needed to support the central claims. Select the appropriate combination for the actual contribution and claims rather than following a fixed experiment checklist.
- Means and error bars, such as standard deviation or SEM, are common forms of result reporting. When an analysis requires a significance test, apply a necessary test that is appropriate for the study design. Follow the Universal Writing Principles for numerical precision and notation.
- Do not report only qualitative labels such as `supported`, `bounded`, `ineligible`, or `not established` when underlying measurements are available. Report the measurements, uncertainty, and comparison first, then calibrate the claim to the evidence.
- Match claim strength to the actual results. Do not turn a single benchmark improvement, preliminary result, or local demonstration into a claim of general capability.
- Distinguish among terms such as `demonstrates`, `supports`, `is consistent with`, `may enable`, and `can be deployed in`. Wording must match the evidence; weaker language does not replace missing validation.

## Discussion and Conclusion

- Write the Discussion and Conclusion as several consecutive natural paragraphs without subheadings.
- Use a few sentences to summarize the main contributions and most important results. Do not reconstruct the full argument or repeat the Experiments item by item.
- Use a short paragraph to state the most important limitations, prioritizing those that affect interpretation, scope, or generalization.
- Use a few sentences to identify direct and concrete future work. Do not introduce claims unsupported by the paper or end with generic aspirations.

## Supplementary Material

- Place detailed methods, additional controls, robustness checks, secondary analyses, and reproducibility material in the supplement when they are scientifically useful but cannot fit in the main paper.
- Keep the evidence required for the central inference in the main paper. Do not reduce the main text to a summary that can be understood or verified only by reading the supplement.
- Use the supplement to extend the paper, not as an unorganized repository for leftover results. Preserve the relationship of each item to a main claim, method decision, limitation, or reproducibility obligation.
