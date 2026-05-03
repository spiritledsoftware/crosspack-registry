---
title: Registry Package Formats and GUI Cask Support
summary: The registry already supports DMG packages, uses neovide as the schema reference for GUI casks, and a second batch added 12 manifests including GUI apps plus validation and dry-runs that all passed.
tags: []
related:
  - ci/github_actions/dotfiles_package_batch_for_registry.md
  - ci/github_actions/registry_quality_gate_and_signing_workflow_fix.md
keywords: []
createdAt: '2026-05-03T01:55:35.414Z'
updatedAt: '2026-05-03T01:55:35.414Z'
consolidated_at: '2026-05-03T02:26:35.104Z'
consolidated_from:
  - {date: '2026-05-03T02:26:35.104Z', path: ci/github_actions/registry_package_formats.md, reason: 'These files cover the same registry package-format topic and the more detailed GUI-cask batch note supersedes the shorter dmg-support note. The richer file should absorb the shared dmg/neovide facts and keep the batch validation details, package lists, and checksum edge case.'}
---
## Reason
Document registry package format support and the GUI cask batch update using neovide as the DMG reference

## Raw Concept
**Task:**
Document the registry batch update that extended support to GUI casks and verified package manifests

**Changes:**
- Confirmed DMG support in the registry
- Used neovide as the existing package schema reference
- Added 12 new package manifests including GUI apps and CLI tools
- Ran registry validation, release bot dry-runs, and unit tests successfully
- Adjusted spotify-player checksum handling for nonstandard sidecar naming

**Files:**
- packages/neovide.toml
- packages/balenaetcher.toml
- packages/cargo-binstall.toml
- packages/goreleaser.toml
- packages/hammerspoon.toml
- packages/karabiner-elements.toml
- packages/linear.toml
- packages/obsidian-cli.toml
- packages/obsidian.toml
- packages/opencode-desktop.toml
- packages/pearcleaner.toml
- packages/spotify-player.toml
- packages/tirith.toml

**Flow:**
user notes DMG support -> neovide schema reference identified -> GUI and CLI batch added -> validation and dry-runs executed -> tests passed

**Timestamp:** 2026-05-02

**Author:** Ian

## Narrative
### Structure
The update belongs with the registry GitHub Actions knowledge because it describes package-manifest content and validation outcomes for the dotfiles package batch.

### Dependencies
The batch depends on the existing neovide package pattern for DMG and GUI app metadata, plus registry validation and release bot dry-run tooling.

### Highlights
The registry already models DMG support, and the batch expanded coverage to GUI casks while preserving validation quality. All listed checks passed, indicating the new package manifests were consistent with the registry rules.

### Examples
Examples of newly added GUI packages include balenaetcher, hammerspoon, karabiner-elements, obsidian, opencode-desktop, and pearcleaner.

## Facts
- **registry_dmg_support**: The registry supports DMG packages. [project]
- **schema_reference_package**: neovide is used as the schema reference for GUI casks and DMG metadata. [project]
- **batch_size**: A second batch added 12 manifests. [project]
- **added_packages**: The added packages were balenaetcher, cargo-binstall, goreleaser, hammerspoon, karabiner-elements, linear, obsidian-cli, obsidian, opencode-desktop, pearcleaner, spotify-player, and tirith. [project]
- **validation_status**: Validation passed for registry source configs and manifest checks. [project]
- **dry_run_status**: Release bot dry-runs passed for all 12 new packages. [project]
- **test_status**: Python unit tests passed with 62 tests. [project]
- **spotify_player_checksum_handling**: spotify-player required checksum handling adjustments because its .sha256 sidecars did not follow the {url}.sha256 pattern. [project]

## Supplemental note from registry_package_formats
- The registry supports dmg artifacts for package publishing.
- Neovide is a concrete existing example of a package using dmg.
- Publishing flow: package -> build artifact -> registry publish
- This note is a durable confirmation of artifact-format support.