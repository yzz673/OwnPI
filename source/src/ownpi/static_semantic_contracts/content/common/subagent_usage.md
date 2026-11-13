# Subagent Usage Guide

Subagents perform assigned work in separate contexts. Use them to distribute substantial work and give each task focused attention. This guide applies to the calling agent whenever `run_subagents` is available; the caller remains responsible for integrating the results and fulfilling its own task requirements.

The applicable stage contracts govern the work regardless of whether it is performed by the calling agent or a subagent. Delegated work must satisfy the same applicable requirements for content, quality, artifact format, and persistence at specified paths. Child feedback is communication between the subagent and the calling agent, not the stage deliverable itself. Reporting a result in feedback does not replace producing and saving the required artifacts.

## Choose and Organize Tasks

- Delegate work with a clear objective and a useful deliverable, such as investigating a question, conducting an experiment, producing a figure, drafting a section, or reviewing a specific artifact. A task may require substantial exploration and multiple steps; it need not be a trivial operation.
- Before delegating, map task dependencies and batch as many valuable, independent tasks as available capacity allows. Wait for prerequisite results before assigning dependent work; do not create low-value tasks merely to increase parallelism.
- Choose task boundaries that reduce the context each subagent must handle while preserving the information and freedom needed to do the work well. Parallel tasks may examine the same evidence from different perspectives when this serves a clear purpose.
- Assign distinct output paths or explicit file ownership to tasks that write artifacts. Separate contexts do not imply separate workspaces: concurrent subagents may access the same files. Identify shared inputs and who will integrate changes to shared outputs.

## Write the Assignment

Each task requires only `task_name` and `message`. Use a short, descriptive `task_name` that is unique within the batch. The `message` becomes the subagent's Task Message and is the authoritative description of its assignment.

Make the message self-contained for the assigned work. Include the information needed to establish:

- **Objective and purpose:** what to accomplish and how it contributes to the broader task.
- **Inputs and context:** relevant decisions, evidence, resource locations, and prerequisites. Provide explicit paths for existing inputs and intended outputs.
- **Applicable requirements:** the instructions, contracts, skills, or other references the subagent must follow, with usable identifiers or paths and an explicit instruction to read them where needed.
- **Scope and coordination:** which artifacts it owns, which inputs are shared, and any constraints that affect the work.
- **Deliverables and completion conditions:** the concrete results or artifacts expected under the applicable stage contracts, their required output paths, and what constitutes completion. Ask the subagent to produce and save those artifacts and identify them in its feedback.

Do not assume the subagent has seen your conversation history, current handoff, or every contract you have read. Supply the relevant information or direct it to the appropriate resources. Explain the desired outcome and necessary constraints while leaving room for the subagent to design and execute its approach.

## Run a Batch

Call `run_subagents` with a `tasks` list containing the assignments:

```json
{
  "tasks": [
    {
      "task_name": "descriptive-task-name",
      "message": "Self-contained assignment, input and output paths, requirements, and completion conditions."
    }
  ]
}
```

- Submit `run_subagents` alone, without other tool calls in the same turn. Tasks within the batch run concurrently, subject to available execution capacity; the call blocks the caller while the batch executes.
- Respect the configured `runtime.subagent_max_count`. An oversized batch is rejected before any child starts, and the error reports the requested count and limit. Reduce the batch size and retry; the limit applies to each call, not to the cumulative number of tasks in the run.
- Subagents submit completion feedback through `complete_subagent`; required artifacts must be saved at the locations specified by the applicable stage contracts. Consume the returned batch results before planning work that depends on them.

## Consume and Integrate Results

- Examine each child's status and feedback. Distinguish completed work from failures or incomplete work; one child's failure does not invalidate another child's successful output.
- A `failed` child indicates that the assigned task did not reach a valid completion boundary; it does not establish that the child made no progress. Before reassigning the same task, inspect the relevant workspace artifacts and evidence produced during that execution, then decide whether to continue from them, revise the assignment, or reassign the task.
- The tool result provides a feedback index and result previews. When `feedback_truncated` is true, use `file_reader` on the relevant `feedback_path` and follow pagination to read the full feedback. Read the full feedback whenever the preview is insufficient for the next decision. Explicitly unavailable feedback is not an empty successful result.
- Use the reported evidence and artifact locations to inspect the outputs needed for integration. Preserve useful findings and quantitative results, reconcile overlapping outputs, and incorporate the work into the relevant deliverables and ongoing plan.
- If work remains incomplete, use the returned error or feedback to decide what to correct or reassign. In a follow-up assignment, provide the existing artifacts and remaining objective rather than assuming a new subagent remembers the previous execution.
- Judge completion against the original task requirements. Delegation changes how the work is performed; the caller still owns the final synthesis, decisions, and completion assessment required by its role.
