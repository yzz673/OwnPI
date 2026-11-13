# Python Publication Figure: Code Check Tutorial

Code checks run after the plotting script is written and before the figure is accepted for rendering review. They catch deterministic failures that can be proven from source code, input data, or exported geometry. They do not replace visual inspection. If a code check passes but the rendered figure is unclear, return to the relevant design or implementation tutorial.

Before checking individual properties, verify the implementation handoff: there is exactly one active source script and implementation artifact, every panel has a selected chart family and named implementation function, each function matches the logical and aesthetic design, and the source, inputs, exports, and reports use one figure output prefix.

Create the persistent checklist before executing checks. Every applicable item below must appear as its own row; never replace rows with a prose summary. Use this record:

```text
Figure name:
Active implementation:
Checklist item ID:
Check:
Status: TODO | PASS | FAIL | WARNING | NOT APPLICABLE
Evidence:
Source or report location:
Failure, warning, or exemption reason:
Rerun after revision:
```

Execute one row at a time. A `FAIL` or `WARNING` requires a correction or an explicit documented reason, followed by rerunning the affected row. `NOT APPLICABLE` requires a reason tied to the figure structure. The final checklist must include a verdict:

```text
Final verdict: PASS | BLOCKED
Unresolved items:
```

`PASS` is valid only when every applicable row is `PASS`, every exemption is explained, and no unresolved item remains. Do not hand off to Export or Visual Check with an incomplete checklist.

## Contents

1. Validate the input contract
2. Validate numeric and statistical encodings
3. Validate labels and annotations
4. Validate typography and export settings
5. Validate multi-panel geometry
6. Run the deterministic preflight
7. Code-check checklist

## 1. Validate the input contract

Match every input field to the logical design before drawing. Check category order, replicate unit, paired identifiers, metric direction, units, and uncertainty definition. Reject missing, non-finite, duplicated, or incomplete observations unless the exclusion rule is explicit and recorded. Keep all observations by default; never use a plotting convenience that silently drops categories or rows.

For CSV-driven templates, validate the required columns and their types before creating a figure. Production figures must use real input data; demonstration or simulated data are suitable only for smoke tests and must be marked in the output record. The template catalog's --drop-incomplete behavior is an explicit exception that must record exclusion counts.

## 2. Validate numeric and statistical encodings

Check that each estimate has the correct lower and upper uncertainty bound, that intervals are ordered, and that all values use the same transformation and unit. Check monotonicity before interpolation: reject duplicate x values, direction changes, and unsorted paired arrays. If an x grid is decreasing, reverse it together with its paired values.

Do not add significance marks, error bars, confidence bands, or reference lines unless their statistical meaning is defined. Confirm the replicate unit, test, assumptions, multiplicity correction, and legend wording before encoding statistics. A plotting script must not invent hypothesis tests.

Keep zero references when zero has scientific meaning. A tightened axis is valid only when the scale remains explicit and the choice does not conceal the comparison. For logarithmic axes, reject non-positive values and reserve enough multiplicative headroom for annotations.

## 3. Validate labels and annotations

Check that every panel has the intended label, axis meaning, unit, and category mapping. Use the luminance-aware text choice for in-bar or cell annotations. Place labels above the upper uncertainty extent or outside the mark using a data-dependent margin; do not rely on a fixed y coordinate that can overlap another mark.

Check long labels against the allocated slot width at final physical size. Reject clipped labels, overlapping annotations, hidden error bars, and legends that cover data. Use Unicode for simple symbols such as R² when mathtext would make a subscript or superscript fall below the readable glyph floor.

## 4. Validate typography and export settings

Set the font family, fallback stack, editable SVG text, and PDF font type before creating the figure:

```python
mpl.rcParams.update({
    "font.family": "sans-serif",
    "font.sans-serif": ["Arial", "Helvetica", "DejaVu Sans"],
    "svg.fonttype": "none",
    "pdf.fonttype": 42,
})
```

Check font size and line width at the final manuscript size, rather than only on an oversized authoring canvas. Verify the global-settings.md output policy: SVG, PDF, and PNG are required for every figure; TIFF is required only when the submission venue requests it. Use 300–600 dpi for raster output when required, and confirm that the bounding box does not clip any mark or label.

## 5. Validate multi-panel geometry

Run alignment checks after the last layout operation and before saving. The check must operate on the final drawn canvas, after tight_layout, constrained layout, legends, colorbars, or manual position changes. Compare only panels that are intended to share rows, columns, or boundaries.

```python
from audit_panel_alignment import require_matplotlib_panel_alignment

require_matplotlib_panel_alignment(
    fig,
    axes=[ax_a, ax_b, ax_c],
    panel_ids=["a", "b", "c"],
    tolerance_pt=1.5,
    gutter_tolerance_pt=1.5,
    require_panel_labels=True,
    strict=True,
    json_out="figure.alignment.json",
    overlay_svg="figure.alignment.svg",
)
```

Declare intentional unequal spans with panel-specific exemptions and a reason. Do not increase a global tolerance to hide a layout error. Exclude legend-only axes, colorbars, and insets from unrelated comparison groups. A multi-panel figure with no valid comparison groups is not auditable and must not be reported as passing.

## 6. Check source structure before rendering

Run syntax and import checks before executing the plotting script. Confirm that every module, function, variable, output directory, and output prefix exists. Treat syntax errors, missing imports, undefined names, invalid arguments, and missing output configuration as blocking failures.

