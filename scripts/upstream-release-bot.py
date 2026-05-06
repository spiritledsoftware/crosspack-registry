#!/usr/bin/env python3
from __future__ import annotations

import argparse
import errno
import http.client
import importlib.util
import json
import re
import socket
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
STATE_SCHEMA_VERSION = 2


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
    return {"schema_version": STATE_SCHEMA_VERSION, "sources": {}, "packages": {}, "quarantine": {}}


def _object_map(value: object) -> dict[str, dict[str, Any]]:
    if not isinstance(value, dict):
        return {}
    return {
        key: entry
        for key, entry in value.items()
        if isinstance(key, str) and isinstance(entry, dict)
    }


def _normalize_bot_state(data: dict[str, Any]) -> dict[str, Any] | None:
    schema_version = data.get("schema_version")
    if schema_version == 1:
        return {
            "schema_version": STATE_SCHEMA_VERSION,
            "sources": _object_map(data.get("sources")),
            "packages": {},
            "quarantine": {},
        }
    if schema_version != STATE_SCHEMA_VERSION:
        return None
    return {
        "schema_version": STATE_SCHEMA_VERSION,
        "sources": _object_map(data.get("sources")),
        "packages": _object_map(data.get("packages")),
        "quarantine": _object_map(data.get("quarantine")),
    }


def load_bot_state(path: Path) -> dict[str, Any]:
    if not path.exists():
        return empty_bot_state()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"Ignoring invalid release bot state at {path}: {exc}", file=sys.stderr)
        return empty_bot_state()
    if not isinstance(data, dict):
        print(f"Ignoring invalid release bot state at {path}: root must be an object", file=sys.stderr)
        return empty_bot_state()
    normalized = _normalize_bot_state(data)
    if normalized is None:
        print(f"Ignoring unsupported release bot state at {path}", file=sys.stderr)
        return empty_bot_state()
    return normalized


