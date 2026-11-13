# Research Logic Reconstruction Contract

This contract governs the research-logic organization work within Research-Level Analysis Decision. Its purpose is to reconstruct the full research logic from the organized research record before the work is audited or routed.

Before writing any durable artifact, create `<workspace>/analysis_decision/` if it does not exist. Store all durable artifacts produced by this work under `<workspace>/analysis_decision/research_logic/`, including its Durable Planning System.

## Research Logic Record

Create and maintain the following structure under `<workspace>/analysis_decision/research_logic/`:

```text
research_logic/
├── README.md
├── research_action_line.md
├── source_map.md
└── open_questions.md
```

`README.md` records reconstruction progress, logical gaps, and the directory map. `source_map.md` links action-line nodes to research-view records and authoritative source locators; it does not duplicate experiment, execution, statistical, or quantitative ledgers. `open_questions.md` records unresolved ambiguities that must be considered during the later audit.

## Procedure

1. Read the Raw User Message and Task Spec to establish the complete task context.
2. Read `<workspace>/analysis_decision/research_view/`, including its inventory, evidence records, quantitative records, and integrated view. Read the necessary upstream context directly: the user's research direction, Related Work Map, Research Contract, Method Tree, Resource Basis, and Experiment Framework.
3. Navigate the research view to inspect logical relationships. Trace missing information, ambiguities, contradictions, and important logical jumps to the underlying sources as needed; do not repeat a full implementation-source audit or build duplicate evidence ledgers. Correct factual errors or omissions in the corresponding research-view records rather than maintaining conflicting facts. Treat trajectories, feedback, receipts, revisit history, and record-store entries as process evidence and locators, not as substitutes for scientific artifacts.
4. Reconstruct the complete research action line by connecting:
   - the user objective and task constraints;
   - the user's research direction and related work;
   - the Research Contract and Method Tree;
   - the Resource Basis and Experiment Framework;
   - the implementation actions, revisions, executions, results, analyses, and decisions;
   - the conclusions and remaining unresolved work.
5. Audit the chain forward for coherence and backward from the evidence to identify where each major result and decision originated. Preserve relationships across resources, subjects, conditions, metrics, controls, replications, and result directions.
6. Record what was intended, designed, attempted, revised, and concluded, linking to execution evidence. Distinguish untried or inadequately tested avenues from valid negative/null results and evidence-supported infeasibility. Preserve missing, failed, partial, and unresolved work without treating the absence of a positive result as unfinished work.
7. Keep `README.md` current with reconstruction progress and unresolved logical gaps. On subsequent entries, update affected action-line nodes and source links, then check the coherence of the complete line; do not reconstruct it from scratch by default.

## Completion Gate

Complete this work only when:

- the complete research action line is recorded in `research_action_line.md`;
- every major action-line node is linked to its research-view records and authoritative sources in `source_map.md`;
- missing, failed, partial, unvalidated, and unresolved work is preserved explicitly;
- every material statement in the action line links to an authoritative source locator;
- open ambiguities are recorded in `open_questions.md` rather than silently resolved;
- relationships are complete and traceable without repeating the preceding block's full source audit;
- the research logic is complete enough for the next phase to audit each stage and the overall research maturity without reconstructing the project from scratch.
