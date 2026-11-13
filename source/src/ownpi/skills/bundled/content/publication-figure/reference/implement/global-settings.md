# Python Publication Figure: Global Style Tutorial

This tutorial establishes the shared visual system before any panel is created. Define the final physical size first, then configure typography, editable text, axes, colors, and export parameters. Every panel must inherit these decisions.

## Contents

1. Final physical size and scale
2. Typography and editable text
3. Axes and visual weight
4. Semantic color system
5. Reusable global style
6. Global setup checklist

---

## 1. Define reusable style constants

### PALETTE

```python
PALETTE = {
    "blue_main":      "#0F4D92",
    "blue_secondary": "#3775BA",
    "green_1": "#DDF3DE",
    "green_2": "#AADCA9",
    "green_3": "#8BCF8B",
    "red_1":   "#F6CFCB",
    "red_2":   "#E9A6A1",
    "red_strong": "#B64342",
    "neutral_light": "#CFCECE",
    "neutral_mid":   "#767676",
    "neutral_dark":  "#4D4D4D",
    "neutral_black": "#272727",
    "gold":   "#FFD700",
    "teal":   "#42949E",
    "violet": "#9A4D8E",
    "magenta":"#EA84DD",
}

DEFAULT_COLORS = [
    PALETTE["blue_main"],
    PALETTE["green_3"],
    PALETTE["red_strong"],
    PALETTE["teal"],
    PALETTE["violet"],
    PALETTE["neutral_light"],
]

PALETTE_PUBLICATION_PASTEL = {
    "baseline_dark": "#484878",
    "baseline_mid":  "#7884B4",
    "baseline_soft": "#B4C0E4",
    "focus_soft":   "#E4E4F0",
    "focus_mid":    "#E4CCD8",
    "focus_strong": "#F0C0CC",
    "bg_lilac": "#E0E0F0",
    "bg_aqua":  "#E0F0F0",
    "bg_peach": "#F0E0D0",
    "neutral_light": "#D8D8D8",
    "neutral_mid":   "#A8A8A8",
    "neutral_dark":  "#606060",
    "delta_up":   "#2E9E44",
    "delta_down": "#E53935",
}

DEFAULT_COLORS_PUBLICATION_PASTEL = [
    PALETTE_PUBLICATION_PASTEL["baseline_dark"],
    PALETTE_PUBLICATION_PASTEL["baseline_mid"],
    PALETTE_PUBLICATION_PASTEL["baseline_soft"],
    PALETTE_PUBLICATION_PASTEL["focus_soft"],
    PALETTE_PUBLICATION_PASTEL["focus_mid"],
    PALETTE_PUBLICATION_PASTEL["focus_strong"],
]

PALETTE_IMAGING = {
    "bg": "#000000",
    "context": "#B8B8B8",
    "cyan": "#22D7E6",
    "magenta": "#FF2AD4",
    "white": "#FFFFFF",
}

PALETTE_MATERIAL = {
    "aqua": "#77D7D1",
    "teal": "#33B5A5",
    "lilac": "#B9A7E8",
    "violet": "#7C6CCF",
    "callout_red": "#E53935",
    "neutral": "#D9D9D9",
}

PALETTE_CLINICAL = {
    "baseline": "#272727",
    "week6": "#E28E2C",
    "week13": "#D24B40",
    "week26": "#5B8FD6",
    "year1": "#7BAA5B",
    "year2": "#C45AD6",
    "group_band": "#F2E6D9",
}

PALETTE_GENOMICS = {
    "neutral_light": "#D8D8D8",
    "neutral_mid": "#8F8F8F",
    "wave1": "#D9544D",
    "wave2": "#5B7FCA",
    "wave3": "#B89BD9",
    "outline": "#4D4D4D",
}
```

Use `DEFAULT_COLORS` when color itself carries explicit semantic meaning (`hero`, `baseline`, `positive variant`). Use `DEFAULT_COLORS_PUBLICATION_PASTEL` when several compared methods belong to one or two related families and the page should feel visually unified.

---

## 2. Configure editable typography

These three lines are **non-negotiable** and must appear at the top of every script, before any figure is created. They guarantee editable text in SVG output:

```python
plt.rcParams['font.family'] = 'sans-serif'
plt.rcParams['font.sans-serif'] = ['Arial', 'DejaVu Sans', 'Liberation Sans']
plt.rcParams['svg.fonttype'] = 'none'   # keeps text as <text> nodes, not paths
```

**Why `svg.fonttype = 'none'`**: matplotlib's default (`'path'`) converts every glyph to a bezier path, making text unselectable, unsearchable, and impossible to re-align in Illustrator / Inkscape. With `'none'`, text stays as SVG `<text>` elements and font substitution happens at render time.


---

## 3. Apply the global style function

