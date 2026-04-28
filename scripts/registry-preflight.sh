#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo_root"

manifests=()
load_manifests() {
  manifests=()
  while IFS= read -r manifest; do
    [[ -n "$manifest" ]] || continue
    manifests+=("$manifest")
  done < <("$@")
}

load_manifests "$repo_root/scripts/registry-changed-manifests.sh"

if [[ "${#manifests[@]}" -eq 0 ]]; then
  echo "No manifest changes detected. Skipping registry preflight."
  exit 0
fi

echo "Running registry preflight on ${#manifests[@]} manifest(s)..."
printf ' - %s\n' "${manifests[@]}"

validate_args=()
if [[ "${REGISTRY_REQUIRE_SIGNATURES:-1}" == "0" ]]; then
  validate_args+=("--allow-missing-signatures")
fi

python3 "$repo_root/scripts/registry-validate.py" "${validate_args[@]}" "${manifests[@]}"

if [[ "${REGISTRY_PREFLIGHT_SKIP_SMOKE:-0}" != "1" ]]; then
  release_manifests=()
  for manifest in "${manifests[@]}"; do
    if [[ "$manifest" == releases/*/*.toml ]]; then
      release_manifests+=("$manifest")
    fi
  done

  if [[ "${#release_manifests[@]}" -gt 0 ]]; then
    python3 "$repo_root/scripts/registry-smoke-install.py" "${release_manifests[@]}"
  else
    echo "Skipping smoke-install checks (no release manifests selected)."
  fi
else
  echo "Skipping smoke-install checks (REGISTRY_PREFLIGHT_SKIP_SMOKE=1)."
fi

echo "Registry preflight complete."
