#!/usr/bin/env bash
set -euo pipefail

SIGNING_PRIVATE_KEY_PEM="${SIGNING_PRIVATE_KEY_PEM:?SIGNING_PRIVATE_KEY_PEM is required}"
BEFORE_SHA="${BEFORE_SHA:-}"
AFTER_SHA="${AFTER_SHA:-HEAD}"

if ! command -v openssl >/dev/null 2>&1; then
  echo "openssl is required" >&2
  exit 1
fi
if ! command -v xxd >/dev/null 2>&1; then
  echo "xxd is required" >&2
  exit 1
fi

if [ -z "${BEFORE_SHA}" ] || [ "${BEFORE_SHA}" = "0000000000000000000000000000000000000000" ]; then
  if git rev-parse --verify HEAD~1 >/dev/null 2>&1; then
    BEFORE_SHA="$(git rev-parse HEAD~1)"
  else
    BEFORE_SHA="$(git rev-parse HEAD)"
  fi
fi

mapfile -t changed_manifests < <(
  git diff --name-only "${BEFORE_SHA}" "${AFTER_SHA}" -- 'index/**/*.toml' 'index/**/*.toml.sig' \
    | while IFS= read -r file; do
        manifest_path="${file%.sig}"
        [ -f "$manifest_path" ] && printf '%s\n' "$manifest_path"
      done | sort -u
)

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
  xxd -p -c 9999 "$sig_bin" | tr -d '\n' > "${manifest}.sig"
  printf '\n' >> "${manifest}.sig"
  rm -f "$sig_bin"
  echo "signed ${manifest}"
done
