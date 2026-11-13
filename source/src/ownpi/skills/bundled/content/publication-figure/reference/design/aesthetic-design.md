# Aesthetic Design

Aesthetic design turns the logical figure plan into a coherent visual object. It must make the evidence hierarchy easy to see, while preserving the scientific meaning. The `logical design artifact` is the previous stage's file at `<workspace>/output_synthesis/figures/<figure_name>/<figure_name>_logical_design.md`; it contains the claim, evidence chain, panel roles, expression tasks, reading order, and data boundaries. Read that exact file first and save this stage's result to `<workspace>/output_synthesis/figures/<figure_name>/<figure_name>_aesthetic_design.md`. If it is missing, complete logical design before continuing; do not invent a new claim or panel structure.

This document may contain small code fragments that clarify a design decision, but complete data processing, plotting functions, templates, and export scripts belong to Implement.

## 1. Specify the composition

Record the figure identity and version first:
```text
Figure name:
Active design version:
Logical artifact consumed:
```

Complete:
```text
Composition type:
Canvas orientation:
Overall reading path:
Primary visual focus:
Secondary visual groups:
Panel arrangement:
Panel proportions:
Shared axes or scales:
Legend location or direct-label strategy:
Whitespace and grouping strategy:
```

Make figure layouts as diverse as possible. Choose a quantitative grid, schematic-led composite, image plate with quantification, asymmetric mixed-modality layout, or another justified structure based on the logical design. Do not use equal-sized panels by default. Give more area and visual prominence to decisive evidence when this improves reading, while keeping controls and boundary evidence large enough to judge.

Keep panels tightly arranged with no conspicuous empty gaps, especially between columns; reserve space for actual labels rather than empty grid cells. Compose the complete multi-panel figure as a regular rectangle, keeping text, panel letters, legends, and annotations within its aligned outer boundaries. Allocate panel area by evidence role and information density. Justify an all-equal layout by a genuine need for comparable views, not convenience.

Describe the arrangement in words or a simple layout sketch. Do not begin with a favorite chart template or imitate a reference figure's panel count.

### How to design the composition well

Translate the logical evidence chain into a visible reading path. Put setup before evidence that depends on it, give decisive evidence the clearest position and largest justified area, and keep controls near the comparison they qualify. Do not give every panel equal size when their inferential importance or information density differs. A panel may span rows or columns when that makes its role clear.

For example, a primary comparison with two smaller qualifications can be represented by this design sketch:
```python
grid = fig.add_gridspec(2, 2, width_ratios=[1.7, 1.0])
primary = fig.add_subplot(grid[:, 0])
control = fig.add_subplot(grid[0, 1])
boundary = fig.add_subplot(grid[1, 1])
```

The code is only a visual notation here: it expresses hierarchy and grouping. Implement must adapt it to the declared final dimensions and actual data.

### How to make the composition work

Translate the evidence chain into a visual path: place setup before evidence that depends on it, give decisive comparisons the strongest visual weight, and keep controls close enough to compare. Use unequal panel sizes when evidence importance or information density differs. Use shared axes only when they improve a valid comparison; separate scales when a shared scale would hide an effect, and label the difference clearly. Treat the layout as a page-level argument.

### Canvas dimensions and shared legends

Choose the final manuscript width before setting the canvas. The source examples use roughly 7.0–7.4 inches wide and 5.5–7.8 inches high for journal composites; the larger trend (14 × 4 inches), heatmap (8–20 × 5–9 inches), and radar (12 × 10 inches) examples are authoring canvases, not universal final-size targets. Match the aspect ratio to the evidence and allocated page area. If a figure is reduced in width, its text and strokes shrink by the same factor: final text size = source text size × final width / source width.

Choose rows, columns, and overall width-to-height ratio together. If vertical stacking produces a narrow strip, use the available manuscript width and arrange compatible panels side by side before adding height. Preserve scientifically required panel aspect ratios; neither stretch plots nor force every figure into one fixed ratio.

Use a dedicated legend axis when repeated legends consume plot area:
```python
ax_legend = fig.add_subplot(1, n_panels + 1, n_panels + 1)
ax_legend.legend(handles, labels, loc="center", frameon=False, fontsize=8)
ax_legend.set_axis_off()
```

Each panel must answer a distinct part of the claim. Additional panels should reveal evidence rather than merely fill the grid.

## 2. Specify the visual language

Complete:
```text
Font family and fallback:
Body text size at final physical size:
Panel-letter style and position:
Panel headings, if needed:
Axis and tick treatment:
Line and marker treatment:
Color families and scientific meanings:
Grayscale and accessibility encoding:
Background and border treatment:
```

