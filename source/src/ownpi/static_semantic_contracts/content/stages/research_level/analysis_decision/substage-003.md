# Research Audit and Routing Contract

This contract governs the research audit and routing work within Research-Level Analysis Decision. Its purpose is to audit each required stage against its contract and durable outputs, judge the overall research maturity from the reconstructed research action line, and select exactly one next stage.

Before writing any durable artifact, create `<workspace>/analysis_decision/` if it does not exist. Store all durable artifacts produced by this work under `<workspace>/analysis_decision/`, including its Durable Planning System and the final Analysis Memo.

## Procedure

1. Read the Raw User Message and Task Spec, the organized research record in `<workspace>/analysis_decision/research_view/`, and the action line, source map, and open questions in `<workspace>/analysis_decision/research_logic/`.
2. Read the stage contract or substage contracts for each required upstream stage: Literature Grounding, Research Framing, Method Design, Experiment Design, and Implementation Execution. Inspect that stage's actual durable artifacts under `<workspace>` and follow their locators to the underlying evidence.
3. Audit each required stage separately. For every stage, determine:
   - whether its stage objective was completed;
   - whether all core obligations were executed;
   - whether important results are valid and verified;
   - whether important work and results were recorded durably;
   - whether any core work is missing, failed, unvalidated, or unresolved.
4. Mark a stage as high-quality complete only when all five checks pass. Do not mark a stage as high-quality complete because its limitations were documented, missing work was labeled unresolved, or partial results can support a narrower paper claim. An unexecuted, failed, unvalidated, or unresolved core obligation means that the stage is not high-quality complete.
5. Build a complete Problem Map. For every material problem, record its artifact location, causal owner, downstream impact, dependencies, valid remaining evidence, and the evidence supporting the diagnosis. Preserve causal relationships among problems; do not replace a completeness finding with claim narrowing.
6. Judge research maturity against the intended objective and actual evidence. Verify that reasonable, scientifically meaningful, feasible deeper and broader avenues are substantially explored, with little expected scientific gain from continuing. Check prior attempts and revisions before recommending more work; valid negative/null results and evidence-supported infeasibility count as completed attempts. Repetition requires a new scientific reason or a specific unresolved defect, not a desire for positive results.
7. Persist a detailed Analysis Memo under `<workspace>/analysis_decision/`. It must contain the stage-by-stage completion audit, the reconstructed research logic, the scientific audit, the complete Problem Map, the research maturity judgment, and the rationale for the selected target stage.
8. Before writing feedback, read the Feedback Authoring Contract with `contract_read(contract_id="authoring.feedback")`.
9. Submit concise feedback with `submit_feedback()`, then call `next_stage(target_stage)` exactly once with a stage exposed by the tool for the active workflow plan. The next-stage receipt is the routing authority.

## Routing Rules

- Select exactly one next stage.
- Call `next_stage(target_stage="output_synthesis")` only when every required upstream stage is high-quality complete, every core obligation has been executed and verified, the evidence supports the research objective, no actionable defect requires revisit, and the exploration assessment above establishes little expected scientific gain from further reasonable work.
- Do not route to `output_synthesis` when any required upstream stage is incomplete, partially executed, failed, unvalidated, or contains an unresolved core obligation.
- Claim narrowing, limitation reporting, and honest recording of missing work do not replace completion.
- When a recoverable root-cause problem requires further work, route to one prior stage selected in the active workflow plan. The selected stage must be causally responsible for the problem. If multiple stages are involved, select the earliest or highest-leverage responsible stage.
- Analysis Decision does not terminate the run.
- After a revisit returns, repeat the complete stage-by-stage audit and re-evaluate the complete project record rather than checking only the revised local artifact. Re-evaluating the whole project does not require rebuilding the research view or duplicating its evidence ledgers.

## Completion Gate

Complete this work only after the stage-by-stage audit, integrated research maturity judgment, Problem Map, Analysis Memo, feedback submission, and exactly one valid `next_stage()` call have all been completed.
