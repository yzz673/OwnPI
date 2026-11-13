# Python Publication Figure: Grouped bar chart (multi-metric comparison)

## Purpose

This complete example demonstrates grouped bar chart (multi-metric comparison) from style setup through export.

## When to use

Use this example when the panel data and scientific question match its stated chart structure.

## Design assumptions

Follow the logical and aesthetic design artifacts first. Treat the dimensions, palette, labels, and uncertainty encoding below as explicit implementation decisions, then adapt them to the final physical size.

## Complete Python script

**Goal**: Several methods compared across multiple metrics. Legend in a dedicated panel. When methods belong to related families, use one coherent baseline family plus one coherent hero family.

```python
import os
import numpy as np
import matplotlib.pyplot as plt
from matplotlib import gridspec

# --- Style ---
plt.rcParams['font.family'] = 'sans-serif'
plt.rcParams['font.sans-serif'] = ['Arial']
plt.rcParams['svg.fonttype'] = 'none'
plt.rcParams['font.size'] = 24
plt.rcParams['axes.spines.right'] = False
plt.rcParams['axes.spines.top'] = False
plt.rcParams['axes.linewidth'] = 3

# --- Data ---
methods = ['ResNet1d18', 'ResNet1d34', 'ECGFounder', 'CSFM-Tiny', 'CSFM-Base', 'CSFM-Large']
colors  = ['#484878', '#7884B4', '#B4C0E4', '#E4E4F0', '#E4CCD8', '#F0C0CC']
metrics = ['Metric 1', 'Metric 2', 'Metric 3']
mean = {
    'Metric 1': np.array([0.81, 0.83, 0.86, 0.89, 0.91, 0.92]),
    'Metric 2': np.array([0.63, 0.67, 0.71, 0.74, 0.77, 0.79]),
    'Metric 3': np.array([0.41, 0.45, 0.49, 0.53, 0.56, 0.58]),
}
std  = {k: v * 0.03 for k, v in mean.items()}  # placeholder

# --- Figure ---
fig = plt.figure(figsize=(28, 6))
gs = gridspec.GridSpec(1, len(metrics) + 1)  # +1 for legend panel

handles, labels = None, None
for col, metric in enumerate(metrics):
    ax = fig.add_subplot(gs[col])
    bars = ax.bar(
        range(len(methods)),
        mean[metric],
        yerr=std[metric],
        capsize=5,
        color=colors,
        label=methods,
        error_kw={'elinewidth': 2, 'capthick': 2},
    )
    if col == 0:
        handles, labels = ax.get_legend_handles_labels()
    ax.set_xticks([])
    y_vals = mean[metric]
    margin = (y_vals.max() - y_vals.min()) * 0.15
    ax.set_ylim([y_vals.min() - margin, y_vals.max() + margin])
    ax.set_ylabel(metric, fontsize=32)

# Legend-only panel
ax_leg = fig.add_subplot(gs[-1])
ax_leg.legend(handles, labels, fontsize=28, loc='center', frameon=False)
ax_leg.set_axis_off()

fig.tight_layout(pad=2)
os.makedirs('./figures', exist_ok=True)
fig.savefig('./figures/comparison.png', dpi=300)
fig.savefig('./figures/comparison.svg', bbox_inches='tight')
fig.savefig('./figures/comparison.pdf', bbox_inches='tight')
plt.close(fig)
```

---

## Adaptation points

Replace demonstration data, labels, statistical values, and output paths with the real inputs. Preserve the structure while reconsidering every visual parameter at the declared final size.

## Expected outputs

Follow the required output policy in global-settings.md: export SVG, PDF, and PNG for every figure. Generate TIFF only when the submission venue requires it.

## Checks before delivery

Check data semantics, uncertainty, font rendering, spacing, clipping, legend placement, color accessibility, and final rendered size. Run the Check stage after layout changes.
