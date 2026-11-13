---
name: citation-verification
description: Verify scholarly source identity, produce provenance-bearing BibTeX, and validate bibliography identity closure during academic writing or revision.
tags: [Research, Academic, Citation, Reference]
version: 0.3.0
---

# Citation Verification

Use this Skill to verify citations, prepare source-backed BibTeX, and audit bibliographies. Verify sources as they are introduced, not only after writing.

## Ownership

- `academic-paper-writing` owns citation intent and claim fit: what requires support, where citations belong, and whether literature coverage is sufficient.
- This Skill owns citation identity, bibliographic provenance, and citation-package closure.
- Citation Tools assist resolution and package checks; their receipts are one verification route, not a prerequisite for every citation.
- `academic-paper-writing` audits whole-manuscript claim coverage, not citation identity.

Keep two questions separate:

1. **Identity:** Is this the intended work or dataset, with trustworthy metadata and a traceable source?
2. **Content access:** Was relevant source content actually available for inspection?

A verified identity does not imply accessible content. A missing DOI, optional metadata field, or native receipt does not by itself make an identity unverified.

## Canonical OwnPI Workflow

### 1. Establish the Citation Candidate

Start from the intended source in the research artifacts, user materials, or literature search.

- Prefer a DOI or arXiv ID when one is already known.
- When the exact work is unknown, use `web_search` to discover candidates.
- Treat search results, snippets, filenames, and model memory as discovery evidence only. They do not establish citation identity.

### 2. Resolve Citation Identity and BibTeX

1. Prefer `citation_resolve` for supported sources; load its schema with `tool_search(query="select:citation_resolve")`. Supply one DOI/arXiv identifier or exact title, with author/year to disambiguate title searches.
2. If resolution fails or is unsupported, use available tools to inspect the publisher, proceedings, repository, or attributable full text. Verify the title, creators, date, and version; do not wait for resolver support.
3. Preserve `citation_resolve.data.receipt` unchanged when returned. For direct-source verification, record the citation key, source URL or document locator, access date, and supporting metadata/excerpts in citation QA instead; do not manufacture a receipt.
4. Build or correct BibTeX from the verified source, retaining only supported fields. Do not invent a DOI or substitute a different work to fit a tool's interface.

Either route can establish citation identity. An ambiguous search candidate alone cannot; metadata verification also does not establish full-text access or claim support.

### 3. Integrate the Citation and Preserve Records

- Add only identity-resolved BibTeX entries to the bibliography.
- Number every entry in the final reference list.
- Place each citation beside the sentence or clause it supports, following the coverage and placement rules in `academic-paper-writing`.
- Maintain source evidence for every adopted citation, distinguishing unchanged native receipts from direct-source verification records.
- Cite the resolved publication version actually used; do not silently merge metadata from a preprint and a published version.
- For substantive corrections, record changed fields and supporting source evidence in citation QA. Formatting changes need no fresh lookup; a different work or version needs verification.

### 4. Validate Citation-Package Closure

1. Check the complete manuscript and bibliography for resolving keys, accurate identities, consistent versions, and supporting evidence for every adopted entry, whichever verification route was used.
2. Load `citation_package_validate` with `tool_search(query="select:citation_package_validate")` for an auxiliary check of complete inputs and available receipts. Account for receipt-only coverage errors on directly verified entries with per-key evidence; do not claim the Tool passed, delete valid entries, or fabricate receipts.
3. Resolve actual key, identity, and metadata errors. Preserve original Tool results and source-backed corrections; distinguish tool coverage from the whole-manuscript audit.

Completion requires accurate, traceable references and adequate claim coverage, not universal native receipts or a zero-warning report. A documented direct-source check can complete verification without changing the Runtime or validator.

## Failure Handling

- **Failed or unsupported resolution:** make reasonable source checks, then use direct-source verification. Without new evidence or a changed route, do not repeat failed lookups or blocker declarations, or wait for human/tool changes.
- **Still unverifiable:** replace the citation with a verified source that supports the same point, or omit it and remove or qualify any unsupported claim. Preserve verified attribution for actually used data/methods; do not conceal their use or substitute unrelated credit. Missing optional fields may be omitted.
- **Metadata-only or restricted access:** keep identity and content-access statuses distinct; let `academic-paper-writing` govern whether the available evidence supports the intended use.
- **Receipt integrity failure:** recover the unchanged original or verify directly instead; never fabricate or rehash a receipt. **Metadata differences:** document source-backed corrections without modifying the snapshot.

## References

- Read `references/verification-rules.md` with `skills_read(package="citation-verification", resource="references/verification-rules.md")` when judging identity, access, or package-closure status.
- Read `references/common-errors.md` with `skills_read(package="citation-verification", resource="references/common-errors.md")` when diagnosing a failed or inconsistent citation workflow.

## What Not To Do

- Do not generate citation metadata or BibTeX from memory.
- Do not use Google Scholar, search-result snippets, citation counts, or venue reputation as final verification authority.
- Do not fabricate receipts, source evidence, or claims of successful Tool verification.
- Do not let unverified references or placeholder keys enter the final manuscript package; missing native receipts alone are not a reason to reject directly verified sources.
