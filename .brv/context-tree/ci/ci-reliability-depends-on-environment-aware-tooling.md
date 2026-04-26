---
confidence: 0.59
sources:
  - ci/_index.md
  - facts/_index.md
synthesized_at: '2026-04-26T00:25:15.611Z'
type: synthesis
---

# CI reliability depends on environment-aware tooling

The CI fix and the stored personal preference both point to the same operational concern: the repository must behave predictably in constrained execution environments, and assumptions about local tooling should be avoided. In CI, the signing script was changed to remove its dependency on `xxd` and use `python3` instead, while the workflow redesign delays validation until signature sidecars exist, reducing race and environment-related failures.

## Evidence

- **ci**: `scripts/sign-changed-manifests.sh` no longer depends on `xxd`; hex encoding now uses `python3`, aligning with repository test-environment constraints.
- **facts**: The personal facts area currently contains one lasting preference: reasoning effort = medium, explicitly reset by the user on 2026-04-26T00:05:57.857Z.
