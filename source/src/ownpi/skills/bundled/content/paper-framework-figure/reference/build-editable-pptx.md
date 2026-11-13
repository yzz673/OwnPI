# Build the Editable PPTX

Read all content below carefully and follow every instruction strictly.

Build a scientific explanation, not a collection of labeled boxes. Use the examples below to study how visual structures communicate the method. Follow the task and available scientific evidence; do not invent relationships or results to make a layout convenient.

## Read the Examples

- [Editable reference figures](examples/reference-figures.pptx): slide 1 is Example Figure 1; slide 2 is Example Figure 2.
- [Example Figure 1 preview](examples/example-figure-1.png): inspect the workflow, its local loop, and the management structures.
- [Example Figure 2 preview](examples/example-figure-2.png): the primary visual style reference. Inspect grouping, distillation, and agent reading across the same hierarchy.

View both PNGs before designing. Return to the relevant region as you make design decisions, and inspect the PPTX when exact style properties or reusable assets are useful. The previews and PPTX represent the same version.

When using OwnPI, read this guide with `skills_read`; its response includes the local `skill_root`. Resolve the example paths relative to that root, open the PNGs with `view_image`, and inspect the PPTX through Python when needed. Do not pass PNG or PPTX files to `skills_read`, which reads text resources only.

Use Example Figure 2 (slide 2) as the default visual style reference, including typography and text hierarchy, restrained semantic colors, border and arrow weights, and icon treatment. Use Example Figure 1 primarily for its examples of workflow, loops, containment, and interaction design; where the two styles differ, prefer slide 2 unless the task specifies otherwise. Read the actual properties from the PPTX as needed rather than guessing from pixels; account for theme-inherited values. Adapt the scale to the intended manuscript size. The examples are not layout templates: their panel counts, arrangement, subject matter, and scientific quantities do not prescribe the next figure.

## 1. Decide What the Figure Must Explain

Before choosing positions or shapes, identify the reader's main takeaway, the objects involved, and the relationships that establish it. Distinguish objects from operations, sequence from containment, and a repeated structure from one instance. Confirm grouping and aggregation units, arrow directions, and one-to-many or many-to-one relationships against the source or manuscript. Resolve factual uncertainties before encoding them visually.

In Example Figure 1, panel a explains the research progression and return path; panel b explains work within a stage; panel c explains the supporting management mechanisms. These are different explanatory scales, not three unrelated lists. In Example Figure 2, both branches begin with the same literature, but organize and distill it differently. Their shared origin and different aggregation units determine the structure before styling begins.

Write a short design outline stating the takeaway, essential relationships, and what can remain in the caption. Use it to decide what earns space in the figure; do not turn every implementation detail into a node.

## 2. Let Visual Structure Carry the Explanation

Choose visual forms that express the actual relationship. Use placement, containment, branching, loops, grids, and overview-to-detail expansion deliberately. Text should identify objects and clarify operations; it should not have to explain a relationship that the geometry fails to show.

In Example Figure 1, the leaders from panel a to panel b make the change of scale visible. The opposing curved arrows between Producer and Reviewer express iteration, while subagents are placed inside their respective parent roles. In panel c, paired arrows distinguish recording from informing, launching from receiving status, and formulating a plan from being guided by it. A generic line labeled "interaction" would lose these distinctions.

In Example Figure 2, panel a expands one selected subcategory into case groups, multiple patterns, and their synthesis into Method knowledge. Panel b expands a selected archetype-by-dimension cell. The expansion boundaries communicate the scope of each distillation process. Solid arrows show knowledge construction; the robot and dashed blue paths establish a separate entry and direction for agent reading and evidence tracing. Use conventions that fit the new method, and make their meaning consistent within that figure.

## 3. Increase Information Density by Removing Redundancy

Information density is the amount of meaningful structure the reader can understand, not the number of boxes, arrows, or labels. Keep repetition when it communicates multiplicity or comparison; compress it when it merely repeats an already understood structure.

