#!/usr/bin/env python3
from __future__ import annotations

import argparse
import importlib.util
import json
import re
import subprocess
import sys
import urllib.request
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:
    import tomli as tomllib  # type: ignore[no-redef]


SEMVER_RE = re.compile(r"^\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?$")


class PlannedUpdate:
    def __init__(self, package: str, version: str, config_path: Path, release: dict):
        self.package = package
        self.version = version
        self.config_path = config_path
        self.release = release


def _load_generator_module(repo_root: Path):
    script_path = repo_root / "scripts" / "registry-generate-manifest.py"
    spec = importlib.util.spec_from_file_location(
        "registry_generate_manifest", script_path
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load module from {script_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _http_get_json(url: str, token: str | None = None) -> object:
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "crosspack-registry-upstream-bot",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310
        return json.loads(response.read().decode("utf-8"))


def fetch_github_releases(repo: str, token: str | None = None) -> list[dict]:
    url = f"https://api.github.com/repos/{repo}/releases?per_page=20"
    payload = _http_get_json(url, token=token)
    if not isinstance(payload, list):
        raise RuntimeError(f"Unexpected releases response for {repo}")
    return [item for item in payload if isinstance(item, dict)]


def normalize_tag_to_version(tag: str, tag_prefix: str | None = None) -> str | None:
    if not tag:
        return None
    value = tag.strip()
    if tag_prefix:
        if not value.startswith(tag_prefix):
            return None
        value = value[len(tag_prefix) :]
    elif value.startswith("v"):
        value = value[1:]

    if SEMVER_RE.fullmatch(value):
        return value
    return None


def load_config(path: Path) -> dict:
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise RuntimeError(f"Invalid config root: {path}")
    return data


def plan_updates_for_config(
    *, config_path: Path, releases_root: Path, releases: list[dict]
) -> list[PlannedUpdate]:
    config = load_config(config_path)
    package = str(config["name"])
    source = config.get("source", {})
    if not isinstance(source, dict):
        raise RuntimeError(f"source must be a table in {config_path}")

    include_prereleases = bool(source.get("include_prereleases", False))
    tag_prefix = source.get("tag_prefix")
    if tag_prefix is not None and not isinstance(tag_prefix, str):
        raise RuntimeError(f"tag_prefix must be a string in {config_path}")

    existing_versions = {
        p.stem for p in (releases_root / package).glob("*.toml") if p.is_file()
    }

    for release in releases:
        if release.get("draft") is True:
            continue
        if release.get("prerelease") is True and not include_prereleases:
            continue

        tag_name = release.get("tag_name")
        if not isinstance(tag_name, str):
            continue

        version = normalize_tag_to_version(tag_name, tag_prefix)
        if version is None:
            continue
        if version in existing_versions:
            return []

        return [
            PlannedUpdate(
                package=package,
                version=version,
                config_path=config_path,
                release=release,
            )
        ]
    return []


def _run(cmd: list[str], *, cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, cwd=cwd, check=True, text=True, capture_output=True)


def _stash_paths_for_branch_switch(*, repo_root: Path, paths: list[Path]) -> str | None:
    if not paths:
        return None

    result = subprocess.run(
        [
            "git",
            "stash",
            "push",
            "--include-untracked",
            "--message",
            "upstream-release-bot branch switch",
            "--",
            *(str(path) for path in paths),
        ],
        cwd=repo_root,
        check=True,
        text=True,
        capture_output=True,
    )
    output = f"{result.stdout}\n{result.stderr}"
    if "No local changes to save" in output:
        return None

    return "stash@{0}"


def _restore_stashed_paths(*, repo_root: Path, stash_ref: str, paths: list[Path]) -> None:
    for path in paths:
        untracked_entry = subprocess.run(
            ["git", "cat-file", "-e", f"{stash_ref}^3:{path}"],
            cwd=repo_root,
            check=False,
            text=True,
            capture_output=True,
        )
        source = f"{stash_ref}^3" if untracked_entry.returncode == 0 else stash_ref
        _run(["git", "checkout", source, "--", str(path)], cwd=repo_root)
    _run(["git", "stash", "drop", stash_ref], cwd=repo_root)


def _open_or_update_pr(
    *,
    repo_root: Path,
    staged_paths: list[Path],
    package: str,
    version: str,
    base_branch: str,
    branch_prefix: str,
) -> None:
    branch_name = f"{branch_prefix}/{package}/{version}"
    title = f"chore(registry): add {package} {version}"
    body = (
        "## Summary\n"
        f"- add generated package/release metadata for `{package}` `{version}`\n"
        "- produced by upstream release bot\n"
    )

    _run(["git", "checkout", base_branch], cwd=repo_root)
    remote_branch = _run(
        ["git", "ls-remote", "--heads", "origin", branch_name],
        cwd=repo_root,
    )
    stash_ref: str | None = None
    if remote_branch.stdout.strip():
        stash_ref = _stash_paths_for_branch_switch(
            repo_root=repo_root,
            paths=staged_paths,
        )
        _run(["git", "fetch", "origin", branch_name], cwd=repo_root)
        _run(
            ["git", "switch", "-C", branch_name, f"origin/{branch_name}"],
            cwd=repo_root,
        )
        if stash_ref is not None:
            _restore_stashed_paths(
                repo_root=repo_root,
                stash_ref=stash_ref,
                paths=staged_paths,
            )
    else:
        _run(["git", "switch", "-C", branch_name, base_branch], cwd=repo_root)
    if staged_paths:
        _run(["git", "add", *(str(path) for path in staged_paths)], cwd=repo_root)
    staged = _run(["git", "diff", "--cached", "--name-only"], cwd=repo_root)
    if not staged.stdout.strip():
        return

    _run(["git", "commit", "-m", title], cwd=repo_root)
    _run(["git", "push", "-u", "origin", branch_name], cwd=repo_root)

    existing = _run(
        [
            "gh",
            "pr",
            "list",
            "--head",
            branch_name,
            "--state",
            "open",
            "--json",
            "number",
        ],
        cwd=repo_root,
    )
    found = json.loads(existing.stdout)
    if isinstance(found, list) and found:
        print(f"PR already open for {branch_name}")
        return

    _run(
        [
            "gh",
            "pr",
            "create",
            "--base",
            base_branch,
            "--head",
            branch_name,
            "--title",
            title,
            "--body",
            body,
        ],
        cwd=repo_root,
    )


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        description="Generate/update manifests from upstream releases"
    )
    parser.add_argument(
        "--sources-root",
        type=Path,
        default=Path("registry/sources"),
        help="Path containing source configs",
    )
    parser.add_argument(
        "--packages-root",
        type=Path,
        default=Path("packages"),
        help="Registry package template directory",
    )
    parser.add_argument(
        "--releases-root",
        type=Path,
        default=Path("releases"),
        help="Registry release manifest directory",
    )
    parser.add_argument("--package", action="append", help="Limit to package name")
    parser.add_argument(
        "--dry-run", action="store_true", help="Print actions without writing"
    )
    parser.add_argument(
        "--create-prs",
        action="store_true",
        help="Commit changes and open PRs with gh",
    )
    parser.add_argument("--base-branch", default="main")
    parser.add_argument("--branch-prefix", default="upstream-release")
    args = parser.parse_args(argv)

    repo_root = Path.cwd()
    generator = _load_generator_module(repo_root)

    package_filter = set(args.package or [])
    config_paths = sorted(args.sources_root.glob("*.toml"))
    if package_filter:
        config_paths = [p for p in config_paths if p.stem in package_filter]

    if not config_paths:
        print("No source configs selected")
        return 0

    github_token = __import__("os").environ.get("GITHUB_TOKEN")
    planned: list[PlannedUpdate] = []
    for config_path in config_paths:
        config = load_config(config_path)
        source = config.get("source")
        if not isinstance(source, dict):
            raise RuntimeError(f"Missing source table in {config_path}")
        repo = source.get("repo")
        if not isinstance(repo, str):
            raise RuntimeError(f"Missing source.repo in {config_path}")

        releases = fetch_github_releases(repo, token=github_token)
        planned.extend(
            plan_updates_for_config(
                config_path=config_path,
                releases_root=args.releases_root,
                releases=releases,
            )
        )

    if not planned:
        print("No new releases detected")
        return 0

    created_releases = 0
    written_packages = 0
    for update in planned:
        package_path = args.packages_root / f"{update.package}.toml"
        release_path = args.releases_root / update.package / f"{update.version}.toml"
        if release_path.exists():
            continue

        package_text = generator.generate_package_text(config_path=update.config_path)
        if args.dry_run:
            if not package_path.exists():
                print(f"[dry-run] would generate {package_path}")
            print(f"[dry-run] would generate {release_path}")
            created_releases += 1
            continue

        staged_paths: list[Path] = []
        current_package_text = (
            package_path.read_text(encoding="utf-8") if package_path.exists() else None
        )
        if current_package_text != package_text:
            package_path.parent.mkdir(parents=True, exist_ok=True)
            package_path.write_text(package_text, encoding="utf-8")
            print(f"Generated {package_path}")
            written_packages += 1
            staged_paths.append(package_path)

        release_text = generator.generate_release_text(
            config_path=update.config_path,
            version=update.version,
            release=update.release,
        )
        release_path.parent.mkdir(parents=True, exist_ok=True)
        release_path.write_text(release_text, encoding="utf-8")
        print(f"Generated {release_path}")
        created_releases += 1
        staged_paths.append(release_path)

        if args.create_prs:
            _open_or_update_pr(
                repo_root=repo_root,
                staged_paths=staged_paths,
                package=update.package,
                version=update.version,
                base_branch=args.base_branch,
                branch_prefix=args.branch_prefix,
            )

    print(
        "Planned "
        f"{len(planned)} update(s), wrote {created_releases} release manifest(s), "
        f"updated {written_packages} package template(s)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
