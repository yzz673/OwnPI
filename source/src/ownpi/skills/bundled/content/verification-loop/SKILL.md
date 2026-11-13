---
name: verification-loop
description: Use after code, experiment, or research-implementation changes when their correctness needs verification. Provides adaptive build, type check, lint, test, security, and diff review guidance.
version: 1.0.0
---

# Verification Loop Skill

A comprehensive verification workflow for completed changes.

## When to Use

Invoke this skill:
- After completing a feature or significant code change
- When the current task requires explicit quality evidence
- After refactoring

## Verification Phases

Choose the commands adaptively for the current project instead of running every example blindly. Use the stack-appropriate command from `references/STACK-DETECTION.md` when the repo does not match the default examples below.


### Phase 1: Build Verification
```bash
# Python projects (uv)
uv build 2>&1 | tail -20
# OR
python -m build 2>&1 | tail -20

# Node.js projects
npm run build 2>&1 | tail -20
# OR
pnpm build 2>&1 | tail -20
```

If build fails, STOP and fix before continuing.

### Phase 2: Type Check
```bash
# TypeScript projects
npx tsc --noEmit 2>&1 | head -30

# Python projects
pyright . 2>&1 | head -30
```

Report all type errors. Fix critical ones before continuing.

### Phase 3: Lint Check
```bash
# JavaScript/TypeScript
npm run lint 2>&1 | head -30

# Python
ruff check . 2>&1 | head -30
```

### Phase 4: Test Suite
```bash
# Python projects
pytest --cov=src --cov-report=term-missing 2>&1 | tail -50

# Node.js projects
npm run test -- --coverage 2>&1 | tail -50
```

Report:
- Total tests: X
- Passed: X
- Failed: X
- Coverage: X%

### Phase 5: Security Scan
```bash
# Python: Check for secrets
grep -rn "sk-" --include="*.py" . 2>/dev/null | head -10
grep -rn "api_key" --include="*.py" . 2>/dev/null | head -10
pip-audit

# Node.js: Check for secrets
grep -rn "sk-" --include="*.ts" --include="*.js" . 2>/dev/null | head -10
grep -rn "api_key" --include="*.ts" --include="*.js" . 2>/dev/null | head -10

# Check for debug statements
grep -rn "print(" --include="*.py" src/ 2>/dev/null | head -10
grep -rn "console.log" --include="*.ts" --include="*.tsx" src/ 2>/dev/null | head -10
```

### Phase 6: Diff Review
```bash
# Show what changed
git diff --stat
git diff HEAD~1 --name-only
```

Review each changed file for:
- Unintended changes
- Missing error handling
- Potential edge cases

## Verification Record

Record the results of the applicable phases:

```
VERIFICATION REPORT
==================

Build:     [PASS/FAIL]
Types:     [PASS/FAIL] (X errors)
Lint:      [PASS/FAIL] (X warnings)
Tests:     [PASS/FAIL] (X/Y passed, Z% coverage)
Security:  [PASS/FAIL] (X issues)
Diff:      [X files changed]

Overall:   [PASS/FAIL] for the current task

Issues to Fix:
1. ...
2. ...
```

## Reverification

Repeat relevant checks after major changes or when earlier verification evidence is no longer current.


## Reference Files

Load only what is needed:
- `references/STACK-DETECTION.md` - how to choose the right verification command set for the current repo
- `references/REPORT-TEMPLATE.md` - report structure for final verification output
- `examples/example-verification-report.md` - example final report