Example Figure 2 panel a shows several methodological categories but expands only a representative branch into subcategories, then one subcategory into its distillation process. Shared row labels identify case groups and patterns without naming every small shape. Multiple groups and patterns remain visible because their multiplicity matters; drawing the complete hierarchy repeatedly would add clutter without explaining a new relationship.

Panel b uses a compact grid because its two axes determine how evidence is grouped. One highlighted cell connects that organization to the detailed process above. Filling every cell with placeholder text or repeating the process for every cell would add marks without adding understanding. Its specific grid dimensions belong to this example's system, not to the general design rule.

Avoid introducing an arbitrary concrete example merely to fill a box. The short findings, process records, and checklist items in Example Figure 1 panel c have a purpose: they make otherwise abstract management mechanisms recognizable. If an example does not clarify an abstraction, omit it. Never present an illustrative result as measured evidence.

## 4. Give the Reader a Clear Hierarchy and Reading Path

Choose a dominant reading direction and distinguish the main structure from supporting detail. Give important relationships enough space, and keep nearby labels subordinate to the structures they explain. Use concise labels; place extended explanation and the figure title in the caption.

Example Figure 1 panel a provides an overview across the top, with the local mechanism and its support below. The differently colored Analysis stage marks a meaningful role in the return path. Example Figure 2 uses a shared base and two upward branches, so the reader can follow each construction path and compare them without switching between separate figures. Neither arrangement should be copied when a new figure requires a different reading order.

Keep font roles consistent with the examples: comparable objects should have comparable emphasis, and annotations should not compete with the main labels. If the figure becomes unreadable at manuscript size, simplify or reorganize it before reducing text size.

## 5. Balance Whitespace and Line Density

Distribute visual density deliberately. Avoid a crowded junction beside a large unused region, connectors threading through text, or boundaries that leave labels pressed against an edge. Comparable elements need consistent alignment and spacing, while more complex structures may need more room.

Example Figure 1 separates the paired management arrows into clear horizontal lanes aligned with the three cards. Example Figure 2 separates grouping from the enlarged distillation regions; panel b reserves room for the grid, its axis labels, and the expansion leaders. Study those local gaps and routes, not just the outer panel rectangles.

Lay out the content before routing connectors. After adding a boundary, icon, label, or expansion, reassess the whole affected region and its neighbors. An enclosure that technically fits can still make the composition cramped; resize or redistribute the contents rather than treating the new shape as an isolated addition. Balance line density without adding decorative lines to empty areas.

## 6. Use Icons Where They Earn Their Space

In Example Figure 1 panel a, the stage icons provide quick visual cues while short labels preserve precision. Their common line style and similar apparent size let them support the workflow without competing with it. The Producer and Reviewer icons use the same restrained vocabulary. In Example Figure 2, the robot identifies the agent's reading entry, while branches, document symbols, and the grid carry the remaining structure without extra pictograms.

Use icons to improve recognition or distinguish a meaningful role, not to decorate every box. Match apparent size and stroke weight rather than only image dimensions. Prefer transparent assets, and do not use an icon in place of a scientifically necessary label or relationship.

## 7. Implement the Design as Editable Components

Use native PowerPoint shapes, editable text, and connectors for the main structure. Keep related elements organized without flattening the figure into an image. Embedded SVGs and bitmaps do not make their internal parts editable as PowerPoint shapes; preserve required source assets and describe their editability accurately.

Centralize style settings and derive positions from shared alignment anchors and spacing parameters. Inspect or reuse example assets and style properties when useful; copying a complex component across PPTX files may require preserving image, theme, and relationship references. Reconstruct it from inspected properties if copying would break those references.

Attach arrows to meaningful boundaries, preserve clear direction, and explicitly remove unwanted inherited effects. Use explicit text sizes and margins rather than automatic shrinking to accommodate excess content. Check the result at its intended manuscript size, then follow [Code and Visual Check](code-and-visual-check.md). Return to the example previews when a visual decision is uncertain; they support judgment rather than replace factual and visual verification.
