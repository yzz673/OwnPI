# Research-Level Method Design Contract

## Introduction

Method Design focuses on developing a coherent methodological approach for the Research Contract. It establishes the method logic that connects the Research Main Line and Research Contents and provides the methodological foundation for downstream Experiment Design.

Before writing any durable artifact, create `<workspace>/method_design/` if it does not exist. Keep all durable artifacts owned by this stage, including its Durable Planning System, in that directory.

## What To Do

### Design a Method Tree

1. Search within `<workspace>` for the Research Contract and use it as the primary design anchor. Understand the Research Main Line, the coherent Research Contents organized around it, and the methodological needs created by those contents while remaining aligned with the complete current-task context.

2. Design one logically coherent Method Framework around the Research Main Line. Depending on the research topic, the framework may be a model, framework, pipeline, analysis route, or a connected combination of them. Design the Method Framework around the Research Contents in the Research Contract, using shared and content-specific components that belong to the same method logic where appropriate. Do not assign an independent method to each Research Content or assemble unrelated techniques into a method package.

3. Brainstorm credible alternatives for the framework's key design components so that important decisions are not locked to a single unexamined route. Explain what role each component serves, how it connects to the rest of the framework, which alternatives are compatible, and why the current primary choice is preferred. Keep these choices at the level of methodological principles, component roles, and relationships; a primary choice is a methodological direction, not a fixed implementation configuration or experimental protocol. Alternatives are required for meaningful design decisions, not for every minor detail.

4. Organize the result as a Method Tree: the Research Main Line is the root, the unified Method Framework and its current primary path form the trunk, the Research Contents map to the connected components that address them, and credible alternatives branch from the relevant design nodes. The Method Tree should allow Experiment Design to determine what must be validated.

### Working Practice

- During method design, search within `<workspace>` for the Related Work Map and consult it when it can provide useful ideas or inspiration.
- Give the method clear motivation and logical rigor, and keep its components consistent and coherent with one another. After these foundations are established, pursue non-trivial methodological reasoning specific to the Research Main Line and Research Contents. Existing methods and components may be used, but blind component stacking, cosmetic recombination, or formulaic application of a generic method does not constitute meaningful methodological innovation.
- When revisiting the stage, improve the current method according to the feedback instead of redesigning the entire method from scratch.
- Persist the Method Tree as a primary stage artifact in `<workspace>/method_design/`.

## What Not To Do

- Do not depart from the Research Main Line and design a collection of unrelated methods. Method components and alternatives must remain part of one coherent Method Framework.
- Do not turn the Method Tree into an implementation specification or concrete experimental protocol. Do not fix layer counts, hidden widths, retained dimensionalities, learning rates, batch sizes, training schedules, seed lists, exact data splits, fold counts, sample-size thresholds, candidate-set construction, top-k choices, statistical test configurations, or execution commands. Explain the method's scientific logic and key components while leaving these concrete choices open.
