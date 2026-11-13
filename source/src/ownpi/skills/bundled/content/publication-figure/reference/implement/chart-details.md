# Python Publication Figure: Chart Detail Tutorial

This tutorial turns the expression task defined in the logical design into a clear, precise, publication-ready panel. It focuses on parameters that determine visual quality: markers, line widths, alpha, error bars, ticks, annotations, colormaps, and scales.

## Contents

1. Style image panels
2. Draw grouped comparison bars
3. Draw trends and uncertainty
4. Draw estimates with intervals
5. Draw interpretable heatmaps
6. Place annotations safely
7. Draw radar and polar charts
8. Draw conceptual 3D panels
9. Draw scatter and bubble plots
10. Draw stacked area trends
11. Draw logarithmic bar charts

---

## 1. Style image panels

```python
def style_dark_image_ax(ax, facecolor='black'):
    """Prepare an axes for dark image or rendering plates."""
    ax.set_facecolor(facecolor)
    ax.set_xticks([])
    ax.set_yticks([])
    for spine in ax.spines.values():
        spine.set_visible(False)
    return ax
```

---

## 2. Draw grouped comparison bars

```python
def make_grouped_bar(ax, categories, series, labels,
                     ylabel='Value', colors=None,
                     annotate=False, bar_width=0.8,
                     series_spread=None, error_kw=None):
    """
    Grouped bar chart.

    Parameters
    ----------
    ax         : matplotlib Axes
    categories : list[str]  — x-axis category names (length K)
    series     : list[array] — one array per group (each length K)
    labels     : list[str]  — legend label per group
    ylabel     : str
    colors     : list[str] | None  — defaults to DEFAULT_COLORS; override with
                                     DEFAULT_COLORS_PUBLICATION_PASTEL for unified-family figures
    annotate   : bool  — print value above each bar
    bar_width  : float — total width for all bars in one category
    error_kw   : dict  — passed to ax.bar as error_kw
    series_spread : list[array] | None — one uncertainty array per series

    Returns
    -------
    list[BarContainer]
    """
    import numpy as np
    if colors is None:
        colors = DEFAULT_COLORS
    if error_kw is None:
        error_kw = {'elinewidth': 2, 'capthick': 2, 'capsize': 10}
    n_groups = len(series)
    n_cats = len(categories)
    w = bar_width / n_groups
    x = np.arange(n_cats)
    flat_values = np.concatenate([np.asarray(values, dtype=float) for values in series])
    label_pad = 0.02 * max(float(np.ptp(flat_values)), float(np.max(np.abs(flat_values))), 1.0)
    containers = []
    for i, (vals, label, color) in enumerate(zip(series, labels, colors)):
        spread = None if series_spread is None else np.asarray(series_spread[i], dtype=float)
        offset = (i - (n_groups - 1) / 2) * w
        bars = ax.bar(x + offset, vals, width=w, label=label,
                      color=color, edgecolor='black', linewidth=1.5,
                      yerr=spread, error_kw=error_kw)
        containers.append(bars)
        if annotate:
            for j, (bar, val) in enumerate(zip(bars, vals)):
                upper = bar.get_height() + (0 if spread is None else spread[j])
                ax.text(bar.get_x() + bar.get_width() / 2,
                        upper + label_pad,
                        f'{val:.2f}', ha='center', va='bottom', fontsize=10)
    ax.set_xticks(x)
    ax.set_xticklabels(categories)
    ax.set_ylabel(ylabel)
    ax.legend()
    return containers
```

---

## 3. Draw trends and uncertainty

```python
def make_trend(ax, x, y_series, labels,
               colors=None, ylabel=None, xlabel=None,
               show_shadow=True, shadow_alpha=0.15,
               lw=2.5, marker='o', markersize=8):
    """
    Multi-line trend plot.

    Parameters
    ----------
    x        : array-like   — shared x values
    y_series : list[array]  — one 1D array per line
    labels   : list[str]
    show_shadow : bool  — fill_between ± std if y_series contains 2D arrays (rows=runs)
    """
    import numpy as np
    if colors is None:
        colors = DEFAULT_COLORS
    for y, label, color in zip(y_series, labels, colors):
        y = np.asarray(y)
        if y.ndim == 2:
            mean, std = y.mean(0), y.std(0)
        else:
            mean, std = y, None
        ax.plot(x, mean, color=color, lw=lw, marker=marker,
                markersize=markersize, label=label)
        if show_shadow and std is not None:
            ax.fill_between(x, mean - std, mean + std,
                            color=color, alpha=shadow_alpha)
    if ylabel:
        ax.set_ylabel(ylabel)
    if xlabel:
        ax.set_xlabel(xlabel)
    ax.legend()
```

