---
name: latex-workflow
description: Guides an efficient LaTeX workflow for authoring, building, revising, and validating an academic paper while minimizing unnecessary recompilation and keeping source and outputs coherent.
---

# LaTeX Workflow

Use this Skill when an academic-paper deliverable includes LaTeX source or a compiled PDF. It governs the workflow from manuscript source to a validated LaTeX release candidate. It is not a LaTeX syntax tutorial and does not determine the paper's scientific argument, citation evidence, figure design, or final delivery structure.

## Keep the Source of Truth Clear

- Establish a canonical source arrangement before substantial authoring. Identify the applicable `.tex` entry points, bibliographies, included section files, figures, tables, Supplementary source, build targets, and build directories. Use the arrangement that fits the paper instead of assuming one entry point or one output.
- Document how each release artifact is built, including its source entry point, working directory, and LaTeX toolchain. If any of these change, record the change and rebuild the affected artifacts from canonical source.
- Treat PDFs, rendered pages, logs, and auxiliary files as derived artifacts. Do not edit them as manuscript source.
- Keep previews, historical builds, and current release candidates clearly distinguished. Multiple deliverables are normal; avoid conflicting versions that claim the same role.
- When a file is generated, repair its generator or source template instead of repeatedly patching the generated output.

## Work in Phases

### 1. Prepare the Source

Inspect existing material before creating another source tree. Select the canonical files and remove ambiguity about where edits belong. Record the expected build targets, entry points, and output locations.

### 2. Author in Coherent Batches

Develop the manuscript, bibliography, Supplementary material, and asset plan in coherent batches. Do not interrupt ordinary drafting with a complete build or rendered-paper inspection after every edit.

Before a manuscript build, inspect the source for:

- placeholders, unfinished sections, and duplicate or stale content;
- missing include paths, bibliography files, citation keys, labels, and cross-references;
- malformed LaTeX commands and incorrect document boundaries;
- missing figure or table inclusions; and
- divergence between generated files and their generators.

### 3. Build and Inspect Assets

Generate and inspect figures and tables at the asset level before integrating them into the manuscript. An asset revision normally requires rebuilding and rechecking that asset, not immediately rebuilding the entire paper.

Stabilize the manuscript text, bibliography, Supplementary source, and asset set relevant to each build target before starting final document closure.

### 4. Choose the Smallest Validation Scope

Match validation effort to the change and the question being resolved:

- Prose, planning, and metadata edits do not require an immediate PDF build.
- Citation-key, cross-reference, include-path, or LaTeX-structure changes require a later manuscript build, but related edits should be batched first.
- Figure or table changes require asset-level regeneration and inspection. They do not immediately require a complete manuscript rebuild unless integration or layout is being evaluated.
- Layout-affecting changes require rebuilding the affected manuscript target and inspecting the affected rendered pages.
- Copying or reorganizing an unchanged release candidate does not require recompilation.

After canonical source changes, treat earlier compiled PDFs and inspection conclusions as outdated. Rebuild only at the next meaningful checkpoint after related edits are complete.

### 5. Use Intermediate Build Checkpoints Deliberately

Use an intermediate build when it answers a concrete question or prevents further work from accumulating on a broken source structure. Useful checkpoints may include an early structural build, a build after a substantial authoring batch, or a build after related LaTeX-structure changes.

At an intermediate checkpoint, build each affected target once from its canonical entry point and inspect the build diagnostics. Do not run complete rendered-paper QA by default. Inspect rendered pages only when the current question concerns layout, asset integration, or another visual property.

Before the first manuscript build, discover `latex_build` with `tool_search(query="select:latex_build")` and use its current schema for intermediate and final builds.

If a build fails, classify the findings as source, bibliography, asset, toolchain, or environment problems. Address related findings together, then rebuild the affected target once. Do not repeatedly rebuild without making a relevant change.

### 6. Complete Final Document Closure

Start final closure only after the manuscript text, bibliography, Supplementary source, and applicable assets are stable enough to evaluate as a complete paper.

Build each required target from its canonical entry point with the documented working directory and toolchain. Use the bibliography and repeated LaTeX passes required to resolve citations and cross-references. Check at least:

- compilation and bibliography status;
- unresolved citations, references, and labels;
- missing assets and material box warnings;
- page count and required document components; and
- agreement between the canonical source and each produced PDF.

After the final build succeeds, inspect the rendered paper. Use a document-level overview plus detailed inspection of pages containing figures, tables, equations, dense text, or other layout-sensitive content. Check clipping, overflow, float placement, alignment, legibility, blank pages, and visual consistency. A successful build command alone does not establish rendered quality.

Discover `pdf_inspect` with `tool_search(query="select:pdf_inspect")`. Use its rendered-page references with `view_image` for visual inspection, then bind the visual findings through `pdf_inspect` according to its current schema.

Collect findings before editing and fix related issues together. If a finding changes canonical source or assets, rebuild the affected target and repeat only the inspection needed to verify the change. Do not restart the entire workflow.

### 7. Finalize the LaTeX Release Candidate

Freeze a coherent set of canonical source and validated PDFs. The set may contain a main manuscript, Supplementary material, appendices, or other required documents in the arrangement appropriate to the paper. Make the role and current version of each artifact unambiguous, then hand the release candidate to the active Output Stage Contract for final delivery organization.

## Build Discipline

Build checkpoints are decision points, not a fixed schedule. Compile when a build can validate a coherent batch, answer a concrete integration question, or establish the final release candidate. Do not compile merely because a file was edited.

When the preferred compiler or Runtime Tool is unavailable, keep the limitation explicit and follow the `Dependency Handling` section already included in your operating instructions. Do not create an unrelated fallback manuscript or treat a preview produced through another route as the final compiled paper.

## Completion Standard

The workflow is complete when the canonical source, documented build procedure, build diagnostics, rendered QA, and release-candidate PDFs describe the same paper. File existence alone does not establish this agreement, and repeated rebuilding alone does not improve it.