def write_bot_state(path: Path, state: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    normalized = {
        "packages": dict(sorted(_object_map(state.get("packages")).items())),
        "quarantine": dict(sorted(_object_map(state.get("quarantine")).items())),
        "schema_version": STATE_SCHEMA_VERSION,
        "sources": dict(sorted(_object_map(state.get("sources")).items())),
    }
    path.write_text(
        json.dumps(normalized, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def bot_state_needs_persist(path: Path, state: dict[str, Any]) -> bool:
    if not path.exists():
        return False
    try:
        current = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return True
    expected = {
        "packages": dict(sorted(_object_map(state.get("packages")).items())),
        "quarantine": dict(sorted(_object_map(state.get("quarantine")).items())),
        "schema_version": STATE_SCHEMA_VERSION,
        "sources": dict(sorted(_object_map(state.get("sources")).items())),
    }
    return current != expected


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def iso_from_epoch(epoch: int) -> str:
    return datetime.fromtimestamp(epoch, timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def iso_after_seconds(now_iso: str, seconds: int) -> str:
    current = datetime.fromisoformat(now_iso.replace("Z", "+00:00"))
    return iso_from_epoch(int(current.timestamp()) + seconds)


def package_backoff_active(package_state: dict[str, Any], *, now_iso: str | None = None) -> bool:
    backoff_until = package_state.get("backoff_until")
    if not isinstance(backoff_until, str) or not backoff_until:
        return False
    current = now_iso or utc_now_iso()
    return backoff_until > current


def backoff_from_http_error(error: urllib.error.HTTPError, *, now_epoch: int | None = None) -> dict[str, Any]:
    reset = error.headers.get("x-ratelimit-reset") if error.headers else None
    if isinstance(reset, str) and reset.isdigit():
        reset_epoch = int(reset)
    else:
        base = int(time.time() if now_epoch is None else now_epoch)
        reset_epoch = base + 3600
    return {
        "reason_code": "rate-limited",
        "detail": _format_fetch_error(error),
        "backoff_until": iso_from_epoch(reset_epoch),
        "last_failure_reason": "rate-limited",
        "last_failure_reset_at": iso_from_epoch(reset_epoch),
        "last_failed_at": utc_now_iso(),
    }


def backoff_from_transient_fetch_error(
    error: urllib.error.HTTPError | urllib.error.URLError,
) -> dict[str, Any]:
    now = utc_now_iso()
    if isinstance(error, urllib.error.HTTPError) and error.code == 429:
        reset = error.headers.get("x-ratelimit-reset") if error.headers else None
        if isinstance(reset, str) and reset.isdigit():
            backoff_until = iso_from_epoch(int(reset))
        else:
            backoff_until = iso_after_seconds(now, 3600)
        return {
            "reason_code": "rate-limited",
            "detail": _format_fetch_error(error),
            "backoff_until": backoff_until,
            "last_failure_reason": "rate-limited",
            "last_failure_reset_at": backoff_until,
            "last_failed_at": now,
        }

    backoff_until = iso_after_seconds(now, 3600)
    return {
        "reason_code": "upstream-error",
        "detail": _format_fetch_error(error),
        "backoff_until": backoff_until,
        "last_failure_reason": "upstream-error",
        "last_failure_reset_at": backoff_until,
        "last_failed_at": now,
    }


def source_identity_for_release(release_kind: object, release_strategy: dict) -> str | None:
    if release_kind == "github_releases":
        repo = release_strategy.get("repo")
        return github_release_state_key(repo) if isinstance(repo, str) else None
    url = release_strategy.get("url")
    if isinstance(release_kind, str) and isinstance(url, str):
        return f"{release_kind}:{url}"
    return release_kind if isinstance(release_kind, str) else None


def apply_package_source_audit(
    package_state: dict[str, Any], *, source_identity: str | None, source_kind: object
) -> None:
    if source_identity is not None:
        package_state["source_identity"] = source_identity
    if isinstance(source_kind, str):
        package_state["source_kind"] = source_kind


def quarantine_package(
    state: dict[str, Any],
    *,
    package: str,
    reason_code: str,
    detail: str,
    attempted_version: str,
    last_good_version: str | None,
    now_iso: str | None = None,
) -> bool:
    quarantine = state.setdefault("quarantine", {})
    if not isinstance(quarantine, dict):
        quarantine = {}
        state["quarantine"] = quarantine
    now_value = now_iso or utc_now_iso()
    previous = quarantine.get(package)
    first_seen = previous.get("first_seen_at") if isinstance(previous, dict) else None
    next_entry = {
        "reason_code": reason_code,
        "detail": detail,
        "first_seen_at": first_seen if isinstance(first_seen, str) else now_value,
        "last_seen_at": now_value,
        "attempted_version": attempted_version,
    }
    if last_good_version is not None:
        next_entry["last_good_version"] = last_good_version
    changed = previous != next_entry
    quarantine[package] = next_entry
    return changed


def clear_quarantine(state: dict[str, Any], *, package: str) -> bool:
    quarantine = state.setdefault("quarantine", {})
    if not isinstance(quarantine, dict):
        state["quarantine"] = {}
        return False
    return quarantine.pop(package, None) is not None


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
        except http.client.IncompleteRead as error:
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
    if error.code == 429 or 500 <= error.code <= 599:
        return True
    if error.code != 403:
        return False
    reason = str(error.reason).lower()
    if "rate limit" in reason or "secondary limit" in reason:
        return True
    remaining = error.headers.get("x-ratelimit-remaining") if error.headers else None
    if remaining == "0":
        return True
    body = _http_error_body(error).lower()
    return "rate limit" in body or "secondary limit" in body


def _is_transient_url_error(error: urllib.error.URLError) -> bool:
    reason = getattr(error, "reason", None)
    if isinstance(reason, TimeoutError | socket.timeout):
        return True
    if isinstance(reason, str) and "timed out" in reason.lower():
        return True
    reason_errno = getattr(reason, "errno", None)
    return reason_errno in {
        errno.EAGAIN,
        errno.ECONNRESET,
        errno.ETIMEDOUT,
    }


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


def validate_package_generated_paths(*, repo_root: Path, package: str, staged_paths: list[Path]) -> None:
    package_paths = [path for path in staged_paths if path == Path("packages") / f"{package}.toml"]
    release_paths = [
        path
        for path in staged_paths
        if len(path.parts) == 3
        and path.parts[0] == "releases"
        and path.parts[1] == package
        and path.suffix == ".toml"
    ]
    validate_generated_paths(repo_root=repo_root, staged_paths=[*package_paths, *release_paths])


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


def _remote_branch_exists(*, repo_root: Path, branch_name: str) -> bool:
    result = subprocess.run(
        ["git", "ls-remote", "--exit-code", "origin", branch_name],
        cwd=repo_root,
        check=False,
        text=True,
        capture_output=True,
    )
    return result.returncode == 0


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
    staged = _run(["git", "diff", "--cached", "--name-only"], cwd=repo_root)
    if not staged.stdout.strip():
        number = _open_pr_number_for_branch(repo_root=repo_root, branch_name=branch_name)
        if number is not None:
            _enable_pr_automerge(repo_root=repo_root, pr_ref=str(number))
            print(f"PR already open for {branch_name}; enabled automerge")
        return
    validate_generated_paths(repo_root=repo_root, staged_paths=staged_paths)

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


def _open_or_update_rolling_pr(
    *,
    repo_root: Path,
    staged_paths: list[Path],
    base_branch: str,
    branch_name: str,
    title: str,
    body: str,
) -> None:
    path_snapshot = _snapshot_paths(repo_root, staged_paths)
    _run(["git", "fetch", "origin", base_branch], cwd=repo_root)
    _run(["git", "switch", "-C", branch_name, f"origin/{base_branch}"], cwd=repo_root)
    _restore_path_snapshot(repo_root, path_snapshot)
    if staged_paths:
        _run(["git", "add", *(str(path) for path in staged_paths)], cwd=repo_root)
    staged = _run(["git", "diff", "--cached", "--name-only"], cwd=repo_root)
    if not staged.stdout.strip():
        number = _open_pr_number_for_branch(repo_root=repo_root, branch_name=branch_name)
        if number is not None:
            _enable_pr_automerge(repo_root=repo_root, pr_ref=str(number))
            print(f"PR already open for {branch_name}; enabled automerge")
        return
    validate_generated_paths(repo_root=repo_root, staged_paths=staged_paths)
    _run(["git", "commit", "-m", title], cwd=repo_root)
    push_cmd = ["git", "push", "--force-with-lease", "-u", "origin", branch_name]
    if _remote_branch_exists(repo_root=repo_root, branch_name=branch_name):
        _run(
            [
                "git",
                "fetch",
                "origin",
                f"+refs/heads/{branch_name}:refs/remotes/origin/{branch_name}",
            ],
            cwd=repo_root,
        )
        expected = _run(
            ["git", "rev-parse", f"refs/remotes/origin/{branch_name}"], cwd=repo_root
        ).stdout.strip()
        push_cmd = [
            "git",
            "push",
            f"--force-with-lease=refs/heads/{branch_name}:{expected}",
            "-u",
            "origin",
            branch_name,
        ]
    _run(push_cmd, cwd=repo_root)
    number = _open_pr_number_for_branch(repo_root=repo_root, branch_name=branch_name)
    if number is not None:
        _run(["gh", "pr", "edit", str(number), "--title", title, "--body", body], cwd=repo_root)
        _enable_pr_automerge(repo_root=repo_root, pr_ref=str(number))
        print(f"Updated PR #{number} for {branch_name}; enabled automerge")
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


def _reconcile_empty_rolling_pr(
    *, repo_root: Path, base_branch: str, branch_name: str
) -> None:
    if not _remote_branch_exists(repo_root=repo_root, branch_name=branch_name):
        return
    _run(["git", "fetch", "origin", base_branch], cwd=repo_root)
    _run(
        ["git", "fetch", "origin", f"+refs/heads/{branch_name}:refs/remotes/origin/{branch_name}"],
        cwd=repo_root,
    )
    expected = _run(
        ["git", "rev-parse", f"refs/remotes/origin/{branch_name}"], cwd=repo_root
    ).stdout.strip()
    _run(
        [
            "git",
            "push",
            f"--force-with-lease=refs/heads/{branch_name}:{expected}",
            "origin",
            f"origin/{base_branch}:refs/heads/{branch_name}",
        ],
        cwd=repo_root,
    )
    number = _open_pr_number_for_branch(repo_root=repo_root, branch_name=branch_name)
    if number is not None:
        _run(
            [
                "gh",
                "pr",
                "close",
                str(number),
                "--comment",
                "Closing stale rolling release PR: no generated changes remain.",
            ],
            cwd=repo_root,
        )


def render_pr_body(
    *,
    updated_packages: list[str],
    quarantine_added: list[str],
    quarantine_updated: list[str],
    quarantine_cleared: list[str],
    backoff_packages: list[str],
    state_changed: bool,
    created_releases: int,
    written_packages: int,
    quarantined_count: int,
    transient_failures: int,
    skipped_fetches: int,
) -> str:
    updated = ", ".join(sorted(updated_packages)) if updated_packages else "none"
    added = ", ".join(sorted(set(quarantine_added))) if quarantine_added else "none"
    quarantine_updated_value = ", ".join(sorted(set(quarantine_updated))) if quarantine_updated else "none"
    cleared = ", ".join(sorted(set(quarantine_cleared))) if quarantine_cleared else "none"
    backoff = ", ".join(sorted(set(backoff_packages))) if backoff_packages else "none"
    return (
        "## Summary\n"
        f"- updated packages: {updated}\n"
        f"- quarantined packages: {quarantined_count}\n"
        f"- release manifests written: {created_releases}\n"
        f"- package templates updated: {written_packages}\n"
        f"- transient fetch failures: {transient_failures}\n"
        f"- release fetches skipped: {skipped_fetches}\n"
        "\n## Audit\n"
        f"- state changed: {'yes' if state_changed else 'no'}\n"
        f"- quarantine added: {added}\n"
        f"- quarantine updated: {quarantine_updated_value}\n"
        f"- quarantine cleared: {cleared}\n"
        f"- backoff packages: {backoff}\n"
        "\n## Validation\n"
        "- registry-validate-source.py for changed package templates\n"
        "- registry-validate.py --allow-missing-signatures for changed metadata\n"
    )


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
    parser.add_argument("--branch-name", default="upstream-release/rolling")
    parser.add_argument("--branch-prefix", default=None, help=argparse.SUPPRESS)
    parser.add_argument(
        "--state-path",
        type=Path,
        default=Path("state/upstream-release-bot.json"),
        help="Release bot advisory state path",
    )
    args = parser.parse_args(argv)

    repo_root = Path.cwd()
    if args.create_prs:
        _run(["git", "fetch", "origin", args.base_branch], cwd=repo_root)
        _run(["git", "switch", "-C", args.branch_name, f"origin/{args.base_branch}"], cwd=repo_root)

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
    state_packages = bot_state.setdefault("packages", {})
    if not isinstance(state_packages, dict):
        state_packages = {}
        bot_state["packages"] = state_packages
    state_changed = bot_state_needs_persist(args.state_path, bot_state)
    planned: list[PlannedUpdate] = []
    skipped_fetches = 0
    transient_fetch_failures = 0
    up_to_date_packages = 0
    all_staged_paths: list[Path] = []
    updated_packages: list[str] = []
    quarantine_added: list[str] = []
    quarantine_updated: list[str] = []
    quarantine_cleared: list[str] = []
    backoff_packages: list[str] = []
    pr_material_state_changed = False
    for config_path in config_paths:
        config = load_config(config_path)
        package_name = str(config.get("name") or config_path.stem)
        package_state = state_packages.setdefault(package_name, {})
        if not isinstance(package_state, dict):
            package_state = {}
            state_packages[package_name] = package_state
        if package_backoff_active(package_state):
            skipped_fetches += 1
            backoff_packages.append(
                f"{package_name}:backoff-active:{package_state.get('backoff_until')}"
            )
            print(
                f"registry_update package={package_name} status=skipped reason=backoff-active "
                f"reset_at={package_state.get('backoff_until')}",
                file=sys.stderr,
            )
            continue
        source = config.get("source")
        if not isinstance(source, dict):
            raise RuntimeError(f"Missing source table in {config_path}")
        normalized = normalize_source(source)
        release_strategy = normalized["release"]
        release_kind = release_strategy.get("kind")
        source_identity = source_identity_for_release(release_kind, release_strategy)
        previous_package_state = dict(package_state)
        apply_package_source_audit(
            package_state, source_identity=source_identity, source_kind=release_kind
        )
        if package_state != previous_package_state:
            state_changed = True
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
                    up_to_date_packages += 1
                    checked_at = utc_now_iso()
                    package_state["last_checked_at"] = checked_at
                    next_entry = dict(state_entry or {})
                    if source_identity is not None:
                        next_entry["source_identity"] = source_identity
                    if isinstance(release_kind, str):
                        next_entry["source_kind"] = release_kind
                    next_entry["last_checked_at"] = checked_at
                    if next_entry != state_sources.get(state_key):
                        state_sources[state_key] = next_entry
                        state_changed = True
                    for key in (
                        "backoff_until",
                        "reason_code",
                        "detail",
                        "last_failed_at",
                        "last_failure_reason",
                        "last_failure_reset_at",
                    ):
                        if key in package_state:
                            package_state.pop(key, None)
                            state_changed = True
                    continue
                releases = fetch_result.releases
                version_strategy = normalized.get("version")
                if not isinstance(version_strategy, dict):
                    version_strategy = {"kind": "github_tag"}
                next_entry = dict(state_entry or {})
                if source_identity is not None:
                    next_entry["source_identity"] = source_identity
                if isinstance(release_kind, str):
                    next_entry["source_kind"] = release_kind
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
                    next_entry["latest_seen_version"] = latest_version
                    package_state["latest_seen_version"] = latest_version
                package_state["last_checked_at"] = next_entry["last_checked_at"]
                if next_entry != state_sources.get(state_key):
                    state_sources[state_key] = next_entry
                    state_changed = True
            else:
                raise RuntimeError(f"Unsupported source.release.kind in {config_path}")
            for key in (
                "backoff_until",
                "reason_code",
                "detail",
                "last_failed_at",
                "last_failure_reason",
                "last_failure_reset_at",
            ):
                if key in package_state:
                    package_state.pop(key, None)
                    state_changed = True
        except (urllib.error.HTTPError, urllib.error.URLError) as error:
            if isinstance(error, urllib.error.HTTPError):
                if not _is_skippable_release_fetch_error(
                    error=error, release_kind=release_kind
                ):
                    raise
            elif not _is_transient_url_error(error):
                raise
            if isinstance(error, urllib.error.HTTPError) and error.code == 403:
                package_state.update(backoff_from_http_error(error))
            else:
                package_state.update(backoff_from_transient_fetch_error(error))
            apply_package_source_audit(
                package_state, source_identity=source_identity, source_kind=release_kind
            )
            state_changed = True
            pr_material_state_changed = True
            skipped_fetches += 1
            transient_fetch_failures += 1
            backoff_packages.append(
                f"{package_name}:{package_state.get('reason_code')}:{package_state.get('backoff_until')}"
            )
            print(
                f"Skipping {config_path.stem}: failed to fetch upstream releases "
                f"({_format_fetch_error(error)})",
                file=sys.stderr,
            )
            print(
                f"registry_update package={package_name} status=skipped "
                f"reason={package_state.get('reason_code')} reset_at={package_state.get('backoff_until')}",
                file=sys.stderr,
            )
            continue
        planned_for_config = plan_updates_for_config(
            config_path=config_path,
            releases_root=args.releases_root,
            releases=releases,
        )
        if planned_for_config:
            planned.extend(planned_for_config)
        else:
            up_to_date_packages += 1

    if not planned and not state_changed:
        if args.create_prs:
            _reconcile_empty_rolling_pr(
                repo_root=repo_root,
                base_branch=args.base_branch,
                branch_name=args.branch_name,
            )
        print(
            "No new releases detected; "
            f"skipped {skipped_fetches} release fetch(es)"
        )
        print(
            f"registry_update_summary updated=0 up_to_date={up_to_date_packages} "
            "quarantined=0 transient_failed=0 "
            f"skipped={skipped_fetches}"
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
            package_state = state_packages.setdefault(update.package, {})
            if not isinstance(package_state, dict):
                package_state = {}
                state_packages[update.package] = package_state
            last_good_version = package_state.get("last_successful_version")
            if not isinstance(last_good_version, str):
                last_good_version = None
            existing_quarantine = update.package in _object_map(bot_state.get("quarantine"))
            if quarantine_package(
                bot_state,
                package=update.package,
                reason_code="metadata-malformed",
                detail=str(exc),
                attempted_version=update.version,
                last_good_version=last_good_version,
            ):
                state_changed = True
                pr_material_state_changed = True
                if existing_quarantine:
                    quarantine_updated.append(update.package)
                else:
                    quarantine_added.append(update.package)
            skipped_updates += 1
            print(
                "Skipping "
                f"{update.package} {update.version}: incomplete upstream release ({exc})",
                file=sys.stderr,
            )
            print(
                f"registry_update package={update.package} status=quarantined "
                f"reason=metadata-malformed attempted={update.version}",
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
        staged_paths.append(release_path)

        package_state = state_packages.setdefault(update.package, {})
        if not isinstance(package_state, dict):
            package_state = {}
            state_packages[update.package] = package_state
        try:
            validate_package_generated_paths(
                repo_root=repo_root,
                package=update.package,
                staged_paths=staged_paths,
            )
        # Keep package validation failures isolated: restore staged writes,
        # record the error, quarantine this package, and continue others.
        except (subprocess.CalledProcessError, RuntimeError) as exc:
            if package_path in staged_paths and written_packages > 0:
                written_packages -= 1
            for generated_path in staged_paths:
                full_path = repo_root / generated_path
                if generated_path == package_path and current_package_text is not None:
                    full_path.write_text(current_package_text, encoding="utf-8")
                elif full_path.exists():
                    full_path.unlink()
            last_good_version = package_state.get("last_successful_version")
            if not isinstance(last_good_version, str):
                last_good_version = None
            existing_quarantine = update.package in _object_map(bot_state.get("quarantine"))
            if quarantine_package(
                bot_state,
                package=update.package,
                reason_code="metadata-malformed",
                detail=str(exc),
                attempted_version=update.version,
                last_good_version=last_good_version,
            ):
                state_changed = True
                pr_material_state_changed = True
                if existing_quarantine:
                    quarantine_updated.append(update.package)
                else:
                    quarantine_added.append(update.package)
            skipped_updates += 1
            print(
                "Skipping "
                f"{update.package} {update.version}: incomplete upstream release ({exc})",
                file=sys.stderr,
            )
            print(
                f"registry_update package={update.package} status=quarantined "
                f"reason=metadata-malformed attempted={update.version}",
                file=sys.stderr,
            )
            continue

        created_releases += 1
        package_state["last_successful_version"] = update.version
        package_state["last_generated_at"] = utc_now_iso()
        state_changed = True
        pr_material_state_changed = True
        if clear_quarantine(bot_state, package=update.package):
            state_changed = True
            pr_material_state_changed = True
            quarantine_cleared.append(update.package)
        updated_packages.append(f"{update.package}@{update.version}")
        for path in staged_paths:
            if path not in all_staged_paths:
                all_staged_paths.append(path)

    if args.create_prs and not planned and not pr_material_state_changed:
        _reconcile_empty_rolling_pr(
            repo_root=repo_root,
            base_branch=args.base_branch,
            branch_name=args.branch_name,
        )
        print(
            "No new releases detected; "
            f"skipped {skipped_fetches} release fetch(es)"
        )
        print(
            f"registry_update_summary updated=0 up_to_date={up_to_date_packages} "
            "quarantined=0 transient_failed=0 "
            f"skipped={skipped_fetches}"
        )
        return 0

    if state_changed and not args.dry_run:
        write_bot_state(args.state_path, bot_state)
        if args.state_path not in all_staged_paths:
            all_staged_paths.append(args.state_path)

    if args.create_prs and all_staged_paths:
        body = render_pr_body(
            updated_packages=updated_packages,
            quarantine_added=quarantine_added,
            quarantine_updated=quarantine_updated,
            quarantine_cleared=quarantine_cleared,
            backoff_packages=backoff_packages,
            state_changed=state_changed,
            created_releases=created_releases,
            written_packages=written_packages,
            quarantined_count=len(set([*quarantine_added, *quarantine_updated])),
            transient_failures=transient_fetch_failures,
            skipped_fetches=skipped_fetches,
        )
        _open_or_update_rolling_pr(
            repo_root=repo_root,
            staged_paths=all_staged_paths,
            base_branch=args.base_branch,
            branch_name=args.branch_name,
            title="chore(registry): update upstream releases",
            body=body,
        )

    if not planned:
        print(
            "No new releases detected; "
            f"skipped {skipped_fetches} release fetch(es)"
        )

    print(
        "Planned "
        f"{len(planned)} update(s), wrote {created_releases} release manifest(s), "
        f"updated {written_packages} package template(s), "
        f"skipped {skipped_updates} incomplete update(s), "
        f"skipped {skipped_fetches} release fetch(es)"
    )
    print(
        "registry_update_summary "
        f"updated={created_releases} "
        f"up_to_date={up_to_date_packages} "
        f"quarantined={len(set([*quarantine_added, *quarantine_updated]))} "
        f"transient_failed={transient_fetch_failures} "
        f"skipped={skipped_fetches}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
