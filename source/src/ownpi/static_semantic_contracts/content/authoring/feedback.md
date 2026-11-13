# Feedback Authoring Contract

Write concise, findings-first feedback.

## Findings

When findings exist, present them first and order them by priority. Use one entry per issue:

`[P1] Actionable finding title - evidence locator`

Follow the title with one short paragraph that states the concrete problem, its impact, and the
evidence that establishes it. Keep each locator as precise as the available material allows.

- `P0`: a critical failure that invalidates the current result or makes continuation unsafe.
- `P1`: an urgent defect or evidence gap that should be addressed next.
- `P2`: an ordinary defect or evidence gap that should be addressed.
- `P3`: a lower-impact issue that remains actionable and worth fixing.

Valid locators include `file:line`, an artifact path, a claim ID, a stage name, a result reference,
or an evidence reference. Do not invent a locator that cannot be followed.

Report only concrete, supported, actionable findings. When no qualifying finding exists, write
exactly `No findings.` Do not create a finding merely to avoid an empty review.

## Overall Assessment

When findings exist, end with a brief overall assessment when it adds useful context. Mention
material residual risks or evidence limitations when they affect interpretation. Keep the assessment
secondary to the findings and do not repeat them.
