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
    [--output-root <registry-root>] \
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
OUTPUT_ROOT="."
LICENSE_VALUE="TODO_LICENSE"
HOMEPAGE="https://example.invalid/TODO_HOMEPAGE"
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

url_path="$URL"
url_path="${url_path%%\?*}"
url_path="${url_path%%\#*}"
asset_name="${url_path##*/}"
if [[ -z "$asset_name" || "$asset_name" == "$url_path" ]]; then
  asset_name="TODO_ASSET_NAME"
fi
asset_template="${asset_name/$VERSION/\{version\}}"

archive_lines=""
if [[ "$asset_name" == *.tar.gz || "$asset_name" == *.tgz ]]; then
  archive_lines=$'archive = "tar.gz"\nstrip_components = 1\n'
elif [[ "$asset_name" == *.zip ]]; then
  archive_lines=$'archive = "zip"\nstrip_components = 0\n'
fi

package_out="${OUTPUT_ROOT%/}/packages/${NAME}.toml"
release_dir="${OUTPUT_ROOT%/}/releases/${NAME}"
release_out="${release_dir}/${VERSION}.toml"

if [[ -e "$release_out" && "$FORCE" -ne 1 ]]; then
  echo "Refusing to overwrite existing release manifest: $release_out (use --force to overwrite)" >&2
  exit 1
fi

tmp_root="$(mktemp -d)"
tmp_package="$tmp_root/packages/${NAME}.toml"
tmp_release="$tmp_root/releases/${NAME}/${VERSION}.toml"
mkdir -p "$(dirname "$tmp_package")" "$(dirname "$tmp_release")"

cleanup() {
  rm -rf "$tmp_root"
}
trap cleanup EXIT

cat > "$tmp_package" <<EOF
name = "$NAME"
license = "$LICENSE_VALUE"
homepage = "$HOMEPAGE"

[source]
provider = "github"
repo = "TODO_OWNER/TODO_REPO"
include_prereleases = false

[[artifacts]]
target = "$TARGET"
asset = "$asset_template"
${archive_lines}[[artifacts.binaries]]
name = "$BINARY_NAME"
path = "$BINARY_PATH"
EOF

cat > "$tmp_release" <<EOF
name = "$NAME"
version = "$VERSION"

[[artifacts]]
target = "$TARGET"
url = "$URL"
sha256 = "0000000000000000000000000000000000000000000000000000000000000000"
EOF

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VALIDATOR="$SCRIPT_DIR/registry-validate.py"

package_validation_path="$tmp_package"
write_package=1
if [[ -e "$package_out" && "$FORCE" -ne 1 ]]; then
  package_validation_path="$package_out"
  write_package=0
fi

if ! python3 "$VALIDATOR" --allow-missing-signatures "$package_validation_path" "$tmp_release" >/dev/null; then
  echo "Validation failed; manifests not written" >&2
  exit 1
fi

mkdir -p "$release_dir"
mv "$tmp_release" "$release_out"

if [[ "$write_package" -eq 1 ]]; then
  mkdir -p "$(dirname "$package_out")"
  cp "$tmp_package" "$package_out"
  echo "Scaffolded package template: $package_out"
else
  echo "Using existing package template: $package_out"
fi

echo "Scaffolded release manifest: $release_out"
