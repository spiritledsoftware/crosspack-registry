#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage:
  scripts/registry-scaffold-entry.sh \
    --name <package> \
    --version <version> \
    --target <target-triple> \
    --url <artifact-url> \
    [--output-root <index-dir>] \
    [--license <license>] \
    [--homepage <homepage-url>] \
    [--binary-name <binary-name>] \
    [--binary-path <binary-path>] \
    [--force]
EOF
}

NAME=""
VERSION=""
TARGET=""
URL=""
OUTPUT_ROOT="index"
LICENSE_VALUE="TODO_LICENSE"
HOMEPAGE="TODO_HOMEPAGE"
BINARY_NAME=""
BINARY_PATH="TODO_BINARY_PATH"
FORCE=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --name)
      NAME="$2"
      shift 2
      ;;
    --version)
      VERSION="$2"
      shift 2
      ;;
    --target)
      TARGET="$2"
      shift 2
      ;;
    --url)
      URL="$2"
      shift 2
      ;;
    --output-root)
      OUTPUT_ROOT="$2"
      shift 2
      ;;
    --license)
      LICENSE_VALUE="$2"
      shift 2
      ;;
    --homepage)
      HOMEPAGE="$2"
      shift 2
      ;;
    --binary-name)
      BINARY_NAME="$2"
      shift 2
      ;;
    --binary-path)
      BINARY_PATH="$2"
      shift 2
      ;;
    --force)
      FORCE=1
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown argument: $1" >&2
      usage >&2
      exit 1
      ;;
  esac
done

if [[ -z "$NAME" || -z "$VERSION" || -z "$TARGET" || -z "$URL" ]]; then
  echo "Missing required args: --name --version --target --url" >&2
  usage >&2
  exit 1
fi

if [[ -z "$BINARY_NAME" ]]; then
  BINARY_NAME="$NAME"
fi

ARCHIVE_LINES=""
if [[ "$URL" == *.tar.gz || "$URL" == *.tgz ]]; then
  ARCHIVE_LINES=$'archive = "tar.gz"\nstrip_components = 1\n'
elif [[ "$URL" == *.zip ]]; then
  ARCHIVE_LINES=$'archive = "zip"\nstrip_components = 0\n'
fi

OUT_DIR="${OUTPUT_ROOT%/}/$NAME"
OUT_FILE="$OUT_DIR/$VERSION.toml"
TMP_FILE="$(mktemp)"

if [[ -e "$OUT_FILE" && "$FORCE" -ne 1 ]]; then
  rm -f "$TMP_FILE"
  echo "Refusing to overwrite existing manifest: $OUT_FILE (use --force to overwrite)" >&2
  exit 1
fi

cat > "$TMP_FILE" <<EOF
name = "$NAME"
version = "$VERSION"
license = "$LICENSE_VALUE"
homepage = "$HOMEPAGE"

[source]
url = "TODO_SOURCE_URL"
checksum = "TODO_SOURCE_CHECKSUM"
signature = "TODO_SOURCE_SIGNATURE"

[[artifacts]]
target = "$TARGET"
url = "$URL"
sha256 = "TODO_SHA256"
${ARCHIVE_LINES}
[[artifacts.binaries]]
name = "$BINARY_NAME"
path = "$BINARY_PATH"
EOF

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VALIDATOR="$SCRIPT_DIR/registry-validate-entry.py"

if ! python3 "$VALIDATOR" "$TMP_FILE" >/dev/null; then
  rm -f "$TMP_FILE"
  echo "Validation failed; manifest not written" >&2
  exit 1
fi

mkdir -p "$OUT_DIR"
mv "$TMP_FILE" "$OUT_FILE"

echo "Scaffolded: $OUT_FILE"