```python
def apply_publication_style(font_size=16, axes_linewidth=2.5, use_tex=False):
    """Apply publication-grade rcParams. Call once before creating any figures."""
    # ── MANDATORY: editable SVG text ──────────────────────────────────────────
    plt.rcParams['font.family'] = 'sans-serif'
    plt.rcParams['font.sans-serif'] = ['Arial', 'DejaVu Sans', 'Liberation Sans']
    plt.rcParams['svg.fonttype'] = 'none'
    # ── Layout & style ────────────────────────────────────────────────────────
    plt.rcParams['font.size'] = font_size
    plt.rcParams['axes.spines.right'] = False
    plt.rcParams['axes.spines.top'] = False
    plt.rcParams['axes.linewidth'] = axes_linewidth
    plt.rcParams['legend.frameon'] = False
    if use_tex:
        plt.rcParams['text.usetex'] = True
```

**Presets:**
- Large bar panels: `apply_publication_style(font_size=24, axes_linewidth=3)`
- Compact figures: `apply_publication_style(font_size=15, axes_linewidth=2)`
- Dense journal-width multi-panels: `apply_publication_style(font_size=8, axes_linewidth=1)`
- LaTeX labels: `apply_publication_style(use_tex=True)`

---

## 4. Select annotation text by background luminance

```python
def is_dark(hex_color, threshold=128):
    """Return True if hex color is dark (use white text on it)."""
    c = hex_color.lstrip('#')
    r, g, b = int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16)
    return (0.299*r + 0.587*g + 0.114*b) < threshold
```

---

---

### Semantic and family color mapping

Always map colors consistently across all panels in a figure:

```python
method_colors = {
    'ResNet1d18': '#484878',   # baseline_dark
    'ResNet1d34': '#7884B4',   # baseline_mid
    'ECGFounder': '#B4C0E4',   # baseline_soft
    'CSFM-Tiny':  '#E4E4F0',   # ours_tiny
    'CSFM-Base':  '#E4CCD8',   # ours_base
    'CSFM-Large': '#F0C0CC',   # ours_large
}
colors = [method_colors[m] for m in methods]
```

Prefer coherent hue families over alternating saturated blue/green/red just because categories differ. Green and red should usually be reserved for **directional annotations**, not primary series identity:

```python
ax.scatter(x_gain, y_gain, marker='^', color='#2E9E44', s=90, zorder=6)  # improvement
ax.scatter(x_drop, y_drop, marker='v', color='#E53935', s=90, zorder=6)  # degradation
```

---

---

## 5. Format scientific axes

```python
ax.ticklabel_format(axis='y', style='sci', scilimits=(0, 0))
```

---

## 6. Position custom spines

```python
# Move bottom spine to y=0 (for negative values)
ax.spines['bottom'].set_position(('data', 0))
ax.xaxis.set_ticks_position('bottom')
ax.spines['left'].set_bounds(0, y_max)
```

---

---

## 7. Use the Python drawing environment

**Python-only execution rule.** When Python is the selected track, do all figure drawing, previewing, exporting, and visual QA in Python. Do not call R/ggplot2, ComplexHeatmap, patchwork, or any R graphics device to create a temporary preview, fallback export, or layout approximation.

### Minimal Python setup

```python
import matplotlib as mpl
import matplotlib.pyplot as plt

# Load require_matplotlib_panel_alignment from the bundled
# scripts/audit_panel_alignment.py before using this helper.

mpl.rcParams.update({
    "font.family": "sans-serif",
    "font.sans-serif": ["Arial", "Helvetica", "DejaVu Sans", "sans-serif"],
    "svg.fonttype": "none",     # editable text in SVG
    "pdf.fonttype": 42,         # editable TrueType text in PDF
    "font.size": 7,             # use 15-24 only for large slide-sized panels
    "axes.spines.right": False,
    "axes.spines.top": False,
    "axes.linewidth": 0.8,
    "legend.frameon": False,
})

def save_pub_py(fig, filename, dpi=600, require_tiff=False):
    # Run after every layout-affecting change, before export. Single-panel
    # figures record NOT APPLICABLE; unresolved multi-panel geometry blocks.
    require_matplotlib_panel_alignment(
        fig,
        json_out=f"{filename}.alignment.json",
        overlay_svg=f"{filename}.alignment.svg",
        tolerance_pt=1.5,
        gutter_tolerance_pt=1.5,
        strict=True,
    )
    fig.savefig(f"{filename}.svg", bbox_inches="tight")
    fig.savefig(f"{filename}.pdf", bbox_inches="tight")
    fig.savefig(f"{filename}.png", dpi=dpi, bbox_inches="tight")
    if require_tiff:
        fig.savefig(f"{filename}.tiff", dpi=dpi, bbox_inches="tight")
```

Use `text.usetex = True` only when LaTeX is installed and math-rich labels are required.

## 8. Required output files

Always export SVG as the editable primary output and PDF as the required rendered output. Use the PDF as the canonical input for visual QA, manuscript placement, collision inspection, clipping inspection, and rendered typography checks.

Also export PNG as a fast visual preview. Export TIFF only when the submission venue explicitly requires raster input. PNG and TIFF are secondary outputs and must never be the only files when the figure contains text that may need adjustment.

Export all required files at the declared final physical size. Use 300–600 dpi for PNG or TIFF when raster output is required. Keep the SVG, PDF, preview image, source script, input reference, and QA reports under one figure output prefix. Run code checks before export, then run rendered visual checks on the PDF and preview image.
