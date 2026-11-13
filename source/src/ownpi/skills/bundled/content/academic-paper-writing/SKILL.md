---
name: academic-paper-writing
description: Provides universal and paper-type-specific guidance for manuscript writing. Use for any academic paper writing.
---

# Academic Paper Writing

The real writing guidance lives in the references below; reading this entry alone is not sufficient. If anything in this Skill conflicts with the user's requirements, follow the user's requirements.

## How to Use the Skill

First, select the profile that matches the manuscript type. Each profile contains one document for manuscript content and one document for figures and tables. Read both documents for the selected profile.

- For a conference paper, use the materials in `references/conference/`. If the user does not specify otherwise, use `templates/iclr-2026` as the LaTeX writing template.
- For a journal paper, use the materials in `references/top-tier-journal/`. If the user does not specify otherwise, use `templates/springer-nature` as the LaTeX writing template.
- If the user does not specify a paper type, use the journal paper profile by default.

In addition to the selected profile, read the Universal Writing Principles with `skills_read(package="academic-paper-writing", resource="references/universal-writing-principles.md")`.

The Universal Writing Principles and the selected profile are complementary and should be used together.

The `benchmark/` directory contains an example paper for each profile. These papers serve only as quality references for manuscript writing, not as benchmarks for the quality or scope of the underlying research. Do not force the manuscript to match their section lengths, page allocations, or other proportions. Consult the relevant benchmark paper when useful for the task at hand.