---

## 4. Draw estimates with intervals

```python
def make_forest_plot(ax, labels, estimates, ci_low, ci_high,
                     colors=None, ref=0.0, xlabel=None, xlim=None,
                     marker='o', markersize=5, lw=1.5):
    """
    Minimal forest plot helper for publication-grade clinical/statistical panels.
    """
    import numpy as np
    y = np.arange(len(labels))[::-1]
    if colors is None:
        colors = ['#B64342'] * len(labels)
    for yi, est, lo, hi, color in zip(y, estimates, ci_low, ci_high, colors):
        ax.plot([lo, hi], [yi, yi], color=color, lw=lw)
        ax.plot(est, yi, marker=marker, ms=markersize, color=color)
    ax.axvline(ref, color='#767676', linestyle='--', linewidth=1.2, alpha=0.8)
    ax.set_yticks(y)
    ax.set_yticklabels(labels)
    if xlabel:
        ax.set_xlabel(xlabel)
    if xlim is not None:
        ax.set_xlim(xlim)
    ax.spines['right'].set_visible(False)
    ax.spines['top'].set_visible(False)
```

Use pale `ax.axhspan(...)` bands behind contiguous label groups when you need the clinical-triptych layout.

---

## 5. Draw interpretable heatmaps

```python
def make_heatmap(ax, matrix, x_labels=None, y_labels=None,
                 cmap='magma', cbar_label=None, annotate=False,
                 fmt='{:.2f}', fontsize=12):
    """
    2D heatmap with optional colorbar and cell annotations.
    """
    import numpy as np
    import matplotlib as mpl
    im = ax.imshow(matrix, cmap=cmap, aspect='auto')
    if cbar_label:
        cbar = ax.figure.colorbar(im, ax=ax)
        cbar.set_label(cbar_label)
    if x_labels:
        ax.set_xticks(range(len(x_labels)))
        ax.set_xticklabels(x_labels, rotation=30, ha='right', rotation_mode='anchor')
    if y_labels:
        ax.set_yticks(range(len(y_labels)))
        ax.set_yticklabels(y_labels)
    if annotate:
        norm = mpl.colors.Normalize(vmin=matrix.min(), vmax=matrix.max())
        cm_obj = plt.get_cmap(cmap)
        for (i, j), val in np.ndenumerate(matrix):
            r, g, b, _ = cm_obj(norm(val))
            lum = 0.299*r + 0.587*g + 0.114*b
            color = 'white' if lum < 0.5 else 'black'
            ax.text(j, i, fmt.format(val), ha='center', va='center',
                    fontsize=fontsize, color=color)
    ax.set_frame_on(False)
```

---

## 6. Place annotations safely

Annotate only what needs a direct visual pointer, using a short phrase, value, or symbol; move full explanations to the caption.

Load the tested helpers from the bundled `scripts/figure_safety.py` rather than calling `np.interp` directly on a curve whose direction has not been asserted:

```python
from figure_safety import interp_monotone, label_y_above

n_equivalent = interp_monotone(target_error, error_curve, example_counts)
label_y = label_y_above(metric_center, metric_spread)
```

`interp_monotone` accepts strictly increasing or decreasing `xp`, reverses a decreasing grid and its paired values together, and rejects duplicates/direction changes. `label_y_above` places a shared annotation above the upper uncertainty extent rather than relying on a fixed `LABEL_Y`.

For compact mathematical labels, prefer supported Unicode glyphs such as `R²` over `$R^2$` when the meaning is unchanged. Then run the bundled `scripts/audit_pdf_text.py` with a 5 pt minimum and `scripts/audit_figure_collisions.py` on the exported PDF, preserving their JSON and optional diagnostic outputs.

---

---

### 6.1. Reduce categorical label crowding

When methods are named in the legend, hide x-ticks entirely.

```python
ax.set_xticks([])        # removes ticks and labels
# Alternatively:
ax.set_xticklabels([])   # keeps tick marks, removes labels
```

---

### 6.2. Set data-dependent axes

