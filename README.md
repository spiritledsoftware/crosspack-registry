# crosspack-registry

Official Crosspack registry source.

## Structure

- `registry.pub` — trusted Ed25519 public key (hex-encoded, 32-byte key as 64 hex chars)
- `index/` — package metadata index

## Notes

- Keep signing private key material out of git history.
- Crosspack clients should pin the SHA-256 fingerprint of `registry.pub` bytes.

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
