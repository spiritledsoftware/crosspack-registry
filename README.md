# crosspack-registry

Official Crosspack registry source.

## Structure

- `registry.pub` - trusted Ed25519 public key (hex-encoded, 32-byte key as 64 hex chars)
- `packages/` - package templates (`<package>.toml` + `<package>.toml.sig`)
- `releases/` - version documents (`<package>/<version>.toml` + `<package>/<version>.toml.sig`)

## Package and Release Contracts

- `packages/<package>.toml` stores shared package metadata and artifact templates:
  - package identity (`name`, `license`, `homepage`)
  - upstream source metadata (`[source]`)
  - artifact template metadata (`target`, `asset`, archive hints, binaries/completions/gui metadata)
- `releases/<package>/<version>.toml` stores version-specific resolved artifact data:
  - `name`, `version`
  - per-target `url` + `sha256`
- signatures are detached hex sidecars (`.sig`) for both package and release docs

## Package Update and Rollback Procedure

When updating package metadata:

1. Update package template in `packages/<package>.toml` if shared metadata/template fields changed.
2. Add a release document in `releases/<package>/<version>.toml` with resolved `url` + `sha256` per target.
3. Open a PR with changed package/release documents.
4. After merge to `main`, workflow `.github/workflows/sign-manifests-on-merge.yml` signs changed documents and updates sidecars.
5. Keep validation logs in `logs/` with command output for traceability.

If a published update must be rolled back:

1. Revert affected package/release document(s) and sidecar(s) to last known-good revision.
2. Re-run validation and clean-prefix install checks.
3. Publish rollback commit with links to fresh validation logs.

## Automation Setup

- Configure repository secret `CROSSPACK_REGISTRY_SIGNING_PRIVATE_KEY_PEM` (Ed25519 private key PEM).
- Ensure workflow permissions allow `contents: write` so generated `.sig` files can be committed back to `main`.

## Upstream Release Bot

Manifest updates do not need to be hand-authored for configured packages.

- Source-of-truth config lives in `packages/*.toml`.
- Workflow `.github/workflows/upstream-release-bot.yml` checks upstream releases and opens PRs for new versions.
- The bot writes:
  - package template docs in `packages/`
  - release docs in `releases/<package>/`

### Generalized source model

Package configs use a generalized schema under `[source.*]` so new upstream patterns can be expressed by composing release discovery, version derivation, checksum loading, and asset URL behavior instead of adding package-specific source kinds.

Current supported strategies:

- `[source.release]`
  - `kind = "github_releases"` with `repo` and optional `tag_prefix` / `include_prereleases`
  - `kind = "json_index"` with `url` for array/object JSON indexes such as Node, Go, and Zig
  - `kind = "text_endpoint"` with `url` and `version_regex` for simple text metadata such as rustup stable metadata
- `[source.version]`
  - `kind = "github_tag"` for SemVer release tags
  - `kind = "semver_field"` for indexes that already expose a SemVer string field
  - `kind = "prefixed_semver_field"` for fields such as `v22.22.2` or `go1.26.2`
  - `kind = "asset_name_regex"` for deriving a version from release asset names
  - `kind = "regex_capture"` for extracting a SemVer value from a release field
- `[source.checksum]`
  - `kind = "asset_digest"` for GitHub release assets that expose `sha256:<hex>` digests
  - `kind = "download_index"` for upstream indexes that already contain SHA-256 values
  - `kind = "download_sha256"` for GitHub-style releases where the registry hashes downloaded assets
  - `kind = "shasums256"` with `url_template` for upstreams that publish a checksum manifest
  - `kind = "url_sha256"` for per-asset `.sha256` sidecar URLs
- `[source.asset]`
  - `kind = "json_index_asset"` for upstream indexes that include resolved asset URLs and checksums
  - `kind = "release_asset_url"` for direct release asset URLs from the release feed
  - `kind = "templated"` with `base_url` for deterministic URL construction from artifact templates

Legacy `provider = "github"` and `provider = "nodejs-dist"` source definitions are still normalized by tooling for compatibility during migration, but new configs should prefer the generalized `[source.release]`, `[source.version]`, `[source.checksum]`, and `[source.asset]` tables.

Useful commands:

```bash
# Validate package source configs
python3 scripts/registry-validate-source.py packages/*.toml

# Dry-run release detection and generation planning
python3 scripts/upstream-release-bot.py --dry-run

# Limit to a single package
python3 scripts/upstream-release-bot.py --dry-run --package ripgrep
```

For operator review/update steps, see `scripts/registry-update-runbook.md`.

## Registry Preflight (Local + CI)

CI enforces a registry quality gate that validates changed package/release docs and runs smoke-install checks for changed releases.

- Schema checks for `packages/*.toml` and `releases/*/*.toml`
- Path/name/version consistency checks
- Required sidecar format checks (`.toml.sig` as 128 hex chars)
- PR smoke-install matrix on `ubuntu-latest` and `macos-latest` for changed release docs
- macOS app-bundle canary via `python3 scripts/registry-smoke-install.py --app-bundle-canary`

Run the same checks locally:

```bash
./scripts/registry-preflight.sh
```

Useful variants:

```bash
# Full scan of all package/release manifests
REGISTRY_PREFLIGHT_ALL=1 ./scripts/registry-preflight.sh

# Full scan without smoke-install
REGISTRY_PREFLIGHT_ALL=1 REGISTRY_PREFLIGHT_SKIP_SMOKE=1 ./scripts/registry-preflight.sh

# Validate only manifests changed from a specific base commit
REGISTRY_BASE_SHA=<base-sha> ./scripts/registry-preflight.sh
```

## Maintainer Scaffolding Workflow

Use scaffold to create package/release placeholders for one-off/manual entries:

```bash
scripts/registry-scaffold-entry.sh \
  --name demo \
  --version 1.2.3 \
  --target x86_64-unknown-linux-gnu \
  --url https://example.com/demo-1.2.3.tar.gz
```

Behavior:

1. Renders package template output at `packages/<name>.toml` (creates when missing, preserves by default when present).
2. Renders release output at `releases/<name>/<version>.toml`.
3. Validates generated package/release docs before writing.
4. Refuses to overwrite existing release docs unless `--force` is set.

Optional flags:

- `--output-root <dir>` to scaffold outside repo root (useful for tests/dry-runs)
- `--license <value>` and `--homepage <url>` to replace defaults
- `--binary-name <name>` and `--binary-path <path>` to customize executable mapping
- `--force` to overwrite existing output files