Never use 0–100 when all values are in 80–95.

```python
margin = (values.max() - values.min()) * 0.1   # 10% padding
ax.set_ylim([values.min() - margin, values.max() + margin])

# Manual ticks at clean round numbers
ax.set_yticks([0.75, 0.80, 0.85, 0.90])
ax.tick_params(axis='y', labelsize=36, length=10, width=2)
```

---

### 6.3. Encode ablations with alpha

```python
import numpy as np

blue_rgb = (0.215686, 0.458824, 0.729412)   # #3775BA as float tuple
n_ablations = len(ablation_configs)
alphas = np.linspace(0.2, 1.0, n_ablations)
colors = [(blue_rgb[0], blue_rgb[1], blue_rgb[2], a) for a in alphas]
# Full method → alpha=1.0, most ablated → alpha=0.2
```

---

### 6.4. Add grayscale-safe hatching

Add hatching so bars remain distinct when printed in black-and-white.

```python
hatches = ['/', '\\\\', '.', 'x', 'o', '+']
for bar_container, hatch in zip(grouped_bars, hatches):
    for patch in bar_container:
        patch.set_hatch(hatch)
        patch.set_edgecolor('black')
        patch.set_linewidth(1.5)
```

---

### 6.5. Add readable values

```python
def annotate_bars(ax, bars, colors, fmt='{:.2f}', fontsize=32, offset=-0.10):
    for bar, color in zip(bars, colors):
        c = color.lstrip('#')
        r, g, b = int(c[0:2],16)/255, int(c[2:4],16)/255, int(c[4:6],16)/255
        lum = 0.299*r + 0.587*g + 0.114*b
        textcolor = 'white' if lum < 0.5 else 'black'
        value = bar.get_height()
        ax.text(bar.get_x() + bar.get_width()/2,
                value + offset,
                fmt.format(value),
                ha='center', va='bottom',
                fontsize=fontsize, color=textcolor)
```

---

### 6.6. Render filled trends

```python
ax.fill_between(x, 0, cumsum_series,
                color=fill_color,
                hatch='\\\\\\',   # triple backslash for dense hatch
                edgecolor='black',
                label=label_name)
# Visually erase the border artifacts:
ax.fill_between(x, 0, cumsum_series,
                facecolor='none',
                edgecolor='white',
                linewidth=2)
```

---

### 6.7. Annotate meaningful events

```python
def mark_events(ax, x_labels, y_cumsum, events_dict, dy_fraction=0.1):
    """Add labeled arrows at event dates on a trend line."""
    x_index = {label: i for i, label in enumerate(x_labels)}
    y_lo, y_hi = ax.get_ylim()
    dy = dy_fraction * (y_hi - y_lo)
    for date, label in events_dict.items():
        if date not in x_index:
            continue
        i = x_index[date]
        stars = label.count('*')
        clean_label = label.replace('*', '')
        y_data = y_cumsum[i]
        ax.annotate(
            clean_label,
            xy=(i, y_data),
            xytext=(i, y_data + (1 + 0.8 * stars) * dy),
            ha='center', va='bottom', fontsize=11,
            arrowprops=dict(arrowstyle='-|>', lw=1.3, color='black',
                            shrinkA=0, shrinkB=0, mutation_scale=15)
        )
```

---

### 6.8. Group nested comparisons

```python
num_methods = len(methods)
xtick_positions = []

for dataset_idx, dataset_name in enumerate(datasets):
    x_start = dataset_idx * (num_methods + 1)   # gap of 1 between groups
    ax.bar(
        np.arange(num_methods) + x_start,
        values[dataset_name],
        color=method_colors,
        label=methods if dataset_idx == 0 else ['_nolegend_'] * num_methods,
    )
    xtick_positions.append(np.mean(np.arange(num_methods)) + x_start)

ax.set_xticks(xtick_positions)
ax.set_xticklabels(datasets)
```

---

### 6.9. Label stable regions directly

Use when the same categorical structure repeats and a legend would become too large.

```python
for x_text, y_text, text, color in label_specs:
    ax.text(
        x_text, y_text, text,
        color=color,
        ha='center', va='center',
        fontsize=9, fontweight='bold',
    )
```

Rules:

- Keep labels inside stable, visually large regions.
- Use a small white or black stroke if the fill varies strongly underneath.
- Prefer direct labels over a mega-legend for repeated stacked-area or phase diagrams.