Use one coherent visual system across the figure. Keep colors tied to methods, conditions, cohorts, or biological meaning. Reserve accents for genuinely important distinctions. Use shape, position, line style, or annotation in addition to color when the distinction matters in grayscale or for color-vision accessibility.

Keep in-figure text to essential short labels and annotations; put explanatory sentences in the caption. Do not add a figure-wide title, subtitle, or footer statement.

Treat typography and dimensions as a linked decision. Specify sizes at the final physical output size, not only at an oversized authoring canvas. Long labels, legends, panel titles, uncertainty marks, and captions must fit their allocated space without shrinking the whole figure into illegibility.

### Typography and axes principles

Use Arial when available, with an explicit fallback recorded in the design. For dense journal figures, a practical starting point is 7–9 pt for ordinary text, with panel letters slightly larger and bold. Treat these as final-size values, not as values for a canvas that will later be scaled down. A small baseline is:
```python
mpl.rcParams.update({
    "font.family": "sans-serif",
    "font.sans-serif": ["Arial", "Helvetica", "DejaVu Sans"],
    "font.size": 7,
    "axes.linewidth": 0.8,
    "axes.spines.top": False,
    "axes.spines.right": False,
    "legend.frameon": False,
})
```

Keep left and bottom spines when they support reading; remove decorative top and right spines by default. Use sparse ticks and whitespace to guide the eye. Do not hide scale, zero references, or uncertainty merely to make a panel look cleaner.

Mathtext can shrink scripts to about 0.7 times the parent size. Inspect rendered glyphs after manuscript placement rather than trusting the source font size. At final journal size, axes around 0.8–1.2 pt provide a starting range; adjust them against the data marks so the axes do not dominate.

For editable SVG text set `svg.fonttype = "none"`. PDF font handling is separate: use `pdf.fonttype = 42` for TrueType embedding and verify the exported fonts and text. SVG settings do not control PDF text.

### How to make the visual language coherent

Choose one role for each visual channel. For example, use color for method or condition identity, position for the measured value, line style for estimate type, and shape for a secondary distinction. Keep the same mapping in every panel. Use a restrained neutral family for reference evidence and reserve strong accents for primary evidence or meaningful directional changes. Prefer direct labels when categories are spatially fixed; use one shared legend when repeated legends interrupt comparison. Keep panel letters small, bold, and consistently placed.

### How to choose a color palette

Choose color according to what it needs to communicate. Use a categorical palette for unordered models or conditions, a sequential palette for ordered magnitude, a diverging palette for values around a meaningful midpoint, and one hue with lightness or alpha changes for ablations or nested variants. Use grayscale plus one or two accents for image panels when the image itself carries the evidence.

Assign roles before selecting exact colors:

```text
Primary evidence:
Baseline or reference:
Control:
Secondary evidence:
Directional gain/loss:
Neutral background:
```

Keep each method, condition, cohort, or biological meaning mapped to the same color in every panel. Do not use a rainbow scale for unrelated categories or a sequential scale as unrelated categorical colors. Add shape, position, line style, hatching, or direct labels when a distinction must survive grayscale. Check contrast, grayscale interpretation, color-vision accessibility, and whether the primary evidence remains more salient than the baselines. Record the semantic mapping and deliberate exceptions in the design artifact.

Use an explicit palette family across panels. A useful starting family is `#484878`, `#7884B4`, and `#B4C0E4` for related baselines, with `#E4CCD8` and `#F0C0CC` for the focal method. Reserve `#2E9E44` and `#E53935` for signed gains or losses. Use grayscale context plus one or two accents for images, and one hue with changing lightness or alpha for ablations:
```python
base = (0.216, 0.459, 0.729)
colors = [(base[0], base[1], base[2], a) for a in np.linspace(0.2, 1.0, n)]
```
Test the rendered result on white, in grayscale, and with a color-vision simulator. The focal evidence should remain more salient than a large neutral reference group.


## 3. Specify each panel's visual treatment

For every panel in the logical design, complete:
```text
Panel:
Logical role:
Chart or image form:
Logical selected chart family:
Final visual form:
Panel geometry and relative size:
Color mapping and semantic meaning:
Visual emphasis:
Encoding of the main comparison:
Encoding of uncertainty or variation:
Label and annotation strategy:
Relationship to neighboring panels:
What must remain visually subordinate:
Implementation reference subsection:
Rendered acceptance criteria:
```

