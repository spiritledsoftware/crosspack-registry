---
title: Dotfiles Package Batch for Registry
summary: Added 11 dotfiles-derived package manifests to the registry, with validation passing and signature sidecars deferred to the merge signing workflow.
tags: []
related: [ci/github_actions/registry_quality_gate_and_signing_workflow_fix.md, ci/github_actions/dotfiles_package_batch_for_registry.abstract.md, ci/github_actions/dotfiles_package_batch_for_registry.overview.md]
keywords: []
createdAt: '2026-05-03T01:37:34.841Z'
updatedAt: '2026-05-03T01:37:34.841Z'
---
## Reason
Record the batch of packages added from chezmoi-dotfiles and the validation outcomes.

## Raw Concept
**Task:**
Add the next batch of high-value packages from the user's dotfiles repository into the registry.

**Changes:**
- Added package manifests for ast-grep, chezmoi, fastfetch, jj, lsd, mise, neovim, opencode, stylua, yazi, and yt-dlp
- Validated registry source configs and manifests
- Ran release-bot dry-runs for the new packages
- Deferred signature sidecars to the merge signing workflow

**Files:**
- packages/ast-grep.toml
- packages/chezmoi.toml
- packages/fastfetch.toml
- packages/jj.toml
- packages/lsd.toml
- packages/mise.toml
- packages/neovim.toml
- packages/opencode.toml
- packages/stylua.toml
- packages/yazi.toml
- packages/yt-dlp.toml
- .github/workflows/registry-quality-gate.yml
- .github/workflows/sign-manifests-on-merge.yml

**Flow:**
identify dotfiles package set -> add package manifests -> validate source configs and manifests -> run release dry-runs -> defer signature sidecars until merge signing

**Timestamp:** 2026-05-02

**Author:** Ian

**Patterns:**
- `REGISTRY_PREFLIGHT_ALL=1 REGISTRY_PREFLIGHT_SKIP_SMOKE=1 ./scripts/registry-preflight.sh` - Preflight command that fails until signature sidecars exist

## Narrative
### Structure
A single registry batch captured the dotfiles-derived package additions and the validation pipeline results for the worktree branch.

### Dependencies
The validation flow depends on allowing missing signatures during PR checks, with signature sidecars created by the merge signing workflow after merge.

### Highlights
The package batch was intentionally limited to manifests only; GUI casks and system/runtime libraries were excluded as less suitable for the registry batch.

### Rules
PR validation can use --allow-missing-signatures; the signing workflow creates sidecars after merge.

### Examples
The added package set included developer CLI tools such as chezmoi, fastfetch, jj, neovim, yazi, and yt-dlp.

## Facts
- **source_repo**: The registry batch came from the dotfiles repository ian-pascoe/chezmoi-dotfiles. [project]
- **worktree_branch**: The work was done in the opencode/kimaki-package-batch-1 branch inside the /home/ianpascoe/.kimaki/worktrees/1010908a/package-batch-1 worktree. [project]
- **packages_added**: 11 package manifests were added: ast-grep, chezmoi, fastfetch, jj, lsd, mise, neovim, opencode, stylua, yazi, and yt-dlp. [project]
- **validation_results**: Registry validation passed with 79 source configs and 224 manifests when allowing missing signatures. [project]
- **release_dry_run**: Release bot dry-runs passed for all 11 new packages. [project]
- **signature_sidecars**: The preflight script failed because the new package manifests did not yet have .toml.sig sidecars. [project]