---

---

## 7. Draw radar and polar charts

Used when comparing multiple methods across many benchmarks simultaneously.

```python
import numpy as np
import matplotlib.pyplot as plt

def plot_radar(methods, colors, subtask_names, value_matrix,
               benchmark_radii, display_range=(45, 90)):
    """
    Parameters
    ----------
    methods        : list[str]    — one curve per method
    colors         : list[str]
    subtask_names  : list[str]    — one spoke per subtask (may contain '\\n')
    value_matrix   : np.ndarray  — shape (n_subtasks, n_methods)
    benchmark_radii: dict         — {benchmark_name: [tick1, tick2, ...]} for normalization
    display_range  : (r_min, r_max) — polar radial display window
    """
    r_lo, r_hi = display_range
    n_subtasks = len(subtask_names)
    n_methods  = len(methods)

    fig = plt.figure(figsize=(12, 10))
    ax  = fig.add_subplot(111, projection='polar')

    # Evenly spaced angles, clockwise from top
    angles = np.linspace(2 * np.pi, 0, n_subtasks, endpoint=False)
    angles_closed = np.append(angles, angles[0])

    def _normalize(val, bench):
        radii_list = benchmark_radii.get(bench, [0, 100])
        span = max(radii_list) - min(radii_list)
        if span <= 0:
            return (r_lo + r_hi) / 2
        frac = np.clip((val - min(radii_list)) / span, 0, 1)
        return r_lo + (r_hi - r_lo) * frac

    subtask_benchmarks = [s.split('\\n', 1)[-1] if '\\n' in s else s
                          for s in subtask_names]

    # Draw data polygons
    for m in range(n_methods):
        norm_vals = np.array([_normalize(value_matrix[i, m], subtask_benchmarks[i])
                              for i in range(n_subtasks)])
        closed = np.append(norm_vals, norm_vals[0])
        ax.plot(angles_closed, closed, color=colors[m], lw=2, label=methods[m])
        ax.fill(angles_closed, closed, color=colors[m], alpha=0.05)
        ax.scatter(angles, norm_vals, color=colors[m], s=18, zorder=5)

    # Style
    ax.set_ylim(r_lo, r_hi)
    ax.set_theta_zero_location('N')
    for spine in ax.spines.values():
        spine.set_visible(False)
    ax.grid(False)

    # Outer boundary ring
    ax.plot(angles_closed, np.full_like(angles_closed, r_hi),
            color='k', lw=0.8, zorder=4)

    # Radial spokes
    for a in angles:
        ax.plot([a, a], [r_lo, r_hi], color='gray', lw=0.5, zorder=4)

    # Benchmark-level contour polygons
    max_levels = max(len(v) for v in benchmark_radii.values())
    for k in range(max_levels):
        disp = np.array([_normalize(benchmark_radii.get(b, [0,100])[
                            min(k, len(benchmark_radii.get(b,[0,100]))-1)], b)
                         for b in subtask_benchmarks])
        ax.plot(angles_closed, np.append(disp, disp[0]),
                color='k', lw=0.6, zorder=4)

    ax.set_yticks([r_hi])
    ax.set_yticklabels([])
    ax.set_xticks(angles)
    ax.set_xticklabels([])

    # Spoke labels (outside outer ring)
    for angle, label in zip(angles, subtask_names):
        r_label = r_hi + 8 + 10 * abs(np.sin(angle))
        ax.text(angle, r_label, label, fontsize=14,
                ha='center', va='center',
                transform=ax.transData, clip_on=False)

    ax.legend(loc='upper right', bbox_to_anchor=(1.40, 0.05),
              fontsize=15, frameon=False)
    return fig, ax
```

**Key settings:**
- `ax.set_theta_zero_location('N')` — top-start convention
- Remove all default spines/grid; draw custom spokes + contour polygons manually
- Normalize each spoke independently using per-benchmark tick lists
- Legend placed **outside** the plot at `bbox_to_anchor=(1.40, 0.05)`

---

## 8. Draw conceptual 3D panels

Used for geometric conceptual diagrams (e.g., embedding space visualization).

