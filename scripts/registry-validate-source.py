#!/usr/bin/env python3
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:
    import tomli as tomllib  # type: ignore[no-redef]


class ValidationError(Exception):
    pass


ARCHIVE_VALUES = {"tar.gz", "zip", "tar.xz", "tgz", "bin"}
TARGET_RE = re.compile(r"^[A-Za-z0-9._-]+$")
REPO_RE = re.compile(r"^[^/\s]+/[^/\s]+$")


def _expect_non_empty_str(obj: dict, key: str, ctx: str) -> str:
    value = obj.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValidationError(f"{ctx}.{key} must be a non-empty string")
    return value


def _expect_optional_non_negative_int(obj: dict, key: str, ctx: str) -> None:
    if key not in obj:
        return
    value = obj[key]
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValidationError(f"{ctx}.{key} must be an integer >= 0")


def validate_source_config(doc: dict) -> None:
    _expect_non_empty_str(doc, "name", "manifest")
    _expect_non_empty_str(doc, "license", "manifest")
    homepage = _expect_non_empty_str(doc, "homepage", "manifest")
    if not homepage.startswith("https://"):
        raise ValidationError("manifest.homepage must start with https://")

    source = doc.get("source")
    if not isinstance(source, dict):
        raise ValidationError("manifest.source must be a table")

    provider = _expect_non_empty_str(source, "provider", "manifest.source")
    if provider != "github":
        raise ValidationError("manifest.source.provider must be 'github'")

    repo = _expect_non_empty_str(source, "repo", "manifest.source")
    if not REPO_RE.fullmatch(repo):
        raise ValidationError("manifest.source.repo must look like owner/name")

    include_prereleases = source.get("include_prereleases")
    if include_prereleases is not None and not isinstance(include_prereleases, bool):
        raise ValidationError("manifest.source.include_prereleases must be a boolean")

    tag_prefix = source.get("tag_prefix")
    if tag_prefix is not None and not isinstance(tag_prefix, str):
        raise ValidationError("manifest.source.tag_prefix must be a string")

    artifacts = doc.get("artifacts")
    if not isinstance(artifacts, list) or not artifacts:
        raise ValidationError("manifest.artifacts must be a non-empty array")

    for idx, artifact in enumerate(artifacts):
        if not isinstance(artifact, dict):
            raise ValidationError(f"manifest.artifacts[{idx}] must be a table")

        prefix = f"manifest.artifacts[{idx}]"
        target = _expect_non_empty_str(artifact, "target", prefix)
        if not TARGET_RE.fullmatch(target):
            raise ValidationError(f"{prefix}.target has invalid characters")

        _expect_non_empty_str(artifact, "asset", prefix)

        archive = artifact.get("archive")
        if archive is not None and archive not in ARCHIVE_VALUES:
            raise ValidationError(
                f"{prefix}.archive must be one of {', '.join(sorted(ARCHIVE_VALUES))}"
            )

        _expect_optional_non_negative_int(artifact, "strip_components", prefix)

        binaries = artifact.get("binaries")
        if not isinstance(binaries, list) or not binaries:
            raise ValidationError(f"{prefix}.binaries must be a non-empty array")

        for bidx, binary in enumerate(binaries):
            if not isinstance(binary, dict):
                raise ValidationError(f"{prefix}.binaries[{bidx}] must be a table")
            bprefix = f"{prefix}.binaries[{bidx}]"
            _expect_non_empty_str(binary, "name", bprefix)
            _expect_non_empty_str(binary, "path", bprefix)

        completions = artifact.get("completions", [])
        if not isinstance(completions, list):
            raise ValidationError(f"{prefix}.completions must be an array when present")
        for cidx, completion in enumerate(completions):
            if not isinstance(completion, dict):
                raise ValidationError(f"{prefix}.completions[{cidx}] must be a table")
            cprefix = f"{prefix}.completions[{cidx}]"
            _expect_non_empty_str(completion, "shell", cprefix)
            _expect_non_empty_str(completion, "path", cprefix)

        gui_apps = artifact.get("gui_apps", [])
        if not isinstance(gui_apps, list):
            raise ValidationError(f"{prefix}.gui_apps must be an array when present")
        for gidx, gui_app in enumerate(gui_apps):
            if not isinstance(gui_app, dict):
                raise ValidationError(f"{prefix}.gui_apps[{gidx}] must be a table")
            gprefix = f"{prefix}.gui_apps[{gidx}]"
            _expect_non_empty_str(gui_app, "app_id", gprefix)
            _expect_non_empty_str(gui_app, "display_name", gprefix)
            _expect_non_empty_str(gui_app, "exec", gprefix)
            categories = gui_app.get("categories")
            if not isinstance(categories, list) or not categories:
                raise ValidationError(f"{gprefix}.categories must be a non-empty array")
            for cat_idx, category in enumerate(categories):
                if not isinstance(category, str) or not category.strip():
                    raise ValidationError(
                        f"{gprefix}.categories[{cat_idx}] must be a non-empty string"
                    )


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        description="Validate registry source configuration"
    )
    parser.add_argument("configs", nargs="+", type=Path, help="registry/sources/*.toml")
    parser.add_argument(
        "--require-package-coverage",
        action="store_true",
        help="Fail if any packages/*.toml template lacks a matching source config",
    )
    parser.add_argument(
        "--packages-root",
        type=Path,
        default=Path("packages"),
        help="Package template root used with --require-package-coverage",
    )
    args = parser.parse_args(argv)

    errors: list[str] = []
    for path in args.configs:
        try:
            doc = tomllib.loads(path.read_text(encoding="utf-8"))
            if not isinstance(doc, dict):
                raise ValidationError("manifest root must be a table")
            validate_source_config(doc)
        except FileNotFoundError:
            errors.append(f"{path}: file not found")
        except tomllib.TOMLDecodeError as exc:
            errors.append(f"{path}: invalid TOML ({exc})")
        except ValidationError as exc:
            errors.append(f"{path}: {exc}")

    if args.require_package_coverage:
        if not args.packages_root.is_dir():
            errors.append(f"{args.packages_root}: packages root not found")
        else:
            source_packages = {path.stem for path in args.configs}
            package_templates = {
                path.stem
                for path in args.packages_root.glob("*.toml")
                if path.is_file() and path.suffix == ".toml"
            }
            missing = sorted(package_templates - source_packages)
            if missing:
                errors.append(
                    "package coverage failed: missing source config(s) for "
                    + ", ".join(missing)
                )

    if errors:
        print("Source config validation failed:", file=sys.stderr)
        for error in errors:
            print(f"  - {error}", file=sys.stderr)
        return 1

    print(f"Validation passed: {len(args.configs)} source config(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
