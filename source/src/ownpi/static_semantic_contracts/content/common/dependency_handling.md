# Dependency Handling

The Runtime provides two managed dependency locations for this run:

- Managed Python environment: `<managed_venv>`
- Managed non-Python dependency root: `<managed_non_python_dependency_root>`

Use the following order when a task needs a package, executable, compiler, renderer, or other dependency:

1. If the user provides an environment, inspect and use that environment first when it contains the required dependency and is appropriate for the task.
2. If the required dependency is not available in the user-provided environment, or no environment was provided, check the managed locations above.
3. If the dependency is still missing, inspect the current host environment for an existing usable dependency.
4. If the dependency is still missing, install Python packages into the managed Python environment. Install non-Python dependencies into the managed non-Python dependency root only when the dependency supports the required managed layout or prefix installation.
5. Do not write dependencies into system directories, system Python, or a system LaTeX installation. Do not assume that discovering a dependency in another environment makes it available in the managed environment; select the explicit environment or install into the managed location as appropriate.

When checking a user-provided Python environment, distinguish the Python process running the Runtime from the target environment. A module check is evidence about the interpreter that performed the import. A path check only establishes that an executable exists. To determine whether a module is available in the user-provided environment, run that environment's explicit Python executable (or an explicitly activated equivalent) and perform the import there. Do not infer the target environment's module availability from a check performed by the Runtime Python. If the probe did not run in the target environment, report the target environment as unverified and verify it before deciding to install or fall back.

When installing or running a dependency in a selected environment, use one unambiguous command form supported by the execution tool. Either provide a shell command or provide the structured executable and argument fields; do not provide both forms in the same invocation. After installation, rerun the import or executable check with the selected environment and record the result before using the dependency.
