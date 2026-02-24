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

- `crosspack@0.0.3`: linux (`x86_64-unknown-linux-gnu`, `aarch64-unknown-linux-gnu`, `x86_64-unknown-linux-musl`, `aarch64-unknown-linux-musl`), darwin (`x86_64-apple-darwin`, `aarch64-apple-darwin`), windows (`x86_64-pc-windows-msvc`)
- `ripgrep@15.1.0`: linux (`x86_64-unknown-linux-gnu`, `aarch64-unknown-linux-gnu`), darwin (`x86_64-apple-darwin`, `aarch64-apple-darwin`), windows (`x86_64-pc-windows-msvc`, `aarch64-pc-windows-msvc`)
- `fd@10.3.0`: linux (`x86_64-unknown-linux-gnu`, `aarch64-unknown-linux-gnu`), darwin (`x86_64-apple-darwin`, `aarch64-apple-darwin`), windows (`x86_64-pc-windows-msvc`, `aarch64-pc-windows-msvc`)
- `fzf@0.68.0`: linux (`x86_64-unknown-linux-gnu`, `aarch64-unknown-linux-gnu`), darwin (`x86_64-apple-darwin`, `aarch64-apple-darwin`), windows (`x86_64-pc-windows-msvc`, `aarch64-pc-windows-msvc`)
- `jq@1.8.1`: linux (`x86_64-unknown-linux-gnu`, `aarch64-unknown-linux-gnu`), darwin (`x86_64-apple-darwin`, `aarch64-apple-darwin`), windows (`x86_64-pc-windows-msvc`)

Caveat: `crosspack@0.0.3` does not yet publish official `aarch64-pc-windows-msvc` release assets.

## Package Update and Rollback Procedure

When updating package metadata in `index/`:

1. Add or update `<version>.toml` with correct artifact metadata (`url`, `sha256`, `archive`, `strip_components`, `binaries`).
2. Re-sign every changed manifest with the registry private key and commit matching sidecars (`<version>.toml.sig`).
3. Validate end-to-end from a clean prefix with Crosspack bootstrap + install.
4. Keep validation logs in `logs/` with command output for traceability.

If a published package update must be rolled back:

1. Revert the affected manifest(s) and signature sidecar(s) to the last known-good revision.
2. Re-run signature verification and clean-prefix install validation.
3. Publish the rollback commit and include links to new validation logs in the PR.

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

After scaffolding, replace placeholders with real values and then sign the manifest sidecar (`<version>.toml.sig`) as part of the normal publication flow.
