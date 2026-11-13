---
name: research-data-availability
description: >-
  Prepare, audit, or revise publication-ready Data Availability statements, data repository plans,
  dataset citations, and FAIR metadata checklists for scientific manuscripts. Use for research
  data sharing, repository selection, persistent identifiers, restricted or sensitive data,
  source data, supplementary datasets, DataCite-style references, or publication FAIR metadata.
---

# Research Data Availability

Use this skill to turn a manuscript's supporting data into a transparent, publication-ready data
availability package: statement text, repository plan, dataset citations, and missing-information
flags.

The governing policy comes from the target journal and article type. The bundled policy references
provide detailed support for Nature Portfolio and Springer Nature submissions; FAIR data practice
and DataCite metadata provide the general implementation layer.

## Default stance

- Treat the Data Availability statement as a link between the paper's claims and the evidence
  needed to inspect, reproduce, or reuse them.
- Do not invent DOIs, accession numbers, repository names, licences, embargo dates, ethics
  approvals, access committees, or data-use conditions.
- Prefer public, discipline-specific repositories. Use generalist or institutional repositories
  only when no suitable community repository exists.
- Describe both newly generated data and reused third-party data.
- If data cannot be openly shared, state why, who controls access, how requests are evaluated,
  and what metadata or representative data can still be public.
- Separate data, code, materials, and protocols unless the journal asks for a combined
  availability section.
- Keep this skill focused on availability and metadata. Do not rewrite methods, analyze
  statistics, or polish the manuscript unless the user asks for those tasks separately.
- Flag "available upon request" as weak unless there is a specific legal, ethical, commercial, or
  third-party restriction.

## Workflow

1. Identify the target journal and article type. If journal-specific instructions conflict with
   this skill, follow the journal.
2. Inventory every dataset needed to support the main and supplementary results:
   generated raw data, processed data, figure source data, secondary data, software outputs,
   models, tables, images, and files underlying statistical analysis.
3. Classify each dataset into one access route:
   `public repository`, `controlled access repository`, `within paper or supplement`,
   `reused public source`, `third-party restricted`, `available on justified request`,
   or `not applicable`.
4. Choose repository and identifier strategy before drafting text. Prefer DOI, accession number,
   Handle, ARK, or stable repository record over personal websites and temporary cloud links.
5. Draft the Data Availability statement using explicit dataset-to-location and access mapping.
6. Add formal dataset citations for public data that support conclusions.
7. Run the FAIR and metadata audit before finalizing.
8. Return ready-to-paste statement text plus any unresolved fields the author must confirm.

## Output format

Unless the user asks for another format, return:

```text
Data Availability
[ready-to-paste statement]

Repository and citation actions
- [specific actions or "None"]

Missing information / risk flags
- [specific flags or "None"]
```

When auditing an existing statement, lead with blocking issues first, then provide a revised
version. When bilingual author alignment is requested, also provide concise Chinese notes for
unresolved decisions.

## Related files

| File | Open when |
|---|---|
| `references/policy-principles.md` | You need the governing Nature/Springer Nature data-sharing rules or edge-case policy logic |
| `references/chinese-author-alignment.md` | The user needs bilingual wording or provides Chinese availability notes |
| `references/statement-patterns.md` | You need ready-to-adapt Data Availability statement patterns |
| `references/repository-and-identifiers.md` | You need repository choice, accession, DOI, embargo, versioning, or dataset citation guidance |
| `references/fair-metadata-checklist.md` | You need FAIR checks, README metadata, file organization, licences, provenance, or DataCite fields |
| `references/source-basis.md` | You need to justify rules with official sources or check which source supports which rule |

Read a needed file with `skills_read(package="research-data-availability", resource="references/<file>.md")`.

## Source hierarchy

Use sources in this order:

1. Target journal instructions and submission system requirements.
2. Nature Portfolio / Springer Nature data, code, materials, and reporting policies when the
   target venue belongs to that journal family.
3. Repository-specific requirements and domain community standards.
4. FAIR principles and DataCite metadata practice.

If a policy detail may have changed, verify the current journal page before giving final
submission advice.
