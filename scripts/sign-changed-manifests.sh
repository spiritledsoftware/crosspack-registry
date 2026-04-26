#!/usr/bin/env bash
set -euo pipefail

SIGNING_PRIVATE_KEY_PEM="${SIGNING_PRIVATE_KEY_PEM:?SIGNING_PRIVATE_KEY_PEM is required}"
BEFORE_SHA="${BEFORE_SHA:-}"
AFTER_SHA="${AFTER_SHA:-HEAD}"

if ! command -v openssl >/dev/null 2>&1; then
  echo "openssl is required" >&2
  exit 1
fi
if ! command -v python3 >/dev/null 2>&1; then
  echo "python3 is required" >&2
  exit 1
fi

if [ -z "${BEFORE_SHA}" ] || [ "${BEFORE_SHA}" = "0000000000000000000000000000000000000000" ]; then
  if git rev-parse --verify HEAD~1 >/dev/null 2>&1; then
    BEFORE_SHA="$(git rev-parse HEAD~1)"
  else
    BEFORE_SHA="$(git rev-parse HEAD)"
  fi
fi

mapfile -t changed_manifests < <({
  git diff --name-only "${BEFORE_SHA}" "${AFTER_SHA}" -- 'packages/*.toml' 'packages/*.toml.sig' 'releases/**/*.toml' 'releases/**/*.toml.sig' \
    | while IFS= read -r file; do
        manifest_path="${file%.sig}"
        [ -f "$manifest_path" ] && printf '%s\n' "$manifest_path"
      done
  find packages releases -type f -name '*.toml' ! -name '*.toml.sig' \
    | while IFS= read -r manifest_path; do
        [ -f "${manifest_path}.sig" ] || printf '%s\n' "$manifest_path"
      done
} | sort -u)

if [ "${#changed_manifests[@]}" -eq 0 ]; then
  echo "no changed manifest files detected"
  exit 0
fi

key_file="$(mktemp)"
trap 'rm -f "$key_file"' EXIT
printf '%s' "$SIGNING_PRIVATE_KEY_PEM" > "$key_file"
chmod 600 "$key_file"

for manifest in "${changed_manifests[@]}"; do
  sig_bin="$(mktemp)"
  openssl pkeyutl -sign -rawin -inkey "$key_file" -in "$manifest" -out "$sig_bin"
  python3 - "$sig_bin" "${manifest}.sig" <<'PY'
from pathlib import Path
import sys

Path(sys.argv[2]).write_text(Path(sys.argv[1]).read_bytes().hex() + "\n", encoding="utf-8")
PY
  rm -f "$sig_bin"
  echo "signed ${manifest}"
done
