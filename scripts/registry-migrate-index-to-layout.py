#!/usr/bin/env python3
from __future__ import annotations

import argparse
import importlib.util
import shutil
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:
    import tomli as tomllib  # type: ignore[no-redef]


PLACEHOLDER_SIGNATURE = "0" * 128 + "\n"


def load_generator_module(script_dir: Path):
    script_path = script_dir / "registry-generate-manifest.py"
    spec = importlib.util.spec_from_file_location(
        "registry_generate_manifest", script_path
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load generator module from {script_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def write_signature_placeholder(path: Path) -> None:
    path.write_text(PLACEHOLDER_SIGNATURE, encoding="utf-8")


def migrate_packages(
    *,
    generator,
    sources_root: Path,
    packages_root: Path,
) -> int:
    written = 0
    for source_path in sorted(sources_root.glob("*.toml")):
        package_path = packages_root / f"{source_path.stem}.toml"
        package_path.parent.mkdir(parents=True, exist_ok=True)
        package_text = generator.generate_package_text(config_path=source_path)
        package_path.write_text(package_text, encoding="utf-8")
        write_signature_placeholder(package_path.with_suffix(".toml.sig"))
        written += 1
    return written


def migrate_releases(
    *,
    generator,
    index_root: Path,
    releases_root: Path,
) -> int:
    written = 0
    for manifest_path in sorted(index_root.glob("*/*.toml")):
        payload = tomllib.loads(manifest_path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise RuntimeError(f"Invalid manifest root table: {manifest_path}")

        name = payload.get("name")
        version = payload.get("version")
        artifacts = payload.get("artifacts")
        if not isinstance(name, str) or not name.strip():
            raise RuntimeError(f"Missing name in {manifest_path}")
        if not isinstance(version, str) or not version.strip():
            raise RuntimeError(f"Missing version in {manifest_path}")
        if not isinstance(artifacts, list) or not artifacts:
            raise RuntimeError(f"Missing artifacts in {manifest_path}")

        release_artifacts: list[dict[str, str]] = []
        for artifact in artifacts:
            if not isinstance(artifact, dict):
                raise RuntimeError(f"Artifact entry must be table in {manifest_path}")

            target = artifact.get("target")
            url = artifact.get("url")
            sha256 = artifact.get("sha256")
            if not isinstance(target, str) or not target.strip():
                raise RuntimeError(f"Missing artifact target in {manifest_path}")
            if not isinstance(url, str) or not url.strip():
                raise RuntimeError(f"Missing artifact url in {manifest_path}")
            if not isinstance(sha256, str) or not sha256.strip():
                raise RuntimeError(f"Missing artifact sha256 in {manifest_path}")

            release_artifacts.append(
                {
                    "target": target,
                    "url": url,
                    "sha256": sha256,
                }
            )

        release_text = generator.render_release_text(
            {
                "name": name,
                "version": version,
                "artifacts": release_artifacts,
            }
        )

        output_path = releases_root / name / f"{version}.toml"
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(release_text, encoding="utf-8")
        write_signature_placeholder(output_path.with_suffix(".toml.sig"))
        written += 1
    return written


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        description="Migrate registry/index manifests into packages/releases layout"
    )
    parser.add_argument(
        "--registry-root",
        type=Path,
        default=Path("."),
        help="Registry repository root",
    )
    parser.add_argument(
        "--delete-index",
        action="store_true",
        help="Delete index/ after successful migration",
    )
    args = parser.parse_args(argv)

    registry_root = args.registry_root.resolve()
    script_dir = registry_root / "scripts"
    generator = load_generator_module(script_dir)

    index_root = registry_root / "index"
    sources_root = registry_root / "registry" / "sources"
    packages_root = registry_root / "packages"
    releases_root = registry_root / "releases"

    if not index_root.is_dir():
        raise RuntimeError(f"Missing index root: {index_root}")
    if not sources_root.is_dir():
        raise RuntimeError(f"Missing source config root: {sources_root}")

    if packages_root.exists():
        shutil.rmtree(packages_root)
    if releases_root.exists():
        shutil.rmtree(releases_root)

    package_count = migrate_packages(
        generator=generator,
        sources_root=sources_root,
        packages_root=packages_root,
    )
    release_count = migrate_releases(
        generator=generator,
        index_root=index_root,
        releases_root=releases_root,
    )

    if args.delete_index:
        shutil.rmtree(index_root)

    print(
        f"Migrated {package_count} package template(s) and {release_count} release manifest(s)"
    )
    if args.delete_index:
        print(f"Deleted legacy index directory: {index_root}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(__import__("sys").argv[1:]))
