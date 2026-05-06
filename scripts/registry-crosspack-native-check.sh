#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo_root"

crosspack_root="${CROSSPACK_REPO_ROOT:-}"
if [[ -z "$crosspack_root" ]]; then
  if [[ -f "$repo_root/../Cargo.toml" && -d "$repo_root/../crates/crosspack-cli" ]]; then
    crosspack_root="$(cd "$repo_root/.." && pwd)"
  else
    echo "CROSSPACK_REPO_ROOT is required when the registry is not checked out inside Crosspack" >&2
    exit 1
  fi
fi

if [[ ! -f "$crosspack_root/Cargo.toml" || ! -d "$crosspack_root/crates/crosspack-cli" ]]; then
  echo "CROSSPACK_REPO_ROOT does not point at a Crosspack checkout: $crosspack_root" >&2
  exit 1
fi

for tool in openssl python3 cargo; do
  if ! command -v "$tool" >/dev/null 2>&1; then
    echo "$tool is required" >&2
    exit 1
  fi
done

python3 - <<'PY'
import sys

if sys.version_info < (3, 11):
    raise SystemExit(
        "python3 3.11+ is required for registry native validation "
        f"(found {sys.version_info.major}.{sys.version_info.minor})"
    )
PY

if [[ "$#" -gt 0 ]]; then
  changed_manifests=("$@")
else
  mapfile -t changed_manifests < <(./scripts/registry-changed-manifests.sh)
fi

manifests_json="$(
  printf '%s\n' "${changed_manifests[@]}" \
    | python3 -c 'import json, sys; print(json.dumps([line.strip() for line in sys.stdin if line.strip()]))'
)"

mapfile -t package_names < <(
  MANIFESTS_JSON="$manifests_json" python3 - <<'PY'
import json
import os
import sys

names = set()
for raw in json.loads(os.environ["MANIFESTS_JSON"]):
    path = raw.strip()
    if path.startswith("packages/") and path.endswith(".toml"):
        names.add(path.removeprefix("packages/").removesuffix(".toml"))
    elif path.startswith("releases/") and path.endswith(".toml"):
        parts = path.split("/")
        if len(parts) >= 3:
            names.add(parts[1])

for name in sorted(names):
    print(name)
PY
)

if [[ "${#package_names[@]}" -eq 0 ]]; then
  echo "No changed package or release manifests require Crosspack native validation."
  exit 0
fi

temp_registry="$(mktemp -d)"
temp_prefix="$(mktemp -d)"
key_file="$temp_registry/test-registry-signing.pem"
trap 'rm -rf "$temp_registry" "$temp_prefix"' EXIT

cp -a "$repo_root/." "$temp_registry/"
openssl genpkey -algorithm ed25519 -out "$key_file" >/dev/null 2>&1
openssl pkey -in "$key_file" -pubout -outform DER 2>/dev/null \
  | python3 -c 'import sys; data = sys.stdin.buffer.read(); print(data[-32:].hex())' \
  > "$temp_registry/registry.pub"

while IFS= read -r -d '' manifest; do
  sig_bin="$(mktemp)"
  openssl pkeyutl -sign -rawin -inkey "$key_file" -in "$manifest" -out "$sig_bin"
  python3 - "$sig_bin" "$manifest.sig" <<'PY'
from pathlib import Path
import sys

Path(sys.argv[2]).write_text(
    Path(sys.argv[1]).read_bytes().hex() + "\n",
    encoding="utf-8",
)
PY
  rm -f "$sig_bin"
done < <(find "$temp_registry/packages" "$temp_registry/releases" -type f -name '*.toml' -print0)

targets=()
if [[ -n "${CROSSPACK_NATIVE_TARGETS:-}" ]]; then
  read -r -a targets <<< "$CROSSPACK_NATIVE_TARGETS"
fi

run_crosspack() {
  CROSSPACK_PREFIX="$temp_prefix" cargo run -q -p crosspack-cli --bin crosspack \
    --manifest-path "$crosspack_root/Cargo.toml" \
    -- --registry-root "$temp_registry" "$@"
}

for package_name in "${package_names[@]}"; do
  echo "crosspack info $package_name"
  run_crosspack info "$package_name" >/dev/null

  if [[ "${#targets[@]}" -eq 0 ]]; then
    echo "crosspack install --dry-run $package_name"
    run_crosspack install --dry-run --non-interactive "$package_name" >/dev/null
  else
    for target in "${targets[@]}"; do
      if ! python3 - "$temp_registry/packages/$package_name.toml" "$target" <<'PY'
import sys
import tomllib
from pathlib import Path

path = Path(sys.argv[1])
target = sys.argv[2]
doc = tomllib.loads(path.read_text(encoding="utf-8"))
if any(artifact.get("target") == target for artifact in doc.get("artifacts", [])):
    raise SystemExit(0)
raise SystemExit(1)
PY
      then
        echo "skip $package_name for unavailable target $target"
        continue
      fi
      echo "crosspack install --dry-run --target $target $package_name"
      run_crosspack install --dry-run --non-interactive --target "$target" "$package_name" >/dev/null
    done
  fi
done

echo "Crosspack native registry validation passed for ${#package_names[@]} package(s)."
