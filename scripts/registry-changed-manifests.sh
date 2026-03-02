#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo_root"

if [[ "${REGISTRY_PREFLIGHT_ALL:-0}" == "1" ]]; then
  python3 - <<'PY'
from pathlib import Path

roots = [Path("packages"), Path("releases")]
paths = []
for root in roots:
    if not root.exists():
        continue
    for path in root.rglob("*.toml"):
        if path.is_file():
            paths.append(path.as_posix())

for path in sorted(paths):
    print(path)
PY
  exit 0
fi

manifest_paths() {
  python3 - <<'PY'
import sys

seen = set()
ordered = []
for raw in sys.stdin:
    path = raw.strip()
    if not path:
        continue

    if path.startswith("packages/"):
        candidate = path[:-4] if path.endswith(".sig") else path
        if candidate.count("/") == 1 and candidate.endswith(".toml"):
            if candidate not in seen:
                seen.add(candidate)
                ordered.append(candidate)
        continue

    if path.startswith("releases/"):
        candidate = path[:-4] if path.endswith(".sig") else path
        parts = candidate.split("/")
        if len(parts) == 3 and parts[2].endswith(".toml"):
            if candidate not in seen:
                seen.add(candidate)
                ordered.append(candidate)

for candidate in sorted(ordered):
    print(candidate)
PY
}

if [[ -n "${REGISTRY_BASE_SHA:-}" ]] && git rev-parse --verify "$REGISTRY_BASE_SHA" >/dev/null 2>&1; then
  git diff --name-only "$REGISTRY_BASE_SHA"...HEAD -- packages releases | manifest_paths || true
  exit 0
fi

git diff --name-only HEAD -- packages releases | manifest_paths || true