```python
import numpy as np
import matplotlib.pyplot as plt

def draw_shaded_sphere(ax, light_dir=(-0.5, 0.5, 0.8),
                       resolution=512, alpha=1.0,
                       extent=(-1, 1, -1, 1)):
    """Draw a 2D shaded disk that mimics a 3D sphere using ray-casting."""
    xs = np.linspace(extent[0], extent[1], resolution)
    ys = np.linspace(extent[2], extent[3], resolution)
    x, y = np.meshgrid(xs, ys)
    r2 = x**2 + y**2
    mask = r2 <= 1.0

    z = np.zeros_like(x)
    z[mask] = np.sqrt(1.0 - r2[mask])

    # Surface normals
    nx, ny, nz = x.copy(), y.copy(), z.copy()
    nrm = np.sqrt(nx**2 + ny**2 + nz**2) + 1e-6
    nx, ny, nz = nx/nrm, ny/nrm, nz/nrm

    # Lambertian shading
    ld = np.array(light_dir, dtype=float)
    ld /= np.linalg.norm(ld)
    intensity = np.maximum(0, nx*ld[0] + ny*ld[1] + nz*ld[2])

    img = np.ones_like(x)
    img[mask] = np.clip(0.2 + 0.9 * intensity[mask], 0, 1)

    ax.imshow(img, cmap='gray',
              extent=list(extent),
              vmin=0, vmax=1, alpha=alpha)
    ax.set_axis_off()
    return ax


def plot_3d_scatter_with_arrows(ax, points, grad_vectors,
                                point_color='#0c2458', arrow_color='#b64342'):
    """3D scatter plot with gradient arrow annotations."""
    from mpl_toolkits.mplot3d import proj3d
    from matplotlib.patches import FancyArrowPatch

    class Arrow3D(FancyArrowPatch):
        def __init__(self, xs, ys, zs, *args, **kwargs):
            super().__init__((0,0), (0,0), *args, **kwargs)
            self._verts3d = xs, ys, zs
        def do_3d_projection(self, renderer=None):
            xs, ys, zs = proj3d.proj_transform(*self._verts3d, self.axes.get_proj())
            self.set_positions((xs[0], ys[0]), (xs[1], ys[1]))
            return np.min(zs)

    ax.scatter(points[:, 0], points[:, 1], points[:, 2],
               s=80, color=point_color, alpha=0.5)
    for p, g in zip(points, grad_vectors):
        arrow = Arrow3D([p[0], p[0]+g[0]], [p[1], p[1]+g[1]], [p[2], p[2]+g[2]],
                        mutation_scale=16, lw=4, arrowstyle='->',
                        color=arrow_color, alpha=0.8)
        ax.add_artist(arrow)

    # Clean 3D axes
    ax.grid(False)
    ax.xaxis.pane.set_visible(False)
    ax.yaxis.pane.set_visible(False)
    ax.zaxis.pane.set_visible(False)
    ax.set_xticks([])
    ax.set_yticks([])
    ax.set_zticks([])
```

---

## 9. Draw scatter and bubble plots

```python
def make_scatter(ax, x, y, labels_or_colors,
                 size=50, alpha=0.7, edgecolors='none'):
    """Single or multi-cluster scatter."""
    import numpy as np
    ax.scatter(x, y, c=labels_or_colors, s=size,
               alpha=alpha, edgecolors=edgecolors)
    ax.set_axis_off()   # for conceptual diagrams; remove for data plots
```

---

## 10. Draw stacked area trends

Used for cumulative publication counts, stacked contributions, etc.

```python
# Filled area (stacked) with hatch for print safety
ax.fill_between(x, 0, y_bottom,
                color='#ffa8a6', label='Category A')
ax.fill_between(x, 0, y_top,
                color='#9BC8FA',
                hatch='///',               # hatch for grayscale print
                edgecolor='black',
                label='Category B')
# Erase border artifacts
ax.fill_between(x, 0, y_top,
                facecolor='none',
                edgecolor='white',
                linewidth=2)

# Overlay the trend line for exact values
ax.plot(x, y_top, lw=3, color='#13457E')
ax.plot(x, y_bottom, lw=3, color='#850c0a')
```

---

## 11. Draw logarithmic bar charts

```python
ax.set_yscale('log')
ymin, ymax = ax.get_ylim()
ax.set_ylim(ymin, ymax * 20)   # expand top for annotations

# Annotate values above bars
for i, val in enumerate(values):
    ax.text(i, val * 1.1, f'{val:.3f}',
            ha='center', va='bottom', fontsize=16)
```

---
