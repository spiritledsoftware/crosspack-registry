#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:
    import tomli as tomllib  # type: ignore[no-redef]


class ValidationError(Exception):
    pass


ALLOWED_CATEGORIES = {
    "cli",
    "developer-tool",
    "database",
    "desktop-app",
    "editor",
    "runtime",
    "shell",
    "utility",
}


def _load_toml(path: Path) -> dict:
    try:
        doc = tomllib.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ValidationError(f"{path}: file not found") from exc
    except tomllib.TOMLDecodeError as exc:
        raise ValidationError(f"{path}: invalid TOML ({exc})") from exc
    if not isinstance(doc, dict):
        raise ValidationError(f"{path}: manifest root must be a table")
    return doc


def _expect_non_empty_string(obj: dict, key: str, ctx: str) -> str:
    value = obj.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValidationError(f"{ctx}.{key} must be a non-empty string")
    return value.strip()


def _package_names(root: Path) -> set[str]:
    if not root.is_dir():
        raise ValidationError(f"{root}: packages root not found")
    return {path.stem for path in root.glob("*.toml") if path.is_file()}


def _source_names(root: Path) -> set[str]:
    if not root.is_dir():
        raise ValidationError(f"{root}: sources root not found")
    return {path.stem for path in root.glob("*.toml") if path.is_file()}


def _release_names(root: Path) -> set[str]:
    if not root.is_dir():
        raise ValidationError(f"{root}: releases root not found")
    names: set[str] = set()
    for pkg_dir in root.iterdir():
        if not pkg_dir.is_dir():
            continue
        if any(path.is_file() and path.suffix == ".toml" for path in pkg_dir.glob("*.toml")):
            names.add(pkg_dir.name)
    return names


def validate_seed_definitions(
    doc: dict,
    *,
    packages_root: Path,
    sources_root: Path,
    releases_root: Path,
) -> int:
    seeds = doc.get("seeds")
    if not isinstance(seeds, list) or not seeds:
        raise ValidationError("seed-definitions.seeds must be a non-empty array")

    seen_packages: set[str] = set()
    for idx, seed in enumerate(seeds):
        if not isinstance(seed, dict):
            raise ValidationError(f"seed-definitions.seeds[{idx}] must be a table")
        ctx = f"seed-definitions.seeds[{idx}]"
        package = _expect_non_empty_string(seed, "package", ctx)
        if package in seen_packages:
            raise ValidationError(f"{ctx}.package duplicates package '{package}'")
        seen_packages.add(package)

        category = _expect_non_empty_string(seed, "category", ctx)
        if category not in ALLOWED_CATEGORIES:
            allowed = ", ".join(sorted(ALLOWED_CATEGORIES))
            raise ValidationError(f"{ctx}.category must be one of: {allowed}")

        _expect_non_empty_string(seed, "rationale", ctx)
        _expect_non_empty_string(seed, "review_notes", ctx)

    package_names = _package_names(packages_root)
    source_names = _source_names(sources_root)
    release_names = _release_names(releases_root)

    missing_seed_definitions = sorted(package_names - seen_packages)
    if missing_seed_definitions:
        raise ValidationError(
            "missing seed definitions for package template(s): "
            + ", ".join(missing_seed_definitions)
        )

    unknown_seed_packages = sorted(seen_packages - package_names)
    if unknown_seed_packages:
        raise ValidationError(
            "seed definitions reference unknown package template(s): "
            + ", ".join(unknown_seed_packages)
        )

    missing_sources = sorted(seen_packages - source_names)
    if missing_sources:
        raise ValidationError(
            "missing source configs for seed package(s): " + ", ".join(missing_sources)
        )

    missing_releases = sorted(seen_packages - release_names)
    if missing_releases:
        raise ValidationError(
            "missing release manifests for seed package(s): " + ", ".join(missing_releases)
        )

    return len(seeds)


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Validate Crosspack seed definitions")
    parser.add_argument(
        "seed_definitions",
        type=Path,
        help="Path to registry/seed-definitions.toml",
    )
    parser.add_argument(
        "--packages-root",
        type=Path,
        default=Path("packages"),
        help="Package template root",
    )
    parser.add_argument(
        "--sources-root",
        type=Path,
        default=Path("registry") / "sources",
        help="Source config root",
    )
    parser.add_argument(
        "--releases-root",
        type=Path,
        default=Path("releases"),
        help="Release manifest root",
    )
    args = parser.parse_args(argv)

    try:
        count = validate_seed_definitions(
            _load_toml(args.seed_definitions),
            packages_root=args.packages_root,
            sources_root=args.sources_root,
            releases_root=args.releases_root,
        )
    except ValidationError as exc:
        print("Seed definition validation failed:", file=sys.stderr)
        print(f"  - {exc}", file=sys.stderr)
        return 1

    print(f"Seed definition validation passed: {count} seed package(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
