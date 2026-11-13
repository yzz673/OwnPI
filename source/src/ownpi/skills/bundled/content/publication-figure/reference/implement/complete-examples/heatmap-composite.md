# Python Publication Figure: Heatmap with dual colormaps (positive/negative columns)

## Purpose

This complete example demonstrates heatmap with dual colormaps (positive/negative columns) from style setup through export.

## When to use

Use this example when the panel data and scientific question match its stated chart structure.

## Design assumptions

Follow the logical and aesthetic design artifacts first. Treat the dimensions, palette, labels, and uncertainty encoding below as explicit implementation decisions, then adapt them to the final physical size.

## Complete Python script

**Goal**: Score matrix where positive = Reds, negative = Blues_r. Cell text auto-contrasted.

```python
import os
import numpy as np
import matplotlib as mpl
import matplotlib.pyplot as plt

plt.rcParams['font.family'] = 'sans-serif'
plt.rcParams['font.sans-serif'] = ['Arial']
plt.rcParams['svg.fonttype'] = 'none'
plt.rcParams['font.size'] = 16
plt.rcParams['axes.spines.right'] = False
plt.rcParams['axes.spines.top'] = False
plt.rcParams['axes.linewidth'] = 2

# matrix: rows = methods, cols = metrics (alternating positive/negative directions)
methods = ['Method A', 'Method B', 'Method C', 'Method D']
metrics = ['Score (+)', 'Error (-)', 'F1 (+)', 'Loss (-)']
matrix  = np.array([
    [0.88,  0.12,  0.85,  0.20],
    [0.81,  0.18,  0.78,  0.28],
    [0.75,  0.25,  0.72,  0.35],
    [0.70,  0.30,  0.68,  0.40],
])

fig, ax = plt.subplots(figsize=(10, 6))
n_rows, n_cols = matrix.shape
vmin, vmax = matrix.min(0), matrix.max(0)

for j in range(n_cols):
    is_positive = (j % 2 == 0)
    cmap = plt.cm.Reds if is_positive else plt.cm.Blues_r
    cmap = cmap.copy()
    norm = mpl.colors.Normalize(
        vmin=0 if is_positive else vmax[j],
        vmax=vmax[j] if is_positive else 0
    )
    ax.imshow(matrix[:, j:j+1], cmap=cmap, norm=norm,
              aspect='auto', extent=[j-0.5, j+0.5, 0, n_rows], origin='lower')

for (i, j), val in np.ndenumerate(matrix):
    is_positive = (j % 2 == 0)
    cmap = plt.cm.Reds if is_positive else plt.cm.Blues_r
    norm = mpl.colors.Normalize(vmin=0 if is_positive else vmax[j],
                                 vmax=vmax[j] if is_positive else 0)
    r, g, b, _ = cmap(norm(val))
    lum = 0.299*r + 0.587*g + 0.114*b
    color = 'white' if lum < 0.5 else 'black'
    ax.text(j, i + 0.5, f'{val:.2f}', ha='center', va='center',
            fontsize=13, color=color)

ax.set_xlim(-0.5, n_cols - 0.5)
ax.set_xticks(np.arange(n_cols))
ax.set_xticklabels(metrics, rotation=30, ha='right', fontsize=14)
ax.tick_params(axis='x', bottom=False, top=False, length=0)
ax.set_yticks(np.arange(n_rows) + 0.5)
ax.set_yticklabels(methods, fontsize=14)
ax.set_frame_on(False)
ax.invert_yaxis()

fig.tight_layout(pad=2)
os.makedirs('./figures', exist_ok=True)
fig.savefig('./figures/heatmap.png', dpi=300)
fig.savefig('./figures/heatmap.svg', bbox_inches='tight')
fig.savefig('./figures/heatmap.pdf', bbox_inches='tight')
plt.close(fig)
```

---

## Adaptation points

Replace demonstration data, labels, statistical values, and output paths with the real inputs. Preserve the structure while reconsidering every visual parameter at the declared final size.

## Expected outputs

Follow the required output policy in global-settings.md: export SVG, PDF, and PNG for every figure. Generate TIFF only when the submission venue requires it.

## Checks before delivery

Check data semantics, uncertainty, font rendering, spacing, clipping, legend placement, color accessibility, and final rendered size. Run the Check stage after layout changes.
