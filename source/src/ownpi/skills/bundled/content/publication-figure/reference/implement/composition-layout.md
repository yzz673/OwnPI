# Python Publication Figure: Multi-Panel Composition Tutorial

This tutorial combines defined panels into a publication figure with a clear reading path and evidence hierarchy. The layout must give decisive evidence appropriate space and visual weight, keep supporting evidence near the comparison it qualifies, and prevent legends, colorbars, and panel labels from obscuring data.

## Contents

1. Place stable panel labels
2. Declare and verify panel alignment
3. Build a multi-metric layout
4. Build a dedicated legend panel
5. Combine a hero panel with supporting quantification
6. Compose a dark image plate
7. Compose repeated clinical rows
8. Build an asymmetric hero layout
9. Combine conceptual and quantitative panels
10. Compose ablation panels and reference baselines
11. Implement complex GridSpec layouts

---

## 1. Place stable panel labels

```python
def add_panel_label(ax, label, x=0, y=1, x_offset_pt=-4, y_offset_pt=3,
                    fontsize=8, color='black', fontweight='bold', va='bottom'):
    """Place a panel label with a fixed physical offset from an axes anchor."""
    from matplotlib.transforms import ScaledTranslation
    offset = ScaledTranslation(
        x_offset_pt / 72,
        y_offset_pt / 72,
        ax.figure.dpi_scale_trans,
    )
    ax.text(
        x, y, label,
        transform=ax.transAxes + offset,
        fontsize=fontsize,
        fontweight=fontweight,
        color=color,
        ha='left',
        va=va,
    )
```

The point offset keeps labels aligned when one panel spans two rows and its neighbouring axes are shorter; a shared axes-fraction offset such as `y=1.02` would produce different physical displacements. For dark image plates, move the label inside the panel and switch to white: `add_panel_label(ax, 'a', x=0.01, y=0.98, x_offset_pt=0, y_offset_pt=0, color='white', va='top')`

---

## 3. Build a multi-metric layout

For 3–4 metrics compared across many methods, use a wide canvas so bars and labels don't crowd.

```python
fig = plt.figure(figsize=(45, 12))   # or (28, 6) for fewer metrics
gs = gridspec.GridSpec(1, n_metrics)

for i, metric in enumerate(metrics):
    ax = fig.add_subplot(gs[i])
    ax.bar(x, values[metric], color=colors, ...)
    ax.set_ylabel(metric, fontsize=54, labelpad=12)
    ax.set_xticks([])

# Last panel: legend only
ax_leg = fig.add_subplot(gs[-1])
ax_leg.legend(handles, labels, fontsize=38, loc='center', frameon=False)
ax_leg.set_axis_off()

fig.tight_layout(pad=2)
```

**Rule**: Width often 3–4× height. Allows left-to-right narrative scanning.

---

## 4. Build a dedicated legend panel

When the legend is large, give it its own axis so data panels stay clean.

```python
fig, axes = plt.subplots(1, n_data + 1, figsize=(...))

for i, ax in enumerate(axes[:-1]):
    bars = ax.bar(...)
    if i == 0:
        handles, labels = ax.get_legend_handles_labels()

# Legend-only panel
axes[-1].legend(handles, labels, fontsize=28, loc='center', frameon=False)
axes[-1].set_axis_off()
```

---

## 5. Combine a hero panel with supporting quantification

Use when one mechanism or fabrication story needs to lead, with 2–4 smaller evidence plots below.

```python
fig = plt.figure(figsize=(7.2, 6.2))
gs = fig.add_gridspec(
    2, 4,
    height_ratios=[2.2, 1.0],
    hspace=0.18, wspace=0.28,
)

ax_top = fig.add_subplot(gs[0, :])    # hero schematic
ax_b = fig.add_subplot(gs[1, 0])
ax_c = fig.add_subplot(gs[1, 1:3])
ax_d = fig.add_subplot(gs[1, 3])

# top panel should carry the main palette and the main visual narrative
```

Rules:

- Allocate `45–60%` of total height to the hero schematic.
- Reuse softened versions of the same colors in the lower plots.
- Keep support plots quieter than the hero panel.

---

## 6. Compose a dark image plate

Use for microscopy, volume rendering, or fluorescence-heavy panels.

```python
fig = plt.figure(figsize=(7.2, 6.5))
gs = fig.add_gridspec(3, 5, hspace=0.08, wspace=0.04)

for r in range(3):
    for c in range(5):
        ax = fig.add_subplot(gs[r, c])
        ax.set_facecolor('black')
        ax.set_xticks([])
        ax.set_yticks([])
        for spine in ax.spines.values():
            spine.set_visible(False)
```

