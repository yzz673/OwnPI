# Final Delivery Workflow Contract

This contract governs the final workflow block of Research-Level Output Synthesis. Its purpose is to select, organize, and verify the completed user-facing research delivery.

## Delivery Structure

Create this directory:

```text
<workspace>/output_synthesis/final_delivery/
├── README.md                         # delivery map and usage instructions
├── pdf/                              # final compiled paper PDF
├── supplementary/                    # final Supplementary material
├── citations/                        # bibliography and citation assets
└── reproducibility/                  # applicable code, data, and reproduction assets
```

Include only final or explicitly deliverable artifacts. Do not include intermediate drafts, diagnostic files, superseded versions, or unrelated workspace files. Main-text tables are conditional: include them only when required by the selected profile or explicitly planned. Include Supplementary tables when required by the Supplementary plan.

## Packaging Procedure

1. Select the final manuscript, Supplementary material, citations, and reproducibility artifacts from the completed writing workflow.
2. Copy or export them into the corresponding delivery directories without changing their substantive content.
3. Write `README.md` with the primary deliverables, purpose of each item, usage or reproduction instructions, source revision, and material limitations or exclusions.
