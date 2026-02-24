#!/usr/bin/env python3
"""Validate a registry entry TOML against crosspack registry schema."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

try:
    import tomllib  # Python 3.11+
except ModuleNotFoundError:  # pragma: no cover - exercised on Python 3.10
    try:
        import tomli as tomllib  # type: ignore[no-redef]
    except ModuleNotFoundError as exc:  # pragma: no cover
        print(
            "Validation failed: Python 3.11+ required (or install tomli for Python 3.10)",
            file=sys.stderr,
        )
        raise SystemExit(1) from exc


class ValidationError(Exception):
    pass


def _expect_non_empty_str(obj: dict, key: str, ctx: str) -> str:
    value = obj.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValidationError(f"{ctx}.{key} must be a non-empty string")
    return value


def _expect_int(obj: dict, key: str, ctx: str) -> int:
    value = obj.get(key)
    if not isinstance(value, int):
        raise ValidationError(f"{ctx}.{key} must be an integer")
    return value


def validate_manifest(manifest: dict) -> None:
    _expect_non_empty_str(manifest, "name", "manifest")
    _expect_non_empty_str(manifest, "version", "manifest")
    _expect_non_empty_str(manifest, "license", "manifest")
    _expect_non_empty_str(manifest, "homepage", "manifest")

    source = manifest.get("source")
    if source is not None:
        if not isinstance(source, dict):
            raise ValidationError("manifest.source must be a table")
        _expect_non_empty_str(source, "url", "manifest.source")
        _expect_non_empty_str(source, "checksum", "manifest.source")
        _expect_non_empty_str(source, "signature", "manifest.source")

    artifacts = manifest.get("artifacts")
    if not isinstance(artifacts, list) or not artifacts:
        raise ValidationError("manifest.artifacts must be a non-empty array")

    for idx, artifact in enumerate(artifacts):
        if not isinstance(artifact, dict):
            raise ValidationError(f"manifest.artifacts[{idx}] must be a table")
        prefix = f"manifest.artifacts[{idx}]"
        _expect_non_empty_str(artifact, "target", prefix)
        _expect_non_empty_str(artifact, "url", prefix)
        _expect_non_empty_str(artifact, "sha256", prefix)

        if "archive" in artifact:
            archive = artifact["archive"]
            if archive not in ("tar.gz", "zip"):
                raise ValidationError(f"{prefix}.archive must be 'tar.gz' or 'zip'")

        if "strip_components" in artifact:
            strip = _expect_int(artifact, "strip_components", prefix)
            if strip < 0:
                raise ValidationError(f"{prefix}.strip_components must be >= 0")

        binaries = artifact.get("binaries")
        if not isinstance(binaries, list) or not binaries:
            raise ValidationError(f"{prefix}.binaries must be a non-empty array")
        for bidx, binary in enumerate(binaries):
            if not isinstance(binary, dict):
                raise ValidationError(f"{prefix}.binaries[{bidx}] must be a table")
            bprefix = f"{prefix}.binaries[{bidx}]"
            _expect_non_empty_str(binary, "name", bprefix)
            _expect_non_empty_str(binary, "path", bprefix)


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Validate crosspack registry entry TOML")
    parser.add_argument("manifest", type=Path, help="Path to <version>.toml")
    args = parser.parse_args(argv)

    try:
        content = args.manifest.read_bytes()
        manifest = tomllib.loads(content.decode("utf-8"))
        if not isinstance(manifest, dict):
            raise ValidationError("manifest root must be a table")
        validate_manifest(manifest)
    except FileNotFoundError:
        print(f"Validation failed: file not found: {args.manifest}", file=sys.stderr)
        return 1
    except tomllib.TOMLDecodeError as exc:
        print(f"Validation failed: invalid TOML: {exc}", file=sys.stderr)
        return 1
    except ValidationError as exc:
        print(f"Validation failed: {exc}", file=sys.stderr)
        return 1

    print(f"Validation passed: {args.manifest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
