#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo_root"

if [[ "${REGISTRY_PREFLIGHT_ALL:-0}" == "1" ]]; then
  find index -type f -name '*.toml' | sort
  exit 0
fi

manifest_paths() {
  grep -E '^index/.+\.toml(\.sig)?$' \
    | sed 's/\.sig$//' \
    | sort -u
}

if [[ -n "${REGISTRY_BASE_SHA:-}" ]] && git rev-parse --verify "$REGISTRY_BASE_SHA" >/dev/null 2>&1; then
  git diff --name-only "$REGISTRY_BASE_SHA"...HEAD -- index \
    | manifest_paths || true
  exit 0
fi

git diff --name-only HEAD -- index \
  | manifest_paths || true
