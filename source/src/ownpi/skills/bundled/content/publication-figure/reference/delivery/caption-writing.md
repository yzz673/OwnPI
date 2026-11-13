# Publication Figure: Caption Writing Tutorial

Write the final figure caption after the figure has passed code check and visual inspection. The caption explains what the reader can see and how to interpret it without relying on the main text. It does not redesign the figure, change panel roles, or replace the Results section.

## Contents

1. Build the caption structure
2. Describe panels precisely
3. Report statistics and data context
4. Make the caption self-contained
5. Control tense and display labels
6. Add a restrained claim-closing sentence
7. Handle special figure types
8. Check length and completeness
9. Persist the caption deliverable

## 1. Build the caption structure

Use this order: Fig. N followed by a short bold noun-phrase title; panel descriptions labeled a, b, c and so on; statistical and data context attached to the relevant panel; and a source-data statement at the end. Suitable title forms include Overview of ..., Comparison of ..., and Performance of .... Keep the title nominal and short. Put numeric results in the panel descriptions or statistics, not in the title.

Keep the figure title and explanatory prose in the separate caption, not on the figure canvas.

## 2. Describe panels precisely

Describe every panel in present tense and in compact scientific prose. State the object shown, the chart or image form, the variables and units, and the visible comparison. Explain colors, shapes, line styles, hatching, fills, and reference lines when their meaning is not obvious. Use one description per panel and preserve the panel labels used in the figure.

A panel description should allow a reader to identify the plotted objects without reading the Results section. Avoid vague phrases such as results are shown. Name the conditions, methods, cohorts, or channels represented by the visual marks.

## 3. Report statistics and data context

Include sample size, replicate unit, summary statistic, error definition, statistical test, multiple-comparison correction, significance threshold, and significance-symbol meaning whenever applicable. State whether values are mean, median, proportion, normalized score, or another summary. Include units and transformations needed to interpret the axis.

Use wording such as: Values are mean ± 95% CI; n = 1373 independent samples. Statistical comparisons used one-way ANOVA with Tukey correction. Do not invent tests or infer replicate units from the appearance of a plot. If a panel has no statistical comparison, do not add statistical language merely to make the caption look complete.

## 4. Make the caption self-contained

A reader must be able to understand the figure away from the manuscript body. Define color and shape mappings, abbreviations, sample information, key numeric anchors, and data-source identifiers in the caption itself. Include relevant units, IDs, or reference values, such as a structure identifier or RMSD, when they are needed to interpret the panel.

End a research figure caption with the applicable source-data statement, for example: Source data are provided as a Source Data file. Use the venue's required wording when it differs. Do not refer to private workspace paths or internal filenames in the submitted caption.

## 5. Control tense and display labels

Use present tense for visual facts: The distributions are shown as violin plots. The gray line denotes the reference condition. Use past tense for methods or provenance: Data were normalized per subject. The panel was adapted from a previous study.

Treat in-figure legend entries as display labels. Start ordinary descriptive labels with an uppercase letter while preserving canonical spelling exactly, including internal capitals, hyphens, periods, and abbreviations such as XGBoost, DeepSeek, GPT-5.2, and RF. Do not apply automatic title casing. Use normal sentence grammar in prose.

## 6. Add a restrained claim-closing sentence

A final sentence may state a bounded observation supported by the displayed evidence. Use it sparingly. Do not introduce evidence absent from the figure, turn association into causation, or repeat the full Results argument. If the panels only describe data, omit the claim-closing sentence.

## 7. Handle special figure types

For review or perspective figures that aggregate previously published systems, give each sub-panel a one-line characterization and include the required attribution and permission statement for every adapted panel.

For tables, use Table N followed by a short noun-phrase title. Describe the table contents and point detailed specifications to Methods when appropriate. Table captions are not substitutes for figure captions.

## 8. Check length and completeness

Follow the current target venue and article type. The Nature Communications-derived conventions in this tutorial do not define a universal word limit. For flagship Nature submissions, verify the current author guidance and keep the complete figure legend below 250 words when that requirement applies.

Before finalizing, verify that the title is short and nominal, every panel is described, all visible encodings are explained, sample and uncertainty information is present, statistical tests are accurate, abbreviations are defined, the source-data statement is included, and no unsupported claim has been added.

Write one paragraph and remove redundancy before delivery. Describe each panel briefly, define shared encodings and statistics once, and omit narration already clear from labels. Move detailed procedures to Methods and extended interpretation to Results or Discussion; retain what readers need to interpret the figure independently.

## 9. Persist the caption deliverable

Save the caption to <workspace>/output_synthesis/figures/<figure_name>/<figure_name>_caption.md. Record the figure revision, final PDF path, and the code-check and visual-check record paths. Keep the caption synchronized with the final figure revision; if a panel, label, color mapping, statistic, or output revision changes, rewrite or recheck the affected caption text.

Use this template:

Fig. N | <Short noun-phrase title> **a** <Brief panel description.> **b** <Brief panel description.> **c** <Brief panel description.> <Shared encodings and essential statistical context, defined once.> <Optional bounded observation.> Source data are provided as a Source Data file.

## Caption closure checklist

- [ ] Caption matches the final figure revision.
- [ ] Title is short, nominal, and free of unsupported numeric claims.
- [ ] Every panel has a precise present-tense description.
- [ ] Colors, shapes, lines, hatching, abbreviations, units, and reference marks are defined.
- [ ] Sample size, replicate unit, summary statistic, uncertainty, and statistical test are accurate where applicable.
- [ ] Canonical method and model names are preserved.
- [ ] Optional claim-closing sentence is bounded by displayed evidence.
- [ ] Source-data statement and any required permissions are included.
- [ ] Venue-specific length and formatting requirements are checked.
- [ ] Caption is one paragraph, with repeated explanations and nonessential methods or interpretation removed.
- [ ] Caption is saved beside the final figure records and references the final revision.
