---
name: pdf
description: Provides guidance for extracting PDF content, rendering pages, creating paper-oriented PDFs, and checking PDF layout and visual quality.
---

# PDF

Use this skill when a task requires PDF-specific handling. Choose the operation according to whether the goal is to understand the document, create a PDF, or verify the rendered result.

## Read PDF Content

- Use `pymupdf` or `pypdf` for text extraction and quick structural checks.
- Treat extracted text as content evidence, not as evidence of layout fidelity.
- Check page counts, readable pages, missing text, and extraction warnings when they affect interpretation.
- Use OCR only when the PDF is scanned or ordinary extraction does not provide usable text.

## Render and Inspect Pages

- Prefer visual review when layout, figures, tables, typography, or page composition matters.
- Prefer `pymupdf` for rendering when it is available; use Poppler only when it is already available and appropriate.
- Inspect rendered pages for clipped text, overlap, broken images, blank pages, unreadable glyphs, inconsistent spacing, and malformed tables.
- Re-render after meaningful changes and inspect the current render rather than relying on an earlier image.

## Create Paper-Oriented PDFs

- Use LaTeX for paper-oriented PDF creation unless an existing project-native generator is clearly more appropriate.
- Keep source content, generated assets, and the resulting PDF consistent with one another.
- Preserve stable, descriptive filenames and make the final PDF traceable to the source and rendering inputs.

## Quality Checks

Before treating a PDF as usable, check the properties relevant to the task:

- the document opens and has the expected page structure;
- required text, citations, headers, and page numbers are present;
- text and figures are not clipped or overlapping;
- tables, charts, and images are sharp, aligned, and legible;
- fonts and special characters render correctly;
- no tool tokens, placeholders, unresolved markers, or broken references remain.

Do not claim that a PDF is visually correct from text extraction alone. A successful parser or build does not establish layout quality.

## Dependencies

- Use the PDF libraries and rendering utilities available in the current environment.
- Prefer existing managed dependencies and project tooling.
- Do not install additional system dependencies solely for a rendering path when an available equivalent provides the required check.
