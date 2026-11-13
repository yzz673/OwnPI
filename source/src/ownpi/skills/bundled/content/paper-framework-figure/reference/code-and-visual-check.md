# Code and Visual Check

Read all content below carefully and follow every instruction strictly. Ask yourself each question against the current figure, not your intended design. A figure passes only when its implementation, scientific meaning, and visual explanation all hold up.

## How to Use the Questions

First inspect the PPTX programmatically, then export and render the current version and view the actual preview. Inspect the whole composition, detailed regions, and the figure at its intended manuscript size. Code checks cannot establish visual correctness; an attractive rendering cannot establish scientific correctness.

For each question, decide **Pass**, **Fail**, or **Not checked**, using the criteria below. Use **Not applicable** only when the feature is absent and unnecessary, with a brief reason. A criterion that is not met is a failure, even if the rest of the figure looks good. Do not answer from memory or give blanket approval without examining the relevant structure. Ground judgments in coordinates, source references, or visible regions as appropriate; a brief note is enough, and no separate checklist file is required.

## 1. Is the PPTX Structurally Sound? (Code Check)

- **Can I reopen the generated PPTX and find the intended slides, components, and connections?** Pass when the file parses, its contents match the design outline, and the rendering application opens it without repair. Missing elements, broken image relationships, or repair prompts fail. Successful parsing alone does not prove rendering compatibility.
- **Are all visible elements within the intended canvas, with valid dimensions?** Pass when bounds, group transforms, and rotations have been accounted for. Unintended off-canvas objects or invalid geometry fail; do not treat group-local coordinates as slide coordinates.
- **Have I checked alignment, gaps, and overlap according to element roles?** Pass when comparable elements share deliberate anchors and spacing, and there are no unintended collisions. Text inside a module, a background enclosing children, and arrowheads touching a boundary are intentional overlaps; text crossing a border or a connector crossing a label is not.
- **Are the intended components editable and their assets intact?** Pass when the main diagram remains native shapes, text, and connectors, with embedded assets available. Flattening the entire diagram or losing required assets fails. An intentionally embedded icon need not be internally editable, but must not be described as native editable geometry.
- **Have I checked text settings and connector endpoints for likely problems?** Pass when sizes, margins, anchors, directions, and repeated styles are deliberate and consistent. Automatic shrinking that hides overloaded content, detached endpoints, or reversed arrowheads fail. Confirm actual wrapping and arrow appearance visually below.

## 2. Is the Scientific Meaning Correct and Complete?

- **Does the figure express the essential information identified in the design outline?** Pass when each necessary object, operation, and relationship has a visible representation. A missing central mechanism fails even if all displayed labels are correct. Implementation details deliberately assigned to the caption need not become nodes.
- **Can I justify the depicted relationships from the source or manuscript?** Pass when sequence, containment, feedback, grouping, aggregation units, and multiplicities match the evidence. Invented dependencies, misleading counts, or a schematic example presented as a measured result fail. Check each branch and connection, not just the terminology.
- **Is every expansion or repeated structure scoped correctly?** Pass when the reader can tell what is expanded, whether it represents one instance or a repeated process, and what aggregates into what. Compare Example Figure 2: one subcategory and one grid cell each lead to a scoped distillation region. An expansion that appears to describe the whole system when it applies per group fails.
- **Do arrow directions and visual conventions preserve the intended meaning?** Pass when entrances, exits, endpoints, and the meanings of solid, dashed, or colored paths are identifiable and consistent. An ambiguous entry, a backwards dependency, or confusing a detail-expansion leader with data flow fails. Different operations can share a line style when labels make their roles clear.

## 3. Would a Reader Understand the Figure?

Temporarily set aside your knowledge of the implementation. Inspect the preview as a reader who knows the field but has not read the source code. Try to describe what the figure says from its visible structure, then compare that reading with the intended explanation. Read the caption afterward to confirm that it supplies context rather than repairs missing visual relationships.

