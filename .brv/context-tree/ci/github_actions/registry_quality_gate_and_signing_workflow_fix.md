---
title: Registry Quality Gate and Signing Workflow Fix
summary: Workflow fix work was done in ./.worktrees/fix-registry-gate-after-signing; origin/main already had the python3 portability change; validation passed with tests.test_github_workflows and full test discovery.
tags: []
related: [ci/github_actions/sign_manifests_on_merge.md, ci/github_actions/registry_quality_gate_and_signing_workflow_fix.md]
keywords: []
createdAt: '2026-04-26T00:09:44.731Z'
updatedAt: '2026-04-26T01:33:41.225Z'
---
## Reason
Record the worktree placement, workflow sequencing fix, and verification outcomes for the registry gate/signing change.

## Raw Concept
**Task:**
Document the registry gate after signing workflow fix and its clean worktree verification.

**Changes:**
- Changed registry-quality-gate.yml from push-based validation to workflow_run after signing completes
- Added a regression test for the GitHub workflow trigger ordering
- Replaced xxd dependency with python3 hex encoding in sign-changed-manifests.sh
- Created the clean PR workspace under ./.worktrees
- Kept the branch based on origin/main
- Confirmed origin/main already had the python3 portability fix
- Verified the workflow test and full suite
- Pushed the fix/registry-gate-after-signing branch and opened PR 74

**Files:**
- .github/workflows/registry-quality-gate.yml
- .github/workflows/sign-manifests-on-merge.yml
- tests/test_github_workflows.py
- scripts/sign-changed-manifests.sh
- .worktrees/fix-registry-gate-after-signing
- tests

**Flow:**
move workspace -> base on origin/main -> apply workflow sequencing fix -> run workflow regression test -> run full test discovery -> push branch -> open PR

**Timestamp:** 2026-04-25T00:00:00Z

**Author:** Ian

## Narrative
### Structure
This knowledge captures the PR workspace location, the minimal scope of the fix, and the verification steps performed in the clean worktree.

### Dependencies
The work depended on origin/main already containing the python3 portability change in the signing script.

### Highlights
Validation succeeded in a clean ./.worktrees worktree, including the workflow-specific unittest and the full suite with 33 passing tests.

### Rules
PRs still validate unsigned manifest changes, but the full signed registry scan runs after the signing workflow completes instead of racing it on the same push.

### Examples
The branch name and PR metadata are preserved for traceability: fix/registry-gate-after-signing and PR 74.

## Facts
- **worktree_location**: The clean PR workspace was moved into ./.worktrees/fix-registry-gate-after-signing. [project]
- **signing_script_portability_change**: origin/main already included the signing-script python3 portability change. [project]
- **remaining_work**: The PR worktree only needed the workflow sequencing fix and its regression test. [project]
- **workflow_test**: python3 -m unittest tests.test_github_workflows passed in the clean worktree. [project]
- **full_test_suite**: python3 -m unittest discover tests passed with 33 tests. [project]
- **branch_name**: The branch pushed was fix/registry-gate-after-signing. [project]
- **pull_request**: The PR was opened at pull request 74. [project]

---

## Key points
- The CI race condition was fixed by changing `registry-quality-gate.yml` from a push-based trigger to a `workflow_run` trigger that runs **after** `sign-manifests-on-merge.yml` completes on `main`.
- This ensures the registry quality gate scans **signed manifests**, avoiding evaluation of the merge commit before signature sidecars exist.
- A regression test was added in `tests/test_github_workflows.py` to prevent the trigger-ordering bug from returning.
- `scripts/sign-changed-manifests.sh` was made more portable by replacing the `xxd` dependency with `python3`-based hex encoding.
- Validation behavior remains split: PRs still validate unsigned manifest changes, while the full signed registry scan happens only after signing finishes.
- Reported test results indicate the focused workflow tests, signing-script tests, and the full suite all passed, with the suite totaling 22 tests.

## Structure / sections summary
- **Reason**: States the purpose — documenting the CI race fix and related test/portability updates.
- **Raw Concept**: Summarizes the concrete task, file changes, workflow flow, and timestamp.
- **Narrative**: Explains the architectural intent, dependency ordering, testing highlights, and behavioral rules.
- **Facts**: Lists specific project facts, including trigger changes, test status, portability fix, and unrelated worktree changes left untouched.

## Notable entities, patterns, or decisions
- **Workflows involved**:
  - `.github/workflows/registry-quality-gate.yml`
  - `.github/workflows/sign-manifests-on-merge.yml`
- **Test file**:
  - `tests/test_github_workflows.py`
- **Script updated for portability**:
  - `scripts/sign-changed-manifests.sh`
- **Design decision**: Use `workflow_run` to serialize signing before quality gating, eliminating same-push race conditions.
- **Implementation pattern**: Keep lightweight PR validation separate from the heavier signed registry scan.
- **Environment constraint**: `xxd` was unavailable in the test environment, motivating the switch to `python3`.
- **Workflow order**: `merge on main -> sign manifests workflow -> registry quality gate signed scan -> tests verify ordering and portability`