Check array contracts before passing data to Matplotlib. Every x and y series must have equal length; labels, colors, and series must match; uncertainty arrays must match values; heatmap labels must match matrix dimensions; and scatter coordinates, colors, and sizes must be compatible. Check that radar polygons are closed and that their value count matches the axis count. Empty groups and silently discarded categories are blocking failures.

## 7. Separate expected geometry from rendered geometry

Code checks validate intended figure dimensions, GridSpec spans, width and height ratios, declared row and column groups, panel labels, and exemptions. They cannot prove that text, legends, colorbars, or annotations fit after rendering. The visual-check stage must inspect actual SVG or PDF bounding boxes. A source-level geometry pass is not proof that the rendered figure is visually correct.

## 8. Validate auxiliary axes and encodings

Declare legend-only axes, colorbars, insets, and decorative axes explicitly, and exclude them from unrelated data-panel alignment groups. Every intentional unequal span must identify its panels, exact exemption, and reason. Check that method, condition, estimate type, and direction mappings remain stable across panels and that important color-only distinctions have a shape, position, line-style, or hatch fallback.

## 9. Chart-specific structural checks

For bar and box-style charts, check group order, non-empty groups, value and uncertainty lengths, and negative-value handling. For violin plots, check finite observations in every group and record smoothing settings that affect interpretation. For line and area plots, check x ordering, series lengths, uncertainty bounds, and stacked-component semantics. For heatmaps, check dimensions, masks, normalization direction, midpoint semantics, and complete tick labels. For scatter, bubble, and radar plots, check finite coordinates, valid sizes, common scales, axis counts, and closed polygons.

## 10. Failure severity

Block delivery for invalid input, syntax or import errors, undefined names, incompatible shapes, non-finite required values, invalid uncertainty, non-positive log values, missing required labels, undeclared geometry exemptions, and failed deterministic checks. Record warnings for low contrast, dense but valid labels, palette concerns, whitespace, salience, or possible clipping that can only be judged after rendering; identify the affected panel and pass it to visual check.

## 11. Run the deterministic preflight

Run validate_figure.py on the source script before rendering. Load figure_safety.py for strict interpolation and annotation placement. Run audit_panel_alignment.py after the final layout draw. Run audit_pdf_text.py and audit_figure_collisions.py after export when the corresponding dependencies are available. Preserve JSON results with the figure bundle.

These checks are blocking when they report invalid input, missing required fields, non-finite values, failed geometry, clipping, or unreadable glyphs. Do not catch and ignore a check exception in a delivery script. A passing code check means the deterministic contract is satisfied; it does not certify aesthetic quality.

## 12. Persist the code-check checklist

Create a persistent checklist for every figure at <workspace>/output_synthesis/figures/<figure_name>/<figure_name>_code_check.md. Record the check date, source script, input data, declared final size, and current verdict in the file. Do not treat chat output or temporary terminal output as the checklist.

Use the checklist as an iterative gate. Run the checks, record PASS, FAIL, or NOT APPLICABLE with evidence, fix every FAIL in the source code or input contract, and run the affected checks again. Keep the previous finding and corrective action when a check has failed. A warning must identify its panel and be handed to visual check.

Implement is complete only when every applicable item is PASS, every NOT APPLICABLE item has a reason, all blocking failures are resolved, and the final source and input versions are recorded. Only then hand the figure to rendered visual check.

Use this checklist:

| ID | Check | Status | Evidence or output | Corrective action |
|---|---|---|---|---|
| CC-01 | Input fields and types | TODO | | |
| CC-02 | Category, replicate, and pairing contract | TODO | | |
| CC-03 | Finite values, missingness, and duplicates | TODO | | |
| CC-04 | Array shapes and label/color lengths | TODO | | |
| CC-05 | Uncertainty and statistical encodings | TODO | | |
| CC-06 | Axis scale and zero/log semantics | TODO | | |
| CC-07 | Annotation and label placement logic | TODO | | |
| CC-08 | Font, text, and export configuration | TODO | | |
| CC-09 | Intended GridSpec and panel geometry | TODO | | |
| CC-10 | Legend, colorbar, inset, and exemption declarations | TODO | | |
| CC-11 | Source syntax, imports, names, and arguments | TODO | | |
| CC-12 | Deterministic preflight and numerical safety | TODO | | |
| CC-13 | Required SVG, PDF, preview, and conditional TIFF outputs | TODO | | |

Record iteration history below the table, including checks run, failures fixed, and remaining work. Use PASS, FAIL, NOT APPLICABLE, and TODO as the only status values. READY_FOR_VISUAL_CHECK is valid only when no applicable item remains TODO or FAIL.

- [ ] Real input data and explicit field mappings are used.
- [ ] Categories, replicates, uncertainty, units, and transformations are validated.
- [ ] NaN, Inf, duplicates, incomplete pairs, and non-positive log values are handled explicitly.
- [ ] Axes, zero references, annotations, and statistical marks have defensible semantics.
- [ ] Font, editable text, final-size typography, and export formats are configured.
- [ ] Multi-panel alignment, gutters, labels, legends, and exemptions are checked after layout.
- [ ] Source preflight and numerical safety checks pass.
- [ ] Exported PDF text and geometry checks pass when applicable.
- [ ] Any visual uncertainty is handed to the rendered visual-check stage.