- **Can I identify the main point and where to begin?** Pass when the entry and dominant reading path are evident. If the reader must search for an entry or guess which cluster is central, revise the hierarchy or layout.
- **Can I follow each important relationship through the geometry?** Pass when position, containment, arrows, or grouping communicate the relationship, with text clarifying it. Compare Example Figure 1: curved arrows show the loop and containment assigns subagents to parents. A paragraph claiming a loop without a visible return path fails.
- **Are the main structure and supporting details distinguishable?** Pass when emphasis and space reflect scientific importance and local details remain subordinate. A secondary annotation that dominates the figure, or unrelated panels whose relationship must be guessed, fails.
- **Are labels concise, interpretable, and sufficient?** Pass when names identify the objects and short annotations clarify operations without turning the figure into prose. Unexplained local jargon, sentence-heavy boxes, or figure-wide titles and footer statements that belong in the caption fail. Do not remove a necessary label merely to make the canvas cleaner.

## 4. Is the Information Density High Without Clutter?

- **Does each major visual element contribute meaning?** Pass when boxes, lines, color changes, and repetitions encode a useful distinction, relationship, or multiplicity. Decorative enclosures, placeholder-filled grids, and duplicate branches that add no information fail. Ask what understanding would be lost if an element were removed.
- **Have I retained the structure while compressing repetition?** Pass when simplification preserves branching, multiplicity, and scope. Example Figure 2 expands a representative branch and a selected cell rather than duplicating every process. A single generic box that hides all hierarchy fails just as an exhaustive, unreadable tree does.
- **Is the distribution of lines and whitespace balanced?** Pass when connectors have room, comparable gaps are consistent, and there are no avoidable congested junctions or isolated unused regions. Uniform line counts are not required. Crowded labels, gratuitous crossings, or an enclosure squeezed around an unchanged layout fail; reassess the whole affected region.
- **Do icons and colors help the explanation?** Pass when each use supports recognition, grouping, or a meaningful distinction. Icons should have compatible apparent sizes and stroke weights. Unrelated stock imagery, an icon for every label without a purpose, or colors implying unsupported relationships fail. Match the restrained treatment of Example Figure 2 without copying its layout.

## 5. Does the Actual Rendering Work at Manuscript Size?

- **Have I viewed a preview generated from this exact PPTX version?** Pass only after inspecting the actual current rendering. A stale PNG, export exit code, metadata check, or unseen file fails as evidence of visual verification. Missing export or viewing capability means **Not checked**, not Pass.
- **Is everything legible and correctly rendered at the intended placement size?** Pass when labels, symbols, arrowheads, borders, and fine grid lines remain clear without relying on magnified crops. Clipping, unintended wrapping, missing glyphs, font substitution that damages layout, weak contrast, or blurred assets fail. Check details at high resolution as well, but do not use zoom to excuse poor manuscript-scale readability.
- **Is the visual style coherent?** Compare primarily with [Example Figure 2](examples/example-figure-2.png), inspecting the [PPTX](examples/reference-figures.pptx) when needed. Pass when typography, emphasis, colors, line weights, and icon treatment work consistently across the new figure. Unintended shadows or effects, mismatched repeated components, and inconsistent visual roles fail. Layout differences are acceptable when they serve the new content.

## Decide Whether to Return to Build

For every failure, identify the affected region and the reason, then correct the cause in the generation script. Missing relationships or poor information hierarchy may require redesign, not smaller fonts or another annotation. Batch related corrections, regenerate, and rerun all affected checks. A layout change can invalidate connections, spacing, and readability outside the edited object.

Leave the loop only when all applicable questions pass for the same current version, no essential check remains unperformed, and no identified scientific or readability defect remains. View the latest rendering after the final change. Stop when these conditions are met; do not iterate indefinitely over unsupported cosmetic preferences. If a check cannot be performed, preserve the editable source and report exactly what remains unverified.