Rules:

- Use black only within the image plate cells.
- Put channel labels, scale bars and small crop guides directly on the plate.
- Keep crop geometry and scale-bar placement consistent across the grid.

---

## 7. Compose repeated clinical rows

Use for outcome-over-time figures that combine trajectories, effect sizes, and summary proportions.

```python
fig = plt.figure(figsize=(7.2, 6.8))
gs = fig.add_gridspec(
    3, 3,
    height_ratios=[1.0, 1.35, 0.8],
    hspace=0.28, wspace=0.32,
)

axes_top = [fig.add_subplot(gs[0, i]) for i in range(3)]
axes_mid = [fig.add_subplot(gs[1, i]) for i in range(3)]
axes_bot = [fig.add_subplot(gs[2, i]) for i in range(3)]

# Put one shared legend strip above axes_top rather than repeating legends.
```

Rules:

- Keep the three columns semantically parallel.
- Use a dashed vertical reference line in the forest-plot row.
- Group shading in the forest-plot row should be pale and subordinate.

---

## 8. Build an asymmetric hero layout

Use when one panel is conceptually central and should dominate.

```python
fig = plt.figure(figsize=(7.2, 5.8))
gs = fig.add_gridspec(3, 4, hspace=0.25, wspace=0.28)

ax_a = fig.add_subplot(gs[0, :2])
ax_b = fig.add_subplot(gs[0, 2])
ax_c = fig.add_subplot(gs[1, :2])
ax_d = fig.add_subplot(gs[1, 2])
ax_e = fig.add_subplot(gs[:, 3])      # hero panel spans all rows
ax_f = fig.add_subplot(gs[2, :2])
```

Rule: do not normalize every subplot to the same size if the science does not have equal importance.

---

---

## 9. Combine conceptual and quantitative panels

Use when a manuscript needs a conceptual mechanism panel that links a probability shift to a geometric or latent-space explanation. Build the pair from the original pattern below: a 1D probability-density panel beside a contour/scatter manifold panel.

**Pattern:**
- Left panel: draw 2-3 probability curves with transparent fills; use one vertical reference line and a single double-headed arrow to define the conceptual gap.
- Right panel: sample points around smooth center curves, show low-alpha clouds, contour density bands, and a small number of highlighted trajectory markers.
- Keep axes only where they carry meaning. The manifold panel can be axis-free if labels and arrows carry the explanation.
- All math labels and manifold names must map to real manuscript concepts. Do not reuse demo labels such as `VIG` or `DPO` unless they are the user's actual terms.

```python
fig, (ax_prob, ax_manifold) = plt.subplots(1, 2, figsize=(24, 6))
plot_distribution(ax_prob)  # probability curves + conceptual gap arrow
plot_manifold(ax_manifold)  # density contours + trajectory markers
fig.tight_layout(pad=0.5)
```

---

## 10. Compose ablation panels and reference baselines

Use when an ablation compares data fraction, hyperparameters, or coupled metrics across a small set of methods. A robust original layout uses three horizontal panels: data fraction, one hyperparameter sweep, and a dual-axis coupled metric sweep.

**Pattern:**
- Use a dashed horizontal baseline for the simple/reference model.
- Use a dotted horizontal line for a meaningful operating point, e.g. "ours at 25% data", only when that comparison is called out in the text.
- Use `twinx()` sparingly. If two y-axes are needed, color each y label to match the corresponding series and keep tick ranges narrow.
- Put legends inside low-density regions of each panel; avoid one giant legend if panel-specific series differ.

```python
fig, axes = plt.subplots(1, 3, figsize=(27, 6),
                         gridspec_kw={"width_ratios": [1.1, 1, 1]})
axes[0].plot(x, baseline, color="black", alpha=0.3, lw=4, ls="--")
axes[0].plot(x, reference, color=hero_color, lw=3, ls=":")
ax2 = axes[2].twinx()
```

---

## 11. Implement complex GridSpec layouts

```python
from matplotlib import gridspec

# 2-row, 4-column layout
fig = plt.figure(figsize=(36, 12))
gs = gridspec.GridSpec(2, 4)

ax_top_left  = fig.add_subplot(gs[0, 0])
ax_top_right = fig.add_subplot(gs[0, 1:3])   # span columns 1-2
ax_legend    = fig.add_subplot(gs[0, 3])     # legend panel
ax_bottom    = fig.add_subplot(gs[1, :])     # full-width bottom
```

---
