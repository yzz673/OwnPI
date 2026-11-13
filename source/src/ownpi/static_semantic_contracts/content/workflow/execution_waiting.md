# Execution Waiting Contract

The current Execution Board ref in dynamic context points to the canonical compact status index for
the current run's durable local and remote jobs. Read it with `file_reader` when execution state
affects the next action.
The Board is not an experiment result or scientific evidence artifact.

Continue independent work while jobs run. Do not repeatedly poll jobs, and use execution diagnostic or
control tools only for a concrete need. Wait only when active jobs remain, every available completed
result has been handled, and no independent work is ready. Before waiting, read the current Board
revision and call `wait_for_execution_update(after_revision=...)`.
