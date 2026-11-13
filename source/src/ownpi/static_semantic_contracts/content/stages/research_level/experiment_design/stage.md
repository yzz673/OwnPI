# Research-Level Experiment Design Contract

## Introduction

Experiment Design focuses on developing a resource-grounded Experiment Framework around the Research Contract and Method Tree. The Experiment Framework defines coherent experimental directions for studying the Research Main Line and Research Contents and evaluating the Method Framework. For each direction, it states the scientific purpose, candidate resources, and possible evidence, without fixing the concrete protocol, final interpretation, or low-level operational choices at this stage.

Before writing any durable artifact, create `<workspace>/experiment_design/` if it does not exist. Keep all durable artifacts owned by this stage, including its Durable Planning System, in that directory.

## What To Do

### Design the Experiments

1. **Establish the Resource Basis.**
   - Search within `<workspace>` for the Research Contract and Method Tree, and use them as the primary design anchors for determining what resources the research requires.
   - Inspect both the resources provided by the user and the resources already available in `<workspace>` to understand what they contain, what they can support, and their important limitations. When needed, perform a lightweight information audit by reading documentation, metadata, directory or file structure, schemas, or small samples without running formal experiments.
   - Evaluate each resource by the useful support it can provide, not by whether it can support the entire research program alone. A dataset that supports only part of the Research Contents may still be valuable. Distinguish what is structurally supported by the available information from what remains plausible but execution-dependent or unknown.
   - If no suitable resources are available or important needs remain unsupported, actively search for and acquire appropriate public resources. Treat user-provided resources as priority candidates and use them whenever they are scientifically suitable, but do not force incompatible resources into the experiment design.
   - Consolidate the available resources, their supported Research Contents, suitability, limitations, and unresolved gaps into a Resource Basis, and persist it in `<workspace>/experiment_design/`.

2. **Define a Coherent Experiment Framework.**
   - Search within `<workspace>` for the Research Contract, Method Tree, and Resource Basis, use them as the primary design anchors, and define connected experimental directions around the Research Main Line, Research Contents, and the Method Framework represented in the Method Tree.
   - Give every direction a clear scientific purpose. State which research question, Research Content, method component, or alternative it examines, why the direction is needed, which resources may support it, and what information it may provide. Do not assemble disconnected directions merely because they are easy to run or because a resource is available.
   - Retain only the high-level constraints needed to preserve the scientific meaning of the direction. Do not turn a direction into a near-complete experimental protocol. Do not prescribe its exact resource assignment, population or task definition, comparison design, baseline or control family, outcome or metric, split, leakage-prevention implementation, statistical procedure, or analysis pipeline. Do not fix target construction, candidate-set size or sampling policy, vocabulary cutoff, top-k choice, exact split proportions or fold identifiers, statistical budget, hyperparameters, feature or model choices, file bindings, execution commands, or final claim scope.
   - Organize the framework around feasibility validation, candidate formal experiments, and diagnostic or adjustment branches. State the prerequisites and scientific role of each direction, and define a coherent research space rather than prescribe one fixed route.
   - Persist the completed Experiment Framework as a primary stage artifact in `<workspace>/experiment_design/`.

### Working Practice

- When revisiting the stage, revise the current Resource Basis and Experiment Framework according to the feedback, preserving valid prior design instead of restarting the experiment design from scratch.

## What Not To Do

- Do not turn resource grounding into large-scale experimentation. Inspect only the lightweight information needed to understand and assess the resources at this stage.
- Do not fix the final experimental protocol, interpretation, or claim scope in the Experiment Framework. In particular, do not lock the target construction, candidate-set size or sampling policy, vocabulary cutoff, top-k choice, exact split proportions, concrete fold IDs, statistical budget, hyperparameters, file bindings, or execution commands.
- Do not reduce the Experiment Framework to a single minimally sufficient experiment. Define a coherent set of purposeful directions and make use of scientifically suitable resources where they can provide distinct evidence or diagnosis, while avoiding redundant directions that add no distinct scientific value.
