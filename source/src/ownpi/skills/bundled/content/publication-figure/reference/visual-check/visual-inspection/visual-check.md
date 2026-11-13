# Publication Figure: Visual Inspection Guide

Before creating this record or writing any visual `PASS`, verify that the current canonical script-check report exists, matches the current export revision, and has final verdict `PASS`. If it does not, leave this record `INCOMPLETE`, write no visual `PASS`, and return to Implement. Then inspect the actual exported figure for readability, spacing, color contrast, visible chart elements, and rendering quality. Use `view_image` or an equivalent image viewer to examine the figure; source code, file metadata, and automated reports cannot establish a visual PASS. This review does not reassess the scientific claim, evidence-chain structure, panel necessity, Results narrative, or main-figure versus supplementary placement.

Save the inspection record at `<workspace>/output_synthesis/figures/<figure_name>/<figure_name>_visual_check.md`. Maintain this file through the check, revision, export, and recheck cycle. A figure can proceed to delivery only after the applicable checks below are closed with evidence.

## Contents

1. [Prepare the inspection](#1-prepare-the-inspection)
2. [Inspect panel geometry and spacing](#2-inspect-panel-geometry-and-spacing)
3. [Inspect typography and readability](#3-inspect-typography-and-readability)
4. [Inspect color and contrast](#4-inspect-color-and-contrast)
5. [Inspect chart elements and labels](#5-inspect-chart-elements-and-labels)
6. [Inspect rendered output quality](#6-inspect-rendered-output-quality)
7. [Record, revise, and close the review](#7-record-revise-and-close-the-review)

## 1. Prepare the inspection

Read the aesthetic specification at `<workspace>/output_synthesis/figures/<figure_name>/<figure_name>_aesthetic_design.md` for the intended dimensions, typography, color mappings, and layout. Use it as the reference for visual consistency. Follow the file-format requirements in [Global Settings](../../implement/global-settings.md); do not introduce a separate export policy here.

Read the matching `<figure_name>_code_check.md` and the latest report from the [render-check entry point](../script-check/README.md). Match reports to the current figure revision. Record unresolved automated findings; a successful process exit or summary verdict does not make an unperformed or unauditable check pass.

Prepare three views of the same revision:

- The complete PNG preview, for spacing, legibility, and consistency across panels.
- A rendered page image of the actual PDF intended for manuscript placement, for export and conversion problems.
- Enlarged panel crops from the exported image or PDF render, for small labels, mathematical symbols, uncertainty marks, and clipping.

Inspect the whole figure before inspecting crops, then return to the whole figure. An enlarged crop can expose a defect but cannot prove that a label is readable in normal use. Inspect at the intended manuscript placement scale as well as enlarged. If the viewer automatically rescales images, record the preview dimensions and intended physical width; do not infer an exact physical font size from display pixels alone. If a manuscript page is available, render that page to inspect the actual placement.

Keep inspection renders and crops separate from the deliverable files. Record their paths and the figure revision they represent. If an image cannot be opened or its resolution is insufficient to judge a check, leave that check TODO or WARNING with the reason. Never report an unviewed figure as visually inspected.

## 2. Inspect panel geometry and spacing

Compare neighboring panel edges, plot areas, labels, legends, and colorbars in the actual image. Assess their intended alignment relationships, not whether every rectangle has identical dimensions. A spanning panel, image with a fixed aspect ratio, or deliberately unequal layout can be correct.

Look for squeezed labels, uneven gaps between comparable panels, excessive empty space caused by an oversized legend slot, and colorbars that visually detach from their panels. Compare the spaces between the actual objects rather than across an entire horizontal pixel row. Dark image panels may need more separation from light plot panels to make their boundary clear.

Check that panel letters have consistent physical offsets, that labels sit visibly with the correct panel, and that legends and colorbars do not cover marks or invade adjacent panels. For repeated image views, check crop alignment, scale-bar placement, and channel-label positions without requiring different images to have identical content.

| Check ID | Inspect | Observable failure |
|---|---|---|
| VI-01 | Intended alignment and rectangular outer composition | Comparable edges visibly drift, panels collide, or text, panel letters, legends, or annotations disrupt the complete figure's regular rectangular outline. |
| VI-02 | Gutters, margins, canvas proportions, and panel hierarchy | Labels are squeezed, conspicuous empty gaps remain between panels, vertical stacking wastes available width, or equal panel sizes erase the declared evidence hierarchy. |
| VI-03 | Panel identifiers and association | Letters have inconsistent offsets, are clipped, or appear attached to the wrong panel. |

## 3. Inspect typography and readability

Read axis labels, ticks, legends, cell values, and annotations in the complete figure at the intended viewing scale. Check recognizable letterforms, adequate separation, and consistent apparent size across comparable panels. Text that can only be read in a magnified crop is not sufficient for normal figure use.

Inspect long names, rotated labels, superscripts, subscripts, minus signs, and other mathematical symbols in enlarged crops. Look for missing glyph boxes, unexpected font substitution, cramped kerning, labels cut at the page edge, and mathematical scripts that become much smaller than surrounding text. Compare SVG and PDF appearance when conversion may have changed fonts.

Use the selected aesthetic specification and applicable venue requirements for sizes. Do not create a new font-size threshold in this review. When text is too small, first consider whether wrapping, more space, shorter approved display labels, or a different legend arrangement can preserve readability; do not repeatedly shrink labels simply to remove overlap.

| Check ID | Inspect | Observable failure |
|---|---|---|
| VI-04 | Normal-scale text readability | Ticks, legend entries, or annotations cannot be read comfortably at the intended placement scale. |
| VI-05 | Font and text-role consistency | Comparable labels use visibly different fonts or sizes, or panel letters overwhelm ordinary labels. |
| VI-06 | Long labels and mathematical glyphs | Clipping, missing symbols, illegible scripts, or collisions prevent reading the intended text. |

## 4. Inspect color and contrast

View the actual figure on its intended background. Check that neighboring fills, markers, lines, and labels remain distinguishable. Pale marks can disappear on white even when they are technically different colors; dark fills can hide black text; overlapping translucent bands can merge into an unintended color.

Compare the same methods or conditions across panels against the declared color mapping. Confirm that line styles, markers, direct labels, or hatching preserve important distinctions in grayscale. Inspect a grayscale preview, and inspect a color-vision simulation when interpretation depends on hue. Record which views were actually inspected; do not claim an accessibility check from the palette name alone.

Assess consistency with the intended visual treatment, including restrained backgrounds and readable accents. Do not rank the scientific importance of panels or reassign their evidence roles. Judge whether specified distinctions can be seen and whether decorative contrast interferes with reading.

| Check ID | Inspect | Observable failure |
|---|---|---|
| VI-07 | Foreground and background contrast | Text or marks disappear into their background, or adjacent fills are visually indistinguishable. |
| VI-08 | Cross-panel color mapping | The same entity changes apparent color or an accent conflicts with its declared meaning. |
| VI-09 | Distinctions beyond hue | Important series merge in grayscale or relevant color-vision views without another readable encoding. |

## 5. Inspect chart elements and labels

Check the actual visibility of the information drawn by the chart. Error-bar caps must not disappear behind markers; confidence bands must remain visible without hiding curves; thin reference lines must survive reduction; hatching must not become a dark texture that overwhelms data. Look for annotations, brackets, arrows, and opaque text backgrounds that cover marks.

Check each applicable chart family:

- For bars, distributions, and interval plots, inspect outlines, group spacing, individual points, whiskers, intervals, and annotations for occlusion or visual crowding.
- For lines and area plots, inspect marker density, line separation, reference-line visibility, overlapping uncertainty bands, and labels inside filled regions.
- For heatmaps, inspect cell-text contrast, label alignment with rows and columns, the readable colorbar, and visible distinction between missing cells and ordinary values.
- For scatter and bubble plots, inspect overplotting, marker-size distinguishability, and whether size and color legends are readable.
- For image panels, inspect image sharpness, crop boundaries, channel labels, scale bars, and whether overlays cover the feature being shown.

Read every legend and direct label while looking at its associated mark. Check that symbols shown in the legend visually match the plotted symbols. These checks concern visible encodings; they do not validate the statistical calculation or the scientific conclusion.

| Check ID | Inspect | Observable failure |
|---|---|---|
| VI-10 | Marks, intervals, and reference lines | Markers hide intervals, bands hide lines, or thin elements disappear at placement scale. |
| VI-11 | Legends, direct labels, and annotations | Symbols cannot be matched to the plot, labels overlap data, brackets obscure uncertainty, or the canvas contains sentence-heavy annotations or figure-wide titles, subtitles, or footer statements. |
| VI-12 | Chart-family details | Unreadable heatmap values, indistinct bubble sizes, crowded distribution marks, or obscured image scale bars impair interpretation. |

## 6. Inspect rendered output quality

Open the required exports specified in Global Settings, rendering vector files to an image when needed. File existence is an input check; this step judges what those files actually display. Compare the PDF-derived preview with the PNG and, when needed, an SVG render. Watch for blank or missing panels, changed fonts, different cropping, lost transparency, altered line weight, and unexpected backgrounds.

Inspect the intended manuscript placement scale for blur or aliasing. Higher PNG DPI cannot repair text that is too small in the layout, and a vector file can still contain low-resolution images. If TIFF is required, inspect its actual rendered appearance too; otherwise record that TIFF-specific inspection is NOT APPLICABLE.

| Check ID | Inspect | Observable failure |
|---|---|---|
| VI-13 | Consistency across required exports | Panels, labels, colors, crop bounds, or transparency change between SVG, PDF, and PNG. |
| VI-14 | Sharpness at placement scale | Raster content blurs, thin marks disappear, or text becomes unreadable after insertion or scaling. |
| VI-15 | Complete rendered page | The page is blank or incomplete, or any required element is clipped at export boundaries. |
| VI-16 | Declared panel form | A rendered panel does not match the active aesthetic design or implementation handoff. |

## 7. Record, revise, and close the review

Create the inspection record before beginning the review. Start each item as TODO and record the specific view inspected, the affected panel, and an observable result. A useful finding identifies the location and symptom, such as "panel b: the last two tick labels overlap in the whole-figure PDF preview." Avoid descriptions such as "looks wrong" without a location or visible defect.

Use these item statuses:

| Status | Meaning |
|---|---|
| TODO | The relevant view has not yet been inspected, or a changed revision invalidated the previous result. |
| PASS | The current exported revision was inspected and satisfies the criterion; evidence is recorded. |
| FAIL | A visible defect requires correction before delivery. |
| WARNING | The outcome is uncertain or a suspected defect needs another view, measurement, or review. This is not a pass. |
| NOT APPLICABLE | The check does not apply to this figure; record a specific reason. |

Resolve a WARNING into PASS, FAIL, or NOT APPLICABLE with evidence. Missing inspection tools or missing previews are not reasons for NOT APPLICABLE. Use INCOMPLETE while views or checks are missing, NEEDS_REVISION while visible defects remain, and READY_FOR_DELIVERY only when every applicable visual item is PASS and every exception has a reason. Required automated checks must also be resolved; visual closure must not conceal outstanding script failures or unauditable required checks.

For a local rendering mismatch, revise Implement. If the intended layout, label strategy, or palette cannot satisfy readability, revise [Aesthetic Design](../../design/aesthetic-design.md), then implement it again. Do not redefine the scientific question or reorganize evidence roles during this visual review.

After a change, rerun affected code checks, regenerate the required exports, refresh automated reports, and inspect the revised views. Mark affected visual items TODO before rechecking; any layout, scale, font, or palette change requires a fresh whole-figure inspection because it can affect neighboring panels. Preserve prior findings and corrective actions in the iteration log. Record source and export hashes or another unambiguous revision identifier so a PASS cannot silently refer to an older figure.

Use the following record structure. Expand a row into panel-specific rows when different panels have different outcomes; do not mark an aggregate item PASS until every applicable panel has been inspected.

```markdown
# Visual Check: <figure_name>

- Source script and revision:
- Aesthetic design artifact:
- Code-check record:
- Automated render-check report and revision:
- SVG / PDF / PNG paths and hashes:
- TIFF path, if required:
- PDF-derived preview and panel-crop paths:
- Grayscale / color-vision preview paths:
- Intended physical dimensions and manuscript placement scale:
- Viewing scale or viewer-resizing limitation:
- Last inspected:
- Overall verdict: INCOMPLETE

## Inspection Results

| ID | Criterion | Panel or scope | Status | Viewed file and evidence | Required action |
|---|---|---|---|---|---|
| VI-01 | Intended alignment and boundaries | All | TODO | | |
| VI-02 | Gutters, margins, auxiliary space | All | TODO | | |
| VI-03 | Panel identifiers | All | TODO | | |
| VI-04 | Normal-scale readability | All | TODO | | |
| VI-05 | Font and text-role consistency | All | TODO | | |
| VI-06 | Long labels and mathematical glyphs | All | TODO | | |
| VI-07 | Foreground/background contrast | All | TODO | | |
| VI-08 | Cross-panel color mapping | All | TODO | | |
| VI-09 | Distinctions beyond hue | All | TODO | | |
| VI-10 | Marks, intervals, reference lines | All | TODO | | |
| VI-11 | Legends and annotations | All | TODO | | |
| VI-12 | Chart-family details | All | TODO | | |
| VI-13 | Export consistency | All exports | TODO | | |
| VI-14 | Sharpness at placement scale | All | TODO | | |
| VI-15 | Complete rendered page | All exports | TODO | | |
| VI-16 | Declared panel form | All panels | TODO | | |

## Iteration Log

| Iteration | Export revision | Finding IDs | Correction and target | Rechecked views | Result |
|---|---|---|---|---|---|
| 1 | | | | | |

## Closure

- All applicable items are PASS:
- Every NOT APPLICABLE item has a reason:
- No TODO, FAIL, or WARNING remains:
- Required automated checks are resolved for this revision:
- Final whole-figure and PDF-placement views were inspected:
- Evidence identifies the final deliverable revision:
- Overall verdict:
```
