# Citation Verification Judgment Rules

Use these rules to judge citation identity, content access, and package closure. Native receipts and inspected authoritative sources are alternative evidence routes; tool coverage is not the scientific acceptance criterion.

## 1. Identity Judgment

Treat identity as resolved when the metadata identifies the intended work or dataset unambiguously through a resolver result or inspected publisher, proceedings, repository, or attributable full-text evidence. Preserve its locator and supporting metadata/excerpts. A DOI/arXiv ID is not mandatory.

Check that:

- the title identifies the intended work;
- the author information is compatible with the intended work;
- the year and venue are consistent with the cited version;
- any supplied DOI, arXiv ID, or source URL belongs to that work;
- preprint and published versions have not been silently combined.

Minor capitalization or formatting differences are not identity conflicts. Materially different titles, authors, identifiers, or publication versions require resolution rather than guessing.

## 2. Content-Access Judgment

Record content access independently from identity:

- **accessible:** source content was retrieved and can be inspected;
- **metadata-only:** identity metadata or an abstract is available, but full source content was not retrieved;
- **restricted:** explicit evidence shows that authentication, a paywall, or access policy prevents retrieval;
- **unavailable:** retrieval failed without enough evidence to classify the source as restricted.

A timeout, DNS failure, or generic network error is `unavailable`, not proof of a paywall. Content-access status does not change the resolved identity of a scholarly work.

## 3. Package Closure

Citation-package closure requires:

- every manuscript citation key to resolve to a bibliography entry;
- every final bibliography entry to have either a native receipt or recorded direct-source verification;
- every retained receipt to remain intact;
- any retained receipt's key and identity (including an explicitly pinned version) to match the bibliography, without requiring identical BibTeX;
- no placeholder keys or unresolved citation markers in the final manuscript.

The native validator requires receipts for all entries and therefore cannot certify mixed-route coverage. Preserve its actual result; account for receipt-coverage errors with per-entry direct-source evidence and check the complete bibliography independently. Do not excuse unresolved keys, identity conflicts, or fabricated evidence as tool limitations.

Review metadata differences against sources, retaining original receipts where available. An evidenced correction can be resolved despite a warning. Neither a clean report nor a receipt alone certifies claim support or literature coverage.
