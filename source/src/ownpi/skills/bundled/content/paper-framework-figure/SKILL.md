---
name: paper-framework-figure
description: Create editable research-paper model architectures, method pipelines, and framework diagrams using python-pptx, with LibreOffice PDF export and rendered visual inspection. Use when a figure primarily communicates components, information flow, or relationships rather than quantitative results.
---

# Editable Paper Framework Figures

Use this file for the overall workflow, dependency preparation, and final delivery. Read the relevant reference before building or checking a figure; do not build or accept a figure from this file alone. Make design and correction decisions autonomously from the task and available scientific evidence.

## Workflow

`Resolve dependencies -> [Build editable PPTX -> Code and visual check -> Correct as needed] -> Export, render, and deliver`

## 1. Resolve Dependencies

The workflow requires Python with `python-pptx`, a LibreOffice executable capable of headless Impress PDF export, and a PDF renderer such as PyMuPDF. Required fonts must be available to LibreOffice in the selected execution environment. Optional assets or formula rendering may need additional dependencies.

For dependency discovery and installation, follow the `Dependency Handling` section already included in your operating instructions. Respect a user-provided environment. Do not prescribe a separate installation policy in this skill.

Resolve the required environment before entering the build/check loop. Revisit this step only when a dependency changes or a missing dependency blocks progress.

## 2. Build and Check Loop

1. Read [reference/build-editable-pptx.md](reference/build-editable-pptx.md), then build or correct the editable PPTX.
2. Read [reference/code-and-visual-check.md](reference/code-and-visual-check.md), then perform code checks followed by visual inspection of a current rendered preview.
3. If a check fails, return to Build, correct the affected content or layout, and repeat the affected checks. Leave the loop only when code and visual checks pass for the same current figure revision.

Preview export and rendering required for visual inspection belong inside this loop. They are checks, not final delivery. Reuse unchanged intermediate results; after a change, regenerate the preview and rerun checks whose results may have been invalidated. A missing or unavailable check does not count as a pass.

## 3. Export, Render, and Deliver

1. Export the accepted PPTX to a vector PDF with LibreOffice and render a PNG preview. Reuse outputs already verified in the loop if they match the accepted revision.
2. Verify that the final files open correctly and match the accepted figure. Visually inspect any new export; if a defect appears, return to the build/check loop.
3. Deliver the editable PPTX, PDF, PNG preview, generation script, and any required source assets in the requested output directory. Remove temporary conversion files.

## Completion Gate

Before marking the task complete or accepting the figure, read [Code and Visual Check](reference/code-and-visual-check.md) and apply its full question checklist to the final delivered revision. Inspect the actual PPTX and rendered outputs against the checklist; a claim that checks passed is not sufficient evidence.

Completion requires every applicable check to pass, with no failed or unperformed checks. An item may be marked Not applicable only with a valid reason. Confirm that the delivered PPTX, PDF, and PNG represent the same checked revision. If any check fails, return to the build/check loop and recheck the affected outputs; if a required check cannot be performed, report the exact gap and do not claim completion or approval.
