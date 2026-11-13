# Citation Workflow Troubleshooting

Read this reference when citation resolution or package closure fails. Preserve Tool diagnostics, but distinguish a failed lookup from an unverified source.

## Identity Problems

### Ambiguous title resolution

**Signal:** Several works plausibly match the supplied title, or the resolver cannot establish one canonical identity.

**Response:** Refine with author/year or inspect an authoritative source directly. If identity remains ambiguous after reasonable checks, replace or omit the citation and review dependent claims. Do not choose by title familiarity or repeat unchanged queries.

### Unsupported source or missing parsed metadata

**Signal:** A dataset DOI fails lookup, a publisher date is not parsed, or a DOI-free proceedings URL is rejected despite available primary evidence.

**Response:** Verify the exact work/version from repository metadata, the publisher page, or full text; retain the locator and evidence in citation QA and create source-backed BibTeX without a native receipt. Do not invent an identifier, wait for resolver changes, or substitute a same-title work.

### Metadata mismatch

**Signal:** The requested identifier conflicts with the returned title, authors, year, venue, or DOI.

**Response:** Check the identifier and version against primary sources. Verify a different work/version afresh. For same-identity metadata errors, retain any original receipt and record evidenced corrections in citation QA.

### Preprint and published version confusion

**Signal:** The same work has different years, venues, titles, or author lists across arXiv and a formal publication.

**Response:** Cite the version actually used and preserve its canonical identifier. Prefer the formal publication when the manuscript relies on that version, but do not silently replace a specifically used preprint.

## Content-Access Problems

### Identity verified but content unavailable

**Signal:** Metadata resolves, but the source is restricted or unavailable.

**Response:** Preserve identity evidence and content-access status. Try another legitimate source or local copy when claim verification requires it; do not hide access limitations or treat metadata as proof of content.

## Receipt and Package Problems

### Receipt hash or schema failure

**Signal:** A citation receipt is rejected as incomplete, modified, or incompatible.

**Response:** Recover the unchanged original result. If recovery or a fresh lookup is unavailable, verify directly and record that route instead; never reconstruct a receipt or label a damaged one valid.

### Manuscript key missing from bibliography

**Signal:** `citation_package_validate` reports an unresolved citation key.

**Response:** Add the identity-resolved BibTeX entry or remove the unsupported citation from the manuscript, then validate again.

### Bibliography entry lacks a receipt

**Signal:** The bibliography contains an entry that has no provenance-bearing citation receipt.

**Response:** Use a native receipt or recorded direct-source verification. For the latter, document the validator's receipt-coverage limitation and check the entry and citation links independently. A missing receipt is not missing evidence; never report a failed Tool result as passed.

### Unused or duplicate entries

**Signal:** Multiple keys represent the same work, or bibliography entries are not cited.

**Response:** Consolidate duplicate keys and archive receipt retries where present. Preserve entries needed by the manuscript and Supplementary material; audit every retained entry regardless of verification route.

### Metadata warning persists after correction

**Response:** Check the field against reliable sources and record the conclusion. A justified final-entry correction need not match the original snapshot or eliminate the warning; an unsupported difference is not resolved. Never revert known errors or remove necessary citations to obtain a clean report.
