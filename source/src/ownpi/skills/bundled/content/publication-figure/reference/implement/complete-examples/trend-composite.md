# Python Publication Figure: Multi-panel trend with shared legend

## Purpose

This complete example demonstrates multi-panel trend with shared legend from style setup through export.

## When to use

Use this example when the panel data and scientific question match its stated chart structure.

## Design assumptions

Follow the logical and aesthetic design artifacts first. Treat the dimensions, palette, labels, and uncertainty encoding below as explicit implementation decisions, then adapt them to the final physical size.

## Complete Python script

**Goal**: Two trend panels (e.g., train/val curves) and a legend-only third panel.

```python
import os
import numpy as np
import matplotlib.pyplot as plt

plt.rcParams['font.family'] = 'sans-serif'
plt.rcParams['font.sans-serif'] = ['Arial']
plt.rcParams['svg.fonttype'] = 'none'
plt.rcParams['font.size'] = 15
plt.rcParams['axes.spines.right'] = False
plt.rcParams['axes.spines.top'] = False
plt.rcParams['axes.linewidth'] = 2

methods = ['Baseline', 'CSFM-Tiny', 'CSFM-Base', 'CSFM-Large']
colors  = ['#7884B4', '#E4E4F0', '#E4CCD8', '#F0C0CC']
x = np.arange(0, 100, 5)

fig, axes = plt.subplots(1, 3, figsize=(18, 5))

for panel_idx, (ax, panel_name) in enumerate(zip(axes[:2], ['Training', 'Validation'])):
    for method, color in zip(methods, colors):
        y = 0.48 + 0.42 * (1 - np.exp(-x / 30)) + np.random.randn(len(x)) * 0.01
        if method == 'Baseline':
            y -= 0.03
        elif method == 'CSFM-Tiny':
            y += 0.00
        elif method == 'CSFM-Base':
            y += 0.02
        elif method == 'CSFM-Large':
            y += 0.03
        ax.plot(x, y, color=color, lw=2.5, marker='o', markersize=6, label=method)
    ax.set_title(panel_name, fontsize=18)
    ax.set_xlabel('Epoch', fontsize=16)
    ax.set_ylabel('Loss', fontsize=16)
    if panel_idx == 0:
        handles, labels = ax.get_legend_handles_labels()

# Legend-only panel
axes[2].legend(handles, labels, fontsize=14, loc='center', frameon=False)
axes[2].set_axis_off()

fig.tight_layout(pad=2)
os.makedirs('./figures', exist_ok=True)
fig.savefig('./figures/trends.png', dpi=300)
fig.savefig('./figures/trends.svg', bbox_inches='tight')
fig.savefig('./figures/trends.pdf', bbox_inches='tight')
plt.close(fig)
```

---

## Adaptation points

Replace demonstration data, labels, statistical values, and output paths with the real inputs. Preserve the structure while reconsidering every visual parameter at the declared final size.

## Expected outputs

Follow the required output policy in global-settings.md: export SVG, PDF, and PNG for every figure. Generate TIFF only when the submission venue requires it.

## Checks before delivery

Check data semantics, uncertainty, font rendering, spacing, clipping, legend placement, color accessibility, and final rendered size. Run the Check stage after layout changes.
