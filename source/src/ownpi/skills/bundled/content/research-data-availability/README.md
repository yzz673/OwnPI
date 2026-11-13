# `research-data-availability` skill

A data-availability skill for preparing manuscript data statements, repository plans, dataset
citations, and FAIR metadata checks for scientific publication.

It can also align Chinese author notes with submission-ready English when bilingual support is
explicitly needed.

## What it does

- drafts ready-to-paste Data Availability statements
- audits weak or incomplete data statements before submission
- maps each supporting dataset to a repository, accession, DOI, or access route
- distinguishes public, controlled-access, third-party, supplementary, and not-applicable cases
- prepares FAIR metadata and DataCite-style dataset citation checks
- flags missing repository records, licences, provenance, embargo details, and access conditions
- supports bilingual author notes when explicitly needed

## Source hierarchy

- target-journal data policies
- Nature Portfolio and Springer Nature research data policies as bundled specialist references
- Nature Portfolio reporting standards for availability of data, code, materials, and protocols
- Scientific Data data policies for repository, rawness, preservation, and data citation practice
- FAIR Guiding Principles and DataCite metadata schema

## File structure

```text
research-data-availability/
├── SKILL.md
├── README.md
├── agents/
│   └── openai.yaml
└── references/
    ├── fair-metadata-checklist.md
    ├── chinese-author-alignment.md
    ├── policy-principles.md
    ├── repository-and-identifiers.md
    ├── source-basis.md
    └── statement-patterns.md
```

## When to use

- preparing a Data Availability statement for a scientific journal
- deciding where to deposit data before submission
- revising "available on request" language
- handling controlled-access, human-participant, proprietary, or third-party data
- citing datasets with DOI, accession number, Handle, ARK, or repository record
- checking whether a dataset deposit is FAIR enough for publication
- converting Chinese data-availability notes into precise English submission language

## Design intent

The skill should make the availability route explicit for every dataset that supports the paper's
claims. It should not fabricate accessions, licences, restrictions, or repository metadata. When
information is missing, it should return a usable draft plus a short list of items the author must
confirm.
