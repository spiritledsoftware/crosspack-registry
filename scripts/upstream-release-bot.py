#!/usr/bin/env python3
from __future__ import annotations

import argparse
import importlib.util
import json
import re
import subprocess
import sys
import time
import urllib.error
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


def normalize_source(source: dict) -> dict:
    release = source.get("release")
    checksum = source.get("checksum")
    asset = source.get("asset")
    if isinstance(release, dict) and isinstance(checksum, dict) and isinstance(asset, dict):
        return {"release": release, "checksum": checksum, "asset": asset}

    provider = source.get("provider")
    if provider == "github":
        repo = source.get("repo")
        if not isinstance(repo, str) or not repo:
            raise RuntimeError("github source config requires source.repo")
        normalized_release = {
            "kind": "github_releases",
            "repo": repo,
            "include_prereleases": bool(source.get("include_prereleases", False)),
        }
        tag_prefix = source.get("tag_prefix")
        if isinstance(tag_prefix, str) and tag_prefix:
            normalized_release["tag_prefix"] = tag_prefix
        return {
            "release": normalized_release,
            "checksum": {"kind": "download_sha256"},
            "asset": {"kind": "release_asset_url"},
        }

    if provider == "nodejs-dist":
        major = source.get("major")
        if isinstance(major, bool) or not isinstance(major, int) or major <= 0:
            raise RuntimeError("nodejs-dist source.major must be an integer > 0")
        return {
            "release": {
                "kind": "node_dist_index",
                "major": major,
                "include_prereleases": bool(source.get("include_prereleases", False)),
            },
            "checksum": {"kind": "shasums256"},
            "asset": {
                "kind": "templated",
                "base_url": f"https://nodejs.org/dist/latest-v{major}.x",
            },
        }

    raise RuntimeError(
        "source config must define either release/checksum/asset tables or a supported legacy provider"
    )


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

    last_error: Exception | None = None
    for attempt in range(3):
        request = urllib.request.Request(url, headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as error:
            if error.code < 500 or attempt == 2:
                raise
            last_error = error
        except urllib.error.URLError as error:
            if attempt == 2:
                raise
            last_error = error

        time.sleep(2**attempt)

    if last_error is not None:
        raise last_error
    raise RuntimeError(f"Failed to fetch JSON from {url}")


def fetch_github_releases(repo: str, token: str | None = None) -> list[dict]:
    url = f"https://api.github.com/repos/{repo}/releases?per_page=20"
    payload = _http_get_json(url, token=token)
    if not isinstance(payload, list):
        raise RuntimeError(f"Unexpected releases response for {repo}")
    return [item for item in payload if isinstance(item, dict)]


def fetch_nodejs_dist_releases(major: int) -> list[dict]:
    payload = _http_get_json("https://nodejs.org/dist/index.json")
    if not isinstance(payload, list):
        raise RuntimeError("Unexpected Node.js dist index response")
    major_prefix = f"v{major}."
    return [
        item
        for item in payload
        if isinstance(item, dict)
        and isinstance(item.get("version"), str)
        and item["version"].startswith(major_prefix)
    ]


def fetch_go_dist_releases() -> list[dict]:
    payload = _http_get_json("https://go.dev/dl/?mode=json")
    if not isinstance(payload, list):
        raise RuntimeError("Unexpected Go dist index response")
    return [item for item in payload if isinstance(item, dict) and item.get("stable") is True]


def fetch_rustup_static_releases() -> list[dict]:
    request = urllib.request.Request(
        "https://static.rust-lang.org/rustup/release-stable.toml",
        headers={"User-Agent": "crosspack-registry-upstream-bot"},
    )
    with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310
        payload = response.read().decode("utf-8")
    match = re.search(r"^version\s*=\s*'([^']+)'", payload, re.MULTILINE)
    if match is None:
        raise RuntimeError("Unexpected rustup release-stable.toml response")
    return [{"version": match.group(1)}]


def fetch_zig_download_index_releases() -> list[dict]:
    payload = _http_get_json("https://ziglang.org/download/index.json")
    if not isinstance(payload, dict):
        raise RuntimeError("Unexpected Zig download index response")
    releases = []
    for key, value in payload.items():
        if key == "master" or not SEMVER_RE.fullmatch(key) or not isinstance(value, dict):
            continue
        release = dict(value)
        release.setdefault("version", key)
        releases.append(release)
    releases.sort(key=lambda item: tuple(int(part) for part in item["version"].split(".")), reverse=True)
    return releases


def fetch_nodejs_dist_shasums(*, major: int) -> dict[str, str]:
    request = urllib.request.Request(
        f"https://nodejs.org/dist/latest-v{major}.x/SHASUMS256.txt",
        headers={"User-Agent": "crosspack-registry-upstream-bot"},
    )
    with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310
        payload = response.read().decode("utf-8")
    shasums: dict[str, str] = {}
    for line in payload.splitlines():
        parts = line.strip().split()
        if len(parts) == 2:
            shasums[parts[1]] = parts[0]
    return shasums


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


def python_standalone_version(release: dict, python_major_minor: str) -> str | None:
    tag_name = release.get("tag_name")
    assets = release.get("assets")
    if not isinstance(tag_name, str) or not isinstance(assets, list):
        return None
    prefix = f"cpython-{python_major_minor}."
    versions: list[tuple[tuple[int, int, int], str]] = []
    for asset in assets:
        if not isinstance(asset, dict):
            continue
        name = asset.get("name")
        if not isinstance(name, str) or not name.startswith(prefix):
            continue
        match = re.match(r"^cpython-(\d+)\.(\d+)\.(\d+)\+", name)
        if match is None:
            continue
        version = f"{match.group(1)}.{match.group(2)}.{match.group(3)}+{tag_name}"
        versions.append((tuple(int(part) for part in match.groups()), version))
    if not versions:
        return None
    versions.sort(reverse=True)
    return versions[0][1]


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
    normalized = normalize_source(source)
    release_strategy = normalized["release"]
    release_kind = release_strategy.get("kind")

    existing_versions = {
        p.stem for p in (releases_root / package).glob("*.toml") if p.is_file()
    }

    if release_kind == "go_dist_index":
        for release in releases:
            version_tag = release.get("version")
            if not isinstance(version_tag, str) or not version_tag.startswith("go"):
                continue
            version = version_tag.removeprefix("go")
            if not SEMVER_RE.fullmatch(version):
                continue
            if version in existing_versions:
                return []
            return [PlannedUpdate(package, version, config_path, release)]
        return []

    if release_kind == "rustup_static":
        for release in releases:
            version = release.get("version")
            if not isinstance(version, str) or not SEMVER_RE.fullmatch(version):
                continue
            if version in existing_versions:
                return []
            return [PlannedUpdate(package, version, config_path, release)]
        return []

    if release_kind == "zig_download_index":
        for release in releases:
            version = release.get("version")
            if not isinstance(version, str) or not SEMVER_RE.fullmatch(version):
                continue
            if version in existing_versions:
                return []
            return [PlannedUpdate(package, version, config_path, release)]
        return []

    if release_kind == "python_build_standalone":
        python_major_minor = release_strategy.get("python_major_minor")
        if not isinstance(python_major_minor, str):
            raise RuntimeError(f"python_build_standalone release.python_major_minor must be set in {config_path}")
        for release in releases:
            if release.get("draft") is True or release.get("prerelease") is True:
                continue
            version = python_standalone_version(release, python_major_minor)
            if version is None:
                continue
            if version in existing_versions:
                return []
            return [PlannedUpdate(package, version, config_path, release)]
        return []

    if release_kind == "node_dist_index":
        major = release_strategy.get("major")
        if isinstance(major, bool) or not isinstance(major, int) or major <= 0:
            raise RuntimeError(f"node_dist_index release.major must be an integer > 0 in {config_path}")

        for release in releases:
            version_tag = release.get("version")
            if not isinstance(version_tag, str) or not version_tag.startswith(f"v{major}."):
                continue
            version = normalize_tag_to_version(version_tag)
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

    include_prereleases = bool(release_strategy.get("include_prereleases", False))
    tag_prefix = release_strategy.get("tag_prefix")
    if tag_prefix is not None and not isinstance(tag_prefix, str):
        raise RuntimeError(f"source.release.tag_prefix must be a string in {config_path}")

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
        normalized = normalize_source(source)
        release_strategy = normalized["release"]
        release_kind = release_strategy.get("kind")
        if release_kind == "node_dist_index":
            major = release_strategy.get("major")
            if isinstance(major, bool) or not isinstance(major, int) or major <= 0:
                raise RuntimeError(f"Missing or invalid source.release.major in {config_path}")
            releases = fetch_nodejs_dist_releases(major)
        elif release_kind == "go_dist_index":
            releases = fetch_go_dist_releases()
        elif release_kind == "rustup_static":
            releases = fetch_rustup_static_releases()
        elif release_kind == "zig_download_index":
            releases = fetch_zig_download_index_releases()
        else:
            repo = release_strategy.get("repo")
            if not isinstance(repo, str):
                raise RuntimeError(f"Missing source.release.repo in {config_path}")
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
    skipped_updates = 0
    for update in planned:
        package_path = args.packages_root / f"{update.package}.toml"
        release_path = args.releases_root / update.package / f"{update.version}.toml"
        if release_path.exists():
            continue

        config = load_config(update.config_path)
        source = config.get("source")
        shasums_by_name = None
        if isinstance(source, dict):
            normalized = normalize_source(source)
            release_strategy = normalized["release"]
            checksum_strategy = normalized["checksum"]
            if (
                release_strategy.get("kind") == "node_dist_index"
                and checksum_strategy.get("kind") == "shasums256"
            ):
                major = release_strategy.get("major")
                if isinstance(major, bool) or not isinstance(major, int) or major <= 0:
                    raise RuntimeError(f"Missing or invalid source.release.major in {update.config_path}")
                shasums_by_name = fetch_nodejs_dist_shasums(major=major)

        try:
            release_text = generator.generate_release_text(
                config_path=update.config_path,
                version=update.version,
                release=update.release,
                shasums_by_name=shasums_by_name,
            )
        except generator.GenerateError as exc:
            skipped_updates += 1
            print(
                "Skipping "
                f"{update.package} {update.version}: incomplete upstream release ({exc})",
                file=sys.stderr,
            )
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
        f"updated {written_packages} package template(s), skipped {skipped_updates} incomplete update(s)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
