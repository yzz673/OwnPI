# Python Publication Figure: Ablation bar chart (alpha-graduated, horizontal)

## Purpose

This complete example demonstrates ablation bar chart (alpha-graduated, horizontal) from style setup through export.

## When to use

Use this example when the panel data and scientific question match its stated chart structure.

## Design assumptions

Follow the logical and aesthetic design artifacts first. Treat the dimensions, palette, labels, and uncertainty encoding below as explicit implementation decisions, then adapt them to the final physical size.

## Complete Python script

**Goal**: Same method with components progressively added; alpha encodes completeness.

```python
import os
import numpy as np
import matplotlib.pyplot as plt

plt.rcParams['font.family'] = 'sans-serif'
plt.rcParams['font.sans-serif'] = ['Arial']
plt.rcParams['svg.fonttype'] = 'none'
plt.rcParams['font.size'] = 24
plt.rcParams['axes.spines.right'] = False
plt.rcParams['axes.spines.top'] = False
plt.rcParams['axes.linewidth'] = 3

configs = ['None', '+ Module A', '+ Module B', '+ Module C', 'Full']
values  = np.array([0.72, 0.78, 0.81, 0.84, 0.88])
stds    = np.array([0.02, 0.02, 0.01, 0.01, 0.01])

n = len(configs)
blue_rgb = (0.215686, 0.458824, 0.729412)   # #3775BA
alphas = np.linspace(0.2, 1.0, n)
colors = [(blue_rgb[0], blue_rgb[1], blue_rgb[2], a) for a in alphas]

fig, ax = plt.subplots(figsize=(12, 6))
ax.barh(range(n), values, xerr=stds,
        color=colors, ecolor='k', capsize=5)
ax.set_yticks(range(n))
ax.set_yticklabels(configs)
ax.set_xlim([values.min() - 0.05, values.max() + 0.03])
ax.set_xlabel('Score', fontsize=32)

fig.tight_layout(pad=2)
os.makedirs('./figures', exist_ok=True)
fig.savefig('./figures/ablation.png', dpi=300)
fig.savefig('./figures/ablation.svg', bbox_inches='tight')
fig.savefig('./figures/ablation.pdf', bbox_inches='tight')
plt.close(fig)
```

---

## Adaptation points

Replace demonstration data, labels, statistical values, and output paths with the real inputs. Preserve the structure while reconsidering every visual parameter at the declared final size.

## Expected outputs

Follow the required output policy in global-settings.md: export SVG, PDF, and PNG for every figure. Generate TIFF only when the submission venue requires it.

## Checks before delivery

Check data semantics, uncertainty, font rendering, spacing, clipping, legend placement, color accessibility, and final rendered size. Run the Check stage after layout changes.
