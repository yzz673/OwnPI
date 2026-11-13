# Research-Level Literature Grounding Contract

## Introduction

Literature Grounding builds a bounded, evidence-graded, prioritized, and extensible research landscape for the current topic. Its goal is to give later stages a reliable basis for research decisions without prematurely locking the project into one route. It is a research foundation, not an early paper Introduction or final positioning package.

Before writing any durable artifact, create `<workspace>/literature_grounding/` if it does not exist. Keep all durable artifacts owned by this stage, including its Durable Planning System, in that directory.

## What To Do

### Literature Grounding

1. Build a work set around the user's research question or research direction. Include related work provided by the user and search broadly beyond papers from the same immediate field. Relevant methods, models, experimental paradigms, analysis approaches, mechanisms, and analogous research questions from other fields may all provide useful insight. Not every source requires full-text retrieval, but important work must be understood and verified well enough to support the Related Work Map.

2. Use the work set to build a durable Related Work Map. For each important line of work, explain what it studied; how it approached the problem through methods, models, experiments, evaluation, and analysis; what conclusions or capabilities it produced; and what evidence, limitations, contradictions, or open questions remain. Connect these findings to the current topic by identifying useful method ideas, requirements for rigorous experiments and analysis, and scientific argument structures that show how strong research organizes its question, approach, evidence, and conclusions. Include negative and conflicting evidence rather than keeping only work that supports the current direction. When the map exposes an important gap in coverage or understanding, return to targeted search and update it. Keep the Related Work Map and its source traceability in `<workspace>/literature_grounding/` as a primary Literature Grounding output.

### Working Practices

- Keep reusable intermediate artifacts organized in `<workspace>/literature_grounding/`. Update existing canonical artifacts incrementally instead of scattering duplicate notes or disconnected versions. Disposable searches and probes do not need to be persisted when they add no reusable evidence or insight.

- When revisit feedback is present, read the feedback together with the current artifacts, preserve valid prior work, and make targeted improvements. Do not restart the stage or replace its outputs with a disconnected rewrite.

- When an important source cannot be accessed, first try reasonable recovery methods such as alternative databases, accessible versions, source recovery, or citation chaining. If the source still cannot be obtained, preserve its locator and access status, record the uncertainty, and keep any dependent claim appropriately bounded rather than silently dropping the research line or treating the source as verified.

## What Not To Do

- Do not construct concrete hypotheses, methods, or experiments for the current research, lock its final contribution or research story, or perform empirical work. Literature Grounding provides foundations and inspiration; later stages own the concrete research design and execution.
- Do not treat a reference list, a collection of papers, or isolated paper summaries as the result. Synthesize the related work into a Related Work Map that provides a foundation for the later research stages.
