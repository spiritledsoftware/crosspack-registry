# crosspack-registry

Official Crosspack registry source.

## Structure

- `registry.pub` — trusted Ed25519 public key (hex-encoded, 32-byte key as 64 hex chars)
- `index/` — package metadata index

## Notes

- Keep signing private key material out of git history.
- Crosspack clients should pin the SHA-256 fingerprint of `registry.pub` bytes.

## Platform Coverage

Current artifact coverage in this registry:

- `crosspack@0.0.4`: linux (`x86_64-unknown-linux-gnu`, `aarch64-unknown-linux-gnu`, `x86_64-unknown-linux-musl`, `aarch64-unknown-linux-musl`), darwin (`x86_64-apple-darwin`, `aarch64-apple-darwin`), windows (`x86_64-pc-windows-msvc`)
- `ripgrep@15.1.0`: linux (`x86_64-unknown-linux-gnu`, `aarch64-unknown-linux-gnu`), darwin (`x86_64-apple-darwin`, `aarch64-apple-darwin`), windows (`x86_64-pc-windows-msvc`, `aarch64-pc-windows-msvc`)
- `fd@10.3.0`: linux (`x86_64-unknown-linux-gnu`, `aarch64-unknown-linux-gnu`), darwin (`x86_64-apple-darwin`, `aarch64-apple-darwin`), windows (`x86_64-pc-windows-msvc`, `aarch64-pc-windows-msvc`)
- `fzf@0.68.0`: linux (`x86_64-unknown-linux-gnu`, `aarch64-unknown-linux-gnu`), darwin (`x86_64-apple-darwin`, `aarch64-apple-darwin`), windows (`x86_64-pc-windows-msvc`, `aarch64-pc-windows-msvc`)
- `jq@1.8.1`: linux (`x86_64-unknown-linux-gnu`, `aarch64-unknown-linux-gnu`), darwin (`x86_64-apple-darwin`, `aarch64-apple-darwin`), windows (`x86_64-pc-windows-msvc`)

Caveat: `crosspack@0.0.3` does not yet publish official `aarch64-pc-windows-msvc` release assets.

## Package Update and Rollback Procedure

When updating package metadata in `index/`:

1. Add or update `<version>.toml` with correct artifact metadata (`url`, `sha256`, `archive`, `strip_components`, `binaries`).
2. Open a PR with changed `index/<package>/<version>.toml` files (sidecars can be omitted in PRs).
3. After merge to `main`, workflow `.github/workflows/sign-manifests-on-merge.yml` generates/updates matching sidecars (`<version>.toml.sig`) automatically.
4. Validate end-to-end from a clean prefix with Crosspack bootstrap + install.
5. Keep validation logs in `logs/` with command output for traceability.

If a published package update must be rolled back:

1. Revert the affected manifest(s) and signature sidecar(s) to the last known-good revision.
2. Re-run signature verification and clean-prefix install validation.
3. Publish the rollback commit and include links to new validation logs in the PR.

## Automation Setup

- Configure repository secret `CROSSPACK_REGISTRY_SIGNING_PRIVATE_KEY_PEM` (Ed25519 private key PEM).
- Ensure workflow permissions allow `contents: write` so generated `.sig` files can be committed back to `main`.

## Registry Preflight (Local + CI)

CI enforces a registry quality gate that validates changed manifests and runs smoke-install checks.

- Schema and required metadata checks for each changed `index/<package>/<version>.toml`
- Checksum + signature format checks (`sha256` fields and matching `.toml.sig` sidecar)
- PR smoke-install matrix on `ubuntu-latest` and `windows-latest` for changed manifests
- Windows package-layout canary via `python scripts/registry-smoke-install.py --app-bundle-canary` (currently validates direct `.exe` layout with `index/jq/1.8.1.toml`)
- Smoke-install path that downloads one artifact per selected manifest, verifies SHA-256, and validates extracted binaries

Run the same checks locally:

```bash
./scripts/registry-preflight.sh
```

Useful variants:

```bash
# Full scan of all manifests (matches push/manual workflow behavior)
REGISTRY_PREFLIGHT_ALL=1 ./scripts/registry-preflight.sh

# Full scan without smoke-install (useful when iterating on validation logic only)
REGISTRY_PREFLIGHT_ALL=1 REGISTRY_PREFLIGHT_SKIP_SMOKE=1 ./scripts/registry-preflight.sh

# Validate only manifests changed from a specific base commit (matches PR workflow behavior)
REGISTRY_BASE_SHA=<base-sha> ./scripts/registry-preflight.sh
```

## Maintainer Scaffolding Workflow

Use the scaffold command to create a new package entry with required fields and placeholder metadata sections:

```bash
scripts/registry-scaffold-entry.sh \
  --name demo \
  --version 1.2.3 \
  --target x86_64-unknown-linux-gnu \
  --url https://example.com/demo-1.2.3.tar.gz
```

Behavior:

1. Renders deterministic TOML output at `index/<name>/<version>.toml`.
2. Auto-populates placeholder metadata for artifact checksum (`sha256`) and source provenance/signature (`[source]` with `url`, `checksum`, `signature` placeholders).
3. Validates the generated manifest before write via `scripts/registry-validate-entry.py`.
4. Aborts without writing if validation fails.

Optional flags:

- `--output-root <dir>` to scaffold outside `index/` (useful for tests/dry runs)
- `--license <value>` and `--homepage <url>` to replace defaults
- `--binary-name <name>` and `--binary-path <path>` to customize executable mapping
- `--force` to overwrite an existing `<version>.toml` (default is safe no-overwrite)

After scaffolding, replace placeholders with real values and then sign the manifest sidecar (`<version>.toml.sig`) as part of the normal publication flow.

Validator runtime note: Python 3.11+ works out of the box (`tomllib`). On Python 3.10, install `tomli` so validation can parse TOML.
