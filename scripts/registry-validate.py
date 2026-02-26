#!/usr/bin/env python3
from __future__ import annotations

import argparse
import re
import sys
import tomllib
from pathlib import Path

SEMVER_RE = re.compile(r"^\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?$")
SHA256_RE = re.compile(r"^[0-9a-fA-F]{64}$")
SIG_RE = re.compile(r"^[0-9a-fA-F]{128}$")
TARGET_RE = re.compile(r"^[A-Za-z0-9._-]+$")


def err(errors: list[str], path: Path, message: str) -> None:
    errors.append(f"{path}: {message}")


def load_manifest(path: Path, errors: list[str]):
    try:
        return tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as exc:
        err(errors, path, f"invalid TOML ({exc})")
    except OSError as exc:
        err(errors, path, f"cannot read file ({exc})")
    return None


def expect_nonempty_str(value, field: str, errors: list[str], path: Path) -> bool:
    if not isinstance(value, str) or not value.strip():
        err(errors, path, f"missing or invalid `{field}` (must be non-empty string)")
        return False
    return True


def validate_manifest(path: Path, errors: list[str], require_signatures: bool) -> None:
    doc = load_manifest(path, errors)
    if doc is None:
        return

    name_ok = expect_nonempty_str(doc.get("name"), "name", errors, path)
    version_ok = expect_nonempty_str(doc.get("version"), "version", errors, path)
    expect_nonempty_str(doc.get("license"), "license", errors, path)
    homepage_ok = expect_nonempty_str(doc.get("homepage"), "homepage", errors, path)

    if version_ok and not SEMVER_RE.fullmatch(doc["version"]):
        err(
            errors,
            path,
            f"invalid `version` format: {doc['version']!r} (expected semver)",
        )

    if homepage_ok and not doc["homepage"].startswith("https://"):
        err(errors, path, "invalid `homepage` (must start with https://)")

    # Ensure file path conventions match metadata.
    if len(path.parts) < 3:
        err(errors, path, "manifest must live under index/<name>/<version>.toml")
    else:
        file_pkg = path.parent.name
        file_ver = path.stem
        if name_ok and file_pkg != doc["name"]:
            err(
                errors,
                path,
                f"package directory `{file_pkg}` does not match `name` `{doc['name']}`",
            )
        if version_ok and file_ver != doc["version"]:
            err(
                errors,
                path,
                f"file version `{file_ver}` does not match `version` `{doc['version']}`",
            )

    artifacts = doc.get("artifacts")
    if not isinstance(artifacts, list) or not artifacts:
        err(errors, path, "missing or invalid `artifacts` (must be a non-empty array)")
        artifacts = []

    for idx, artifact in enumerate(artifacts, start=1):
        prefix = f"artifacts[{idx}]"
        if not isinstance(artifact, dict):
            err(errors, path, f"{prefix} must be a table")
            continue

        target = artifact.get("target")
        url = artifact.get("url")
        sha256 = artifact.get("sha256")

        if not isinstance(target, str) or not TARGET_RE.fullmatch(target):
            err(errors, path, f"{prefix}.target must match {TARGET_RE.pattern}")

        if not isinstance(url, str) or not url.startswith("https://"):
            err(errors, path, f"{prefix}.url must start with https://")

        if not isinstance(sha256, str) or not SHA256_RE.fullmatch(sha256):
            err(errors, path, f"{prefix}.sha256 must be 64 hex characters")

        archive = artifact.get("archive")
        if archive is not None and archive not in {
            "tar.gz",
            "zip",
            "tar.xz",
            "tgz",
            "bin",
        }:
            err(
                errors,
                path,
                f"{prefix}.archive must be one of tar.gz, zip, tar.xz, tgz, bin",
            )

        strip_components = artifact.get("strip_components")
        if strip_components is not None and (
            not isinstance(strip_components, int) or strip_components < 0
        ):
            err(errors, path, f"{prefix}.strip_components must be an integer >= 0")

        binaries = artifact.get("binaries")
        if not isinstance(binaries, list) or not binaries:
            err(errors, path, f"{prefix}.binaries must be a non-empty array")
            continue

        for bidx, binary in enumerate(binaries, start=1):
            bprefix = f"{prefix}.binaries[{bidx}]"
            if not isinstance(binary, dict):
                err(errors, path, f"{bprefix} must be a table")
                continue

            bname = binary.get("name")
            bpath = binary.get("path")
            if not isinstance(bname, str) or not bname.strip():
                err(errors, path, f"{bprefix}.name must be a non-empty string")
            if not isinstance(bpath, str) or not bpath.strip():
                err(errors, path, f"{bprefix}.path must be a non-empty string")
            elif bpath.startswith("/") or ".." in Path(bpath).parts:
                err(
                    errors,
                    path,
                    f"{bprefix}.path must be relative and must not contain '..'",
                )

    if require_signatures:
        sig_path = path.with_suffix(path.suffix + ".sig")
        if not sig_path.exists():
            err(errors, path, f"missing signature sidecar `{sig_path.name}`")
        else:
            try:
                sig_raw = sig_path.read_text(encoding="utf-8").strip()
            except OSError as exc:
                err(errors, path, f"cannot read signature sidecar ({exc})")
            else:
                if not SIG_RE.fullmatch(sig_raw):
                    err(
                        errors,
                        path,
                        f"invalid signature format in `{sig_path.name}` (expected 128 hex characters)",
                    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Validate Crosspack registry manifests"
    )
    parser.add_argument(
        "--allow-missing-signatures",
        action="store_true",
        help="Skip required .toml.sig sidecar checks (for PR pre-merge validation)",
    )
    parser.add_argument("manifests", nargs="+", help="Manifest paths to validate")
    args = parser.parse_args()

    errors: list[str] = []
    manifest_paths = [Path(p) for p in args.manifests]
    for path in manifest_paths:
        validate_manifest(
            path, errors, require_signatures=not args.allow_missing_signatures
        )

    if errors:
        print("Registry manifest validation failed:", file=sys.stderr)
        for entry in errors:
            print(f"  - {entry}", file=sys.stderr)
        return 1

    if args.allow_missing_signatures:
        print(
            f"Validated {len(manifest_paths)} manifest(s): schema, metadata, and checksum checks passed."
        )
    else:
        print(
            f"Validated {len(manifest_paths)} manifest(s): schema, metadata, checksum, and signature format checks passed."
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
