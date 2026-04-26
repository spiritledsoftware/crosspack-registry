---
title: Reasoning Effort Preference
summary: Reasoning effort is set to medium, and work should use an isolated worktree when local main is dirty and divergent.
tags: []
related: []
keywords: []
createdAt: '2026-04-26T01:24:46.747Z'
updatedAt: '2026-04-26T10:36:30.025Z'
---
## Reason
Capture stated reasoning effort preference and working mode note

## Raw Concept
**Task:**
Document preference and repository handling notes from the conversation

**Changes:**
- Set reasoning effort to medium
- Reasoning effort was set to medium
- Use a clean worktree from origin/main for the CI fix
- Commit only the CI fix files and open the PR against main
- Set reasoning effort preference to high
- Recorded reasoning effort preference as medium
- Recorded PR 55 and local main branch state
- Recorded use of an isolated worktree for safe rework

**Flow:**
inspect PR and branch state -> use isolated worktree -> avoid disturbing local in-progress files

**Timestamp:** 2026-04-26T10:36:25.062Z

## Narrative
### Structure
The note combines a user preference with repository state constraints relevant to ongoing work.

### Dependencies
Safe rework depends on avoiding the dirty, divergent local main checkout by using .worktrees.

### Highlights
The assistant chose the smallest safe rework path and isolated the checkout to protect local work.

## Facts
- **reasoning_effort**: Reasoning effort reset to medium. [preference]
- **pr_55_state**: PR 55 is open but dirty against current main. [project]
- **local_main_state**: Local main is divergent and dirty. [project]
- **worktree_strategy**: An isolated .worktrees checkout is used so the rework does not disturb local in-progress files. [project]

---

Reasoning effort preference history: the user first set reasoning effort to high at 2026-04-26T00:05:57.764Z, then later reset it to medium at 2026-04-26T00:05:57.857Z. The current effective preference is medium, while the earlier high setting is preserved as historical context. Keep both events with explicit chronology so future readers can see the transition from high to medium.
