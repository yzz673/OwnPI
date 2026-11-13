---
name: planning-with-files
description: >-
  This skill should be used for complex or long-running work that needs a
  durable Markdown planning blueprint, using one file or a macro-to-micro file
  tree as needed.
when_to_use: >-
  Use when work spans many actions and its plan must stay current; skip it for
  simple one-step work.
tags:
  - Planning
aliases:
  - durable planning
  - markdown planning
  - planning blueprint
version: 0.3.0
---

# Planning with Files

## Purpose

Use a durable Markdown planning system as the blueprint for complex work. Keep it current while
working so the direction, progress, and next work remain clear. Skip this Skill for simple one-step
tasks.

## Build the Planning System

- Create and maintain the planning system in the current task workspace by default. Use another
  location only when the user or runtime explicitly specifies one.
- Use one Markdown file for straightforward work.
- For more complex work, build a Markdown tree as needed, expanding from a broad overview to
  finer-grained plans.
- Choose the shape of the tree from the actual work. Do not impose a fixed split, depth, directory
  layout, filename scheme, or document schema.

## Maintain the Blueprint

- Maintain the planning system throughout the work rather than treating it as an initial outline.
- Keep completed, current, remaining, and next work visible.
- Revise the existing planning system when results, failures, or feedback change the work. Preserve
  valid completed work instead of planning again from scratch.

## Keep Plans Bounded

- Keep actual work products in their owning files. Record concise evidence or artifact locators in
  the planning system instead of copying large outputs, logs, or payloads into it.
- Split planning detail into the Markdown tree when a single file no longer stays clear and useful.

## Bridge to `update_plan`

- Treat the durable Markdown planning system as the complete, persistent blueprint.
- When `update_plan` is available, use it to expose only the current 3-7 actionable steps.
- Keep that short window aligned with the durable planning system. It does not replace the durable
  files, evidence, or work products.
- Follow the tool surface for payload fields and valid statuses.

## Finish

Before finishing, review the planning system against the work. Confirm that known work is complete,
required evidence or artifact locators are present, and no remaining item has been overlooked.
