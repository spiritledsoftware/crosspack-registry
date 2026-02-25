#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo_root"

mapfile -t manifests < <("$repo_root/scripts/registry-changed-manifests.sh")

if [[ "${#manifests[@]}" -eq 0 ]]; then
  echo "No manifest changes detected. Running full registry preflight to validate tooling changes."
  mapfile -t manifests < <(REGISTRY_PREFLIGHT_ALL=1 "$repo_root/scripts/registry-changed-manifests.sh")
fi

echo "Running registry preflight on ${#manifests[@]} manifest(s)..."
printf ' - %s\n' "${manifests[@]}"

validate_args=()
if [[ "${REGISTRY_REQUIRE_SIGNATURES:-1}" == "0" ]]; then
  validate_args+=("--allow-missing-signatures")
fi

python3 "$repo_root/scripts/registry-validate.py" "${validate_args[@]}" "${manifests[@]}"
python3 "$repo_root/scripts/registry-smoke-install.py" "${manifests[@]}"

echo "Registry preflight complete."