The `Final visual form` must correspond to the logical design's `Selected chart family` unless a deliberate change is recorded with a reason. Specify each panel's concrete visual form, geometry, color mapping, legend treatment, typography, and acceptance criteria; broad descriptions such as `quantitative comparison` are not sufficient for implementation.

Select the concrete chart form that best exposes the panel's expression task: for example, a point-and-interval plot for centers and uncertainty, a paired dot or slope plot for matched change, a distribution plot for spread and outliers, a line plot for temporal trends, a scatter plot for relationships, a heatmap for matrix structure, or a representative image with quantitative support. Explain the choice briefly. Do not use a bar chart merely because the input is tabular.

Keep the form visually quiet when the panel is supporting evidence. Use a larger marker, stronger contrast, or more space for the primary evidence. Use direct labels for a small number of spatially fixed series; use one shared legend when the same mapping repeats across panels. Avoid repeating a full legend in every panel.

### How to make a panel attractive and readable

Match visual density to information density. Use points and intervals when the claim depends on estimates and uncertainty; distributions when spread matters; paired links when matching matters; and a heatmap only when matrix structure carries meaning. Avoid decorative 3D effects, unnecessary gradients, redundant legends, and excessive colors. Give each panel one focal point, enough whitespace for labels, and a clear relationship to neighboring panels. Add a detail or zoom panel only when it reveals information hidden by the primary scale.

### Scales and chart-specific treatments

For narrow metric ranges, derive limits from the data, such as `ax.set_ylim(data.min() - margin, data.max() + margin)`, while preserving and labeling a zero reference whenever zero is scientifically meaningful. For bars, use modest error-bar caps (`capsize=4–5`) and thin outlines (`linewidth=1.0–1.5`); for lines, reserve heavier strokes and larger markers for the focal series and use transparent uncertainty bands; for heatmaps, use sequential scales for ordered magnitude, diverging scales around a meaningful midpoint, and explicit missing-value masks; use radar plots only with few axes and a common scale.


A z-score heatmap should expose relative structure with its normalization explicitly labeled; a bubble scatter should add size encoding only when it reveals a useful third quantity, with a size legend that makes the area mapping interpretable.

## 4. Use visual references correctly

When using an example, record:
```text
Reference pattern:
Transferable principle:
Adaptation to this figure:
Element deliberately not copied:
```

Transfer information hierarchy, spacing, label discipline, palette logic, or panel relationships. Do not copy protected artwork, distinctive layouts, or decorative elements without a scientific reason. A complex appearance is not a goal by itself; the final design must remain faithful to the logical design and available evidence.

### Color and reference principles

Assign color a stable scientific meaning across the entire figure. Keep related baselines in one restrained family, keep the focal method or condition in a coherent signal family, and reserve red or green for directional changes when possible. Always add position, shape, or line style when color alone would carry an important distinction. Check the figure in grayscale and inspect whether the intended primary evidence remains visually dominant.

### How to use examples

Compare examples by structure rather than surface resemblance. Ask what the example makes prominent, how it groups panels, how it handles labels and legends, and which visual channel carries each meaning. Transfer those decisions only when the current figure has the same information need. Record the adaptation in the design artifact so the reference does not silently become a template.

## 5. Aesthetic design gate

Follow the required output policy in ../implement/global-settings.md: SVG, PDF, and PNG are required for every figure; TIFF is generated only when the submission venue requires it. Use the declared final physical size and the raster resolution specified there. Inspect clipped labels, thin lines, annotations, font embedding, and transparency at the actual submission size.

Before handing the document to Implement, verify:

- exactly one active aesthetic design exists for the declared `Figure name`;
- the declared logical artifact is the single active version consumed by this design;

- the composition makes the logical reading order apparent;
- the primary evidence is visually prominent for a defensible reason;
- panel sizes reflect information and evidence needs;
- all panel forms match their expression tasks;
- every panel has a concrete visual form and implementation reference subsection;
- a multi-panel figure does not use one chart type for every panel unless the exception is explicitly justified and reviewed;
- typography, colors, axes, labels, and legends form one coherent system;
- the design remains readable at the declared final physical size;
- grayscale and color-accessible encodings preserve the important comparisons;
- no visual emphasis implies an unsupported scientific claim;
- the design can be implemented reproducibly without relying on manual editing;
- unresolved aesthetic decisions are recorded rather than silently delegated to the code-writing stage.

If the problem concerns claim, evidence, panel role, or reading order, return to the logical design artifact. If it concerns visual treatment, composition, spacing, or typography, revise this artifact before implementation.
