# crosspack-registry

Official Crosspack registry source.

## Structure

- `registry.pub` — trusted Ed25519 public key (hex-encoded, 32-byte key as 64 hex chars)
- `index/` — package metadata index

## Notes

- Keep signing private key material out of git history.
- Crosspack clients should pin the SHA-256 fingerprint of `registry.pub` bytes.
