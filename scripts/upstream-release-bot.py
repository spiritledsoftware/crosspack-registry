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
from datetime import datetime, timezone
from email.message import Message
from pathlib import Path
from typing import Any

try:
    import tomllib
except ModuleNotFoundError:
    import tomli as tomllib  # type: ignore[no-redef]


SEMVER_RE = re.compile(r"^\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?$")
STATE_SCHEMA_VERSION = 1


class PlannedUpdate:
    def __init__(self, package: str, version: str, config_path: Path, release: dict):
        self.package = package
        self.version = version
        self.config_path = config_path
        self.release = release


class GithubReleaseFetchResult:
    def __init__(
        self,
        *,
        releases: list[dict],
        etag: str | None = None,
        last_modified: str | None = None,
        not_modified: bool = False,
    ):
        self.releases = releases
        self.etag = etag
        self.last_modified = last_modified
        self.not_modified = not_modified


def normalize_source(source: dict) -> dict:
    release = source.get("release")
    checksum = source.get("checksum")
    asset = source.get("asset")
    version = source.get("version")
    if isinstance(release, dict) and isinstance(checksum, dict) and isinstance(asset, dict):
        normalized = {"release": release, "checksum": checksum, "asset": asset}
        if isinstance(version, dict):
            normalized["version"] = version
        return normalized

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
            "version": {"kind": "github_tag"},
            "checksum": {"kind": "download_sha256"},
            "asset": {"kind": "release_asset_url"},
        }

    if provider == "nodejs-dist":
        major = source.get("major")
        if isinstance(major, bool) or not isinstance(major, int) or major <= 0:
            raise RuntimeError("nodejs-dist source.major must be an integer > 0")
        return {
            "release": {
                "kind": "json_index",
                "url": "https://nodejs.org/dist/index.json",
            },
            "version": {
                "kind": "prefixed_semver_field",
                "field": "version",
                "prefix": "v",
                "require_prefix": f"v{major}.",
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


def empty_bot_state() -> dict[str, Any]:
    return {"schema_version": STATE_SCHEMA_VERSION, "sources": {}}


def load_bot_state(path: Path) -> dict[str, Any]:
    if not path.exists():
        return empty_bot_state()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"Ignoring invalid release bot state at {path}: {exc}", file=sys.stderr)
        return empty_bot_state()
    if not isinstance(data, dict) or data.get("schema_version") != STATE_SCHEMA_VERSION:
        print(f"Ignoring unsupported release bot state at {path}", file=sys.stderr)
        return empty_bot_state()
    sources = data.get("sources")
    if not isinstance(sources, dict):
        print(
            f"Ignoring invalid release bot state at {path}: sources must be an object",
            file=sys.stderr,
        )
        return empty_bot_state()
    normalized_sources = {
        key: value
        for key, value in sources.items()
        if isinstance(key, str) and isinstance(value, dict)
    }
    return {"schema_version": STATE_SCHEMA_VERSION, "sources": normalized_sources}


def write_bot_state(path: Path, state: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    sources = state.get("sources")
    if not isinstance(sources, dict):
        sources = {}
    normalized = {
        "schema_version": STATE_SCHEMA_VERSION,
        "sources": dict(sorted(sources.items())),
    }
    path.write_text(
        json.dumps(normalized, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def github_release_state_key(repo: str) -> str:
    return f"github_releases:{repo}"


def _http_get_json(
    url: str,
    token: str | None = None,
    extra_headers: dict[str, str] | None = None,
) -> tuple[object, Message]:
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "crosspack-registry-upstream-bot",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if extra_headers:
        headers.update(extra_headers)

    last_error: Exception | None = None
    for attempt in range(3):
        request = urllib.request.Request(url, headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310
                return json.loads(response.read().decode("utf-8")), response.headers
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


def fetch_github_releases(
    repo: str,
    token: str | None = None,
    state_entry: dict[str, Any] | None = None,
) -> GithubReleaseFetchResult:
    url = f"https://api.github.com/repos/{repo}/releases?per_page=20"
    extra_headers: dict[str, str] = {}
    if isinstance(state_entry, dict):
        etag = state_entry.get("etag")
        last_modified = state_entry.get("last_modified")
        if isinstance(etag, str) and etag:
            extra_headers["If-None-Match"] = etag
        if isinstance(last_modified, str) and last_modified:
            extra_headers["If-Modified-Since"] = last_modified
    try:
        payload, headers = _http_get_json(
            url, token=token, extra_headers=extra_headers
        )
    except urllib.error.HTTPError as error:
        if error.code == 304:
            return GithubReleaseFetchResult(releases=[], not_modified=True)
        raise
    if not isinstance(payload, list):
        raise RuntimeError(f"Unexpected releases response for {repo}")
    return GithubReleaseFetchResult(
        releases=[item for item in payload if isinstance(item, dict)],
        etag=headers.get("ETag"),
        last_modified=headers.get("Last-Modified"),
    )


def _format_fetch_error(error: urllib.error.HTTPError | urllib.error.URLError) -> str:
    if isinstance(error, urllib.error.HTTPError):
        return f"HTTP {error.code} {error.reason}"
    return str(error.reason)


def _http_error_body(error: urllib.error.HTTPError) -> str:
    if error.fp is None:
        return ""
    try:
        return error.fp.read().decode("utf-8", errors="replace")
    except OSError:
        return ""


def _is_skippable_release_fetch_error(
    *, error: urllib.error.HTTPError, release_kind: object
) -> bool:
    if error.code != 403:
        return False
    if release_kind == "github_releases":
        return True
    reason = str(error.reason).lower()
    if "rate limit" in reason:
        return True
    remaining = error.headers.get("x-ratelimit-remaining") if error.headers else None
    if remaining == "0":
        return True
    body = _http_error_body(error).lower()
    return "rate limit" in body


def fetch_json_index_releases(release_strategy: dict) -> list[dict]:
    url = release_strategy.get("url")
    if not isinstance(url, str) or not url.startswith("https://"):
        raise RuntimeError("json_index release.url must start with https://")
    payload, _headers = _http_get_json(url)
    entries = release_strategy.get("entries", "array")
    if entries == "array":
        if not isinstance(payload, list):
            raise RuntimeError(f"Unexpected JSON index array response from {url}")
        releases = [item for item in payload if isinstance(item, dict)]
    elif entries == "object_values":
        if not isinstance(payload, dict):
            raise RuntimeError(f"Unexpected JSON index object response from {url}")
        skip_keys = release_strategy.get("skip_keys", [])
        if not isinstance(skip_keys, list) or not all(isinstance(item, str) for item in skip_keys):
            raise RuntimeError("json_index release.skip_keys must be a string array")
        skipped = set(skip_keys)
        releases = []
        for key, value in payload.items():
            if key in skipped or not isinstance(value, dict):
                continue
            release = dict(value)
            if release_strategy.get("version_from_key") is True:
                release.setdefault("version", key)
            releases.append(release)
    else:
        raise RuntimeError("json_index release.entries must be 'array' or 'object_values'")

    stable_field = release_strategy.get("stable_field")
    if stable_field is not None:
        if not isinstance(stable_field, str) or not stable_field:
            raise RuntimeError("json_index release.stable_field must be a non-empty string")
        releases = [release for release in releases if release.get(stable_field) is True]
    sort_field = release_strategy.get("sort_semver_field")
    if sort_field is not None:
        if not isinstance(sort_field, str) or not sort_field:
            raise RuntimeError("json_index release.sort_semver_field must be a non-empty string")
        releases = [
            release
            for release in releases
            if isinstance(release.get(sort_field), str) and SEMVER_RE.fullmatch(release[sort_field])
        ]
        releases.sort(
            key=lambda release: tuple(int(part) for part in release[sort_field].split(".")),
            reverse=True,
        )
    return releases


def fetch_text_endpoint_releases(release_strategy: dict) -> list[dict]:
    url = release_strategy.get("url")
    if not isinstance(url, str) or not url.startswith("https://"):
        raise RuntimeError("text_endpoint release.url must start with https://")
    pattern = release_strategy.get("version_regex")
    if not isinstance(pattern, str) or not pattern:
        raise RuntimeError("text_endpoint release.version_regex must be a non-empty string")
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "crosspack-registry-upstream-bot"},
    )
    with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310
        payload = response.read().decode("utf-8")
    match = re.search(pattern, payload, re.MULTILINE)
    if match is None:
        raise RuntimeError(f"Unexpected text endpoint response from {url}")
    return [{"content": payload, "version": match.group(1)}]


def fetch_shasums(url: str) -> dict[str, str]:
    request = urllib.request.Request(
        url,
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


def version_from_strategy(release: dict, strategy: dict, release_strategy: dict) -> str | None:
    kind = strategy.get("kind")
    if kind == "github_tag":
        tag_name = release.get("tag_name")
        tag_prefix = release_strategy.get("tag_prefix")
        if tag_prefix is not None and not isinstance(tag_prefix, str):
            raise RuntimeError("source.release.tag_prefix must be a string")
        return normalize_tag_to_version(tag_name, tag_prefix) if isinstance(tag_name, str) else None

    if kind == "semver_field":
        field = strategy.get("field")
        if not isinstance(field, str) or not field:
            raise RuntimeError("source.version.field must be a non-empty string")
        value = release.get(field)
        return value if isinstance(value, str) and SEMVER_RE.fullmatch(value) else None

    if kind == "prefixed_semver_field":
        field = strategy.get("field")
        prefix = strategy.get("prefix", "")
        require_prefix = strategy.get("require_prefix")
        if not isinstance(field, str) or not field:
            raise RuntimeError("source.version.field must be a non-empty string")
        if not isinstance(prefix, str):
            raise RuntimeError("source.version.prefix must be a string")
        if require_prefix is not None and not isinstance(require_prefix, str):
            raise RuntimeError("source.version.require_prefix must be a string")
        value = release.get(field)
        if not isinstance(value, str):
            return None
        if require_prefix is not None and not value.startswith(require_prefix):
            return None
        if prefix and not value.startswith(prefix):
            return None
        version = value[len(prefix) :] if prefix else value
        return version if SEMVER_RE.fullmatch(version) else None

    if kind == "asset_name_regex":
        pattern = strategy.get("pattern")
        assets = release.get("assets")
        if not isinstance(pattern, str) or not pattern:
            raise RuntimeError("source.version.pattern must be a non-empty string")
        if not isinstance(assets, list):
            return None
        rendered_pattern = pattern.format(tag_name=release.get("tag_name", ""))
        for asset in assets:
            if not isinstance(asset, dict):
                continue
            name = asset.get("name")
            if not isinstance(name, str):
                continue
            match = re.search(rendered_pattern, name)
            if match and SEMVER_RE.fullmatch(match.group(1)):
                return match.group(1)
        return None

    if kind == "regex_capture":
        field = strategy.get("field")
        pattern = strategy.get("pattern")
        if not isinstance(field, str) or not field:
            raise RuntimeError("source.version.field must be a non-empty string")
        if not isinstance(pattern, str) or not pattern:
            raise RuntimeError("source.version.pattern must be a non-empty string")
        value = release.get(field)
        if not isinstance(value, str):
            return None
        match = re.search(pattern, value, re.MULTILINE)
        if match is None:
            return None
        version = match.group(1)
        return version if SEMVER_RE.fullmatch(version) else None

    raise RuntimeError("source.version.kind must be a supported version strategy")


def latest_version_from_releases(
    releases: list[dict], release_strategy: dict, version_strategy: dict
) -> str | None:
    include_prereleases = bool(release_strategy.get("include_prereleases", False))
    for release in releases:
        if release.get("draft") is True:
            continue
        if release.get("prerelease") is True and not include_prereleases:
            continue
        version = version_from_strategy(release, version_strategy, release_strategy)
        if version is not None:
            return version
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
    normalized = normalize_source(source)
    release_strategy = normalized["release"]
    version_strategy = normalized.get("version")
    if not isinstance(version_strategy, dict):
        version_strategy = {"kind": "github_tag"}

    existing_versions = {
        p.stem for p in (releases_root / package).glob("*.toml") if p.is_file()
    }

    include_prereleases = bool(release_strategy.get("include_prereleases", False))

    for release in releases:
        if release.get("draft") is True:
            continue
        if release.get("prerelease") is True and not include_prereleases:
            continue

        version = version_from_strategy(release, version_strategy, release_strategy)
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


def _snapshot_paths(repo_root: Path, paths: list[Path]) -> dict[Path, bytes]:
    snapshot: dict[Path, bytes] = {}
    for path in paths:
        full_path = repo_root / path
        if full_path.exists():
            snapshot[path] = full_path.read_bytes()
    return snapshot


def _restore_path_snapshot(repo_root: Path, snapshot: dict[Path, bytes]) -> None:
    for path, content in snapshot.items():
        full_path = repo_root / path
        full_path.parent.mkdir(parents=True, exist_ok=True)
        full_path.write_bytes(content)


def validate_generated_paths(*, repo_root: Path, staged_paths: list[Path]) -> None:
    package_paths: list[str] = []
    release_paths: list[str] = []
    state_path = Path("state/upstream-release-bot.json")
    for path in staged_paths:
        value = path.as_posix()
        if value.startswith("packages/") and value.endswith(".toml") and len(path.parts) == 2:
            package_paths.append(value)
        elif value.startswith("releases/") and value.endswith(".toml") and len(path.parts) == 3:
            release_paths.append(value)
        elif path == state_path:
            continue
        else:
            raise RuntimeError(f"unexpected generated path for release bot PR: {value}")

    if package_paths:
        _run(["python3", "scripts/registry-validate-source.py", *package_paths], cwd=repo_root)
    if package_paths or release_paths:
        _run(
            [
                "python3",
                "scripts/registry-validate.py",
                "--allow-missing-signatures",
                *package_paths,
                *release_paths,
            ],
            cwd=repo_root,
        )


def _enable_pr_automerge(*, repo_root: Path, pr_ref: str) -> None:
    _run(["gh", "pr", "merge", pr_ref, "--auto", "--squash"], cwd=repo_root)


def _open_pr_number_for_branch(*, repo_root: Path, branch_name: str) -> int | None:
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
    if not isinstance(found, list) or not found:
        return None
    number = found[0].get("number")
    if not isinstance(number, int):
        raise RuntimeError(f"Unexpected PR lookup response for {branch_name}")
    return number


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
    path_snapshot = _snapshot_paths(repo_root, staged_paths)

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
    _restore_path_snapshot(repo_root, path_snapshot)
    if staged_paths:
        _run(["git", "add", *(str(path) for path in staged_paths)], cwd=repo_root)
    validate_generated_paths(repo_root=repo_root, staged_paths=staged_paths)
    staged = _run(["git", "diff", "--cached", "--name-only"], cwd=repo_root)
    if not staged.stdout.strip():
        number = _open_pr_number_for_branch(repo_root=repo_root, branch_name=branch_name)
        if number is not None:
            _enable_pr_automerge(repo_root=repo_root, pr_ref=str(number))
            print(f"PR already open for {branch_name}; enabled automerge")
        return

    _run(["git", "commit", "-m", title], cwd=repo_root)
    _run(["git", "push", "-u", "origin", branch_name], cwd=repo_root)

    number = _open_pr_number_for_branch(repo_root=repo_root, branch_name=branch_name)
    if number is not None:
        _enable_pr_automerge(repo_root=repo_root, pr_ref=str(number))
        print(f"PR already open for {branch_name}; enabled automerge")
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
    _enable_pr_automerge(repo_root=repo_root, pr_ref=branch_name)


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        description="Generate/update manifests from upstream releases"
    )
    parser.add_argument(
        "--packages-root",
        type=Path,
        default=Path("packages"),
        help="Registry package template/source directory",
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
    parser.add_argument(
        "--state-path",
        type=Path,
        default=Path("state/upstream-release-bot.json"),
        help="Release bot advisory state path",
    )
    args = parser.parse_args(argv)

    repo_root = Path.cwd()
    generator = _load_generator_module(repo_root)

    package_filter = set(args.package or [])
    config_paths = sorted(args.packages_root.glob("*.toml"))
    if package_filter:
        config_paths = [p for p in config_paths if p.stem in package_filter]

    if not config_paths:
        print("No package configs selected")
        return 0

    github_token = __import__("os").environ.get("GITHUB_TOKEN")
    bot_state = load_bot_state(args.state_path)
    state_sources = bot_state.setdefault("sources", {})
    if not isinstance(state_sources, dict):
        state_sources = {}
        bot_state["sources"] = state_sources
    state_changed = False
    planned: list[PlannedUpdate] = []
    skipped_fetches = 0
    for config_path in config_paths:
        config = load_config(config_path)
        source = config.get("source")
        if not isinstance(source, dict):
            raise RuntimeError(f"Missing source table in {config_path}")
        normalized = normalize_source(source)
        release_strategy = normalized["release"]
        release_kind = release_strategy.get("kind")
        try:
            if release_kind == "json_index":
                releases = fetch_json_index_releases(release_strategy)
            elif release_kind == "text_endpoint":
                releases = fetch_text_endpoint_releases(release_strategy)
            elif release_kind == "github_releases":
                repo = release_strategy.get("repo")
                if not isinstance(repo, str):
                    raise RuntimeError(f"Missing source.release.repo in {config_path}")
                state_key = github_release_state_key(repo)
                state_entry = state_sources.get(state_key)
                if not isinstance(state_entry, dict):
                    state_entry = None
                fetch_result = fetch_github_releases(
                    repo, token=github_token, state_entry=state_entry
                )
                if fetch_result.not_modified:
                    continue
                releases = fetch_result.releases
                version_strategy = normalized.get("version")
                if not isinstance(version_strategy, dict):
                    version_strategy = {"kind": "github_tag"}
                next_entry = dict(state_entry or {})
                if fetch_result.etag:
                    next_entry["etag"] = fetch_result.etag
                if fetch_result.last_modified:
                    next_entry["last_modified"] = fetch_result.last_modified
                next_entry["last_checked_at"] = utc_now_iso()
                latest_version = latest_version_from_releases(
                    releases, release_strategy, version_strategy
                )
                if latest_version is not None:
                    next_entry["latest_version"] = latest_version
                if next_entry != state_sources.get(state_key):
                    state_sources[state_key] = next_entry
                    state_changed = True
            else:
                raise RuntimeError(f"Unsupported source.release.kind in {config_path}")
        except urllib.error.HTTPError as error:
            if not _is_skippable_release_fetch_error(
                error=error, release_kind=release_kind
            ):
                raise
            skipped_fetches += 1
            print(
                f"Skipping {config_path.stem}: failed to fetch upstream releases "
                f"({_format_fetch_error(error)})",
                file=sys.stderr,
            )
            continue
        planned.extend(
            plan_updates_for_config(
                config_path=config_path,
                releases_root=args.releases_root,
                releases=releases,
            )
        )

    if not planned:
        print(
            "No new releases detected; "
            f"skipped {skipped_fetches} release fetch(es)"
        )
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
            if checksum_strategy.get("kind") == "shasums256":
                checksum_url = checksum_strategy.get("url_template")
                if not isinstance(checksum_url, str) or not checksum_url.startswith("https://"):
                    raise RuntimeError(f"Missing source.checksum.url_template in {update.config_path}")
                shasums_by_name = fetch_shasums(checksum_url)

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

        if state_changed:
            write_bot_state(args.state_path, bot_state)
            if args.state_path not in staged_paths:
                staged_paths.append(args.state_path)

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
        f"updated {written_packages} package template(s), "
        f"skipped {skipped_updates} incomplete update(s), "
        f"skipped {skipped_fetches} release fetch(es)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
