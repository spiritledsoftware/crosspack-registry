#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import re
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:
    import tomli as tomllib  # type: ignore[no-redef]


class GenerateError(Exception):
    pass


class DownloadError(Exception):
    pass


NODE_DIST_VERSION_RE = re.compile(r"^\d+\.\d+\.\d+$")
DOWNLOAD_ATTEMPTS = 3
RELEASE_KIND_VALUES = {
    "github_releases",
    "go_dist_index",
    "node_dist_index",
    "python_build_standalone",
    "rustup_static",
    "zig_download_index",
}
CHECKSUM_KIND_VALUES = {"download_sha256", "download_index", "shasums256", "url_sha256"}
ASSET_KIND_VALUES = {"download_index", "release_asset_url", "templated"}


def _escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def _line(key: str, value: object) -> str:
    if isinstance(value, str):
        return f'{key} = "{_escape(value)}"'
    if isinstance(value, bool):
        return f"{key} = {'true' if value else 'false'}"
    if isinstance(value, int):
        return f"{key} = {value}"
    if isinstance(value, list):
        str_values = ", ".join(f'"{_escape(str(v))}"' for v in value)
        return f"{key} = [{str_values}]"
    raise GenerateError(f"unsupported TOML value type for {key}")


def _render_artifact_templates(chunks: list[str], artifacts: list[dict]) -> None:
    for artifact in artifacts:
        chunks.append("[[artifacts]]")
        chunks.append(_line("target", artifact["target"]))
        chunks.append(_line("asset", artifact["asset"]))
        if "archive" in artifact:
            chunks.append(_line("archive", artifact["archive"]))
        if "strip_components" in artifact:
            chunks.append(_line("strip_components", artifact["strip_components"]))
        chunks.append("")

        for binary in artifact.get("binaries", []):
            chunks.append("[[artifacts.binaries]]")
            chunks.append(_line("name", binary["name"]))
            chunks.append(_line("path", binary["path"]))
        if artifact.get("binaries"):
            chunks.append("")

        for completion in artifact.get("completions", []):
            chunks.append("[[artifacts.completions]]")
            chunks.append(_line("shell", completion["shell"]))
            chunks.append(_line("path", completion["path"]))
            chunks.append("")

        for gui_app in artifact.get("gui_apps", []):
            chunks.append("[[artifacts.gui_apps]]")
            chunks.append(_line("app_id", gui_app["app_id"]))
            chunks.append(_line("display_name", gui_app["display_name"]))
            chunks.append(_line("exec", gui_app["exec"]))
            chunks.append(_line("categories", gui_app["categories"]))
            chunks.append("")


def render_package_text(doc: dict) -> str:
    chunks: list[str] = []
    chunks.append(_line("name", doc["name"]))
    chunks.append(_line("license", doc["license"]))
    chunks.append(_line("homepage", doc["homepage"]))
    chunks.append("")

    source = doc.get("source")
    if isinstance(source, dict):
        render_source_text(chunks, source)

    artifacts = doc.get("artifacts")
    if not isinstance(artifacts, list) or not artifacts:
        raise GenerateError("package template requires a non-empty artifacts array")

    _render_artifact_templates(chunks, artifacts)
    return "\n".join(chunks).rstrip() + "\n"


def render_release_text(doc: dict) -> str:
    chunks: list[str] = []
    chunks.append(_line("name", doc["name"]))
    chunks.append(_line("version", doc["version"]))
    chunks.append("")

    for artifact in doc["artifacts"]:
        chunks.append("[[artifacts]]")
        chunks.append(_line("target", artifact["target"]))
        chunks.append(_line("url", artifact["url"]))
        chunks.append(_line("sha256", artifact["sha256"]))
        chunks.append("")

    return "\n".join(chunks).rstrip() + "\n"


def download(url: str, dest: Path) -> None:
    last_error: Exception | None = None
    for attempt in range(DOWNLOAD_ATTEMPTS):
        req = urllib.request.Request(
            url,
            headers={
                "Accept": "application/octet-stream",
                "User-Agent": "crosspack-registry-upstream-bot",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as response:  # noqa: S310
                dest.write_bytes(response.read())
            return
        except urllib.error.HTTPError as error:
            if error.code < 500:
                raise DownloadError(
                    f"failed to download {url}: HTTP {error.code} {error.reason}"
                ) from error
            last_error = error
        except urllib.error.URLError as error:
            last_error = error

        if attempt < DOWNLOAD_ATTEMPTS - 1:
            time.sleep(2**attempt)

    if isinstance(last_error, urllib.error.HTTPError):
        raise DownloadError(
            "failed to download "
            f"{url} after {DOWNLOAD_ATTEMPTS} attempts: "
            f"HTTP {last_error.code} {last_error.reason}"
        ) from last_error
    raise DownloadError(
        f"failed to download {url} after {DOWNLOAD_ATTEMPTS} attempts: {last_error}"
    ) from last_error


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            block = handle.read(1024 * 1024)
            if not block:
                break
            digest.update(block)
    return digest.hexdigest()


def _asset_map(release: dict) -> dict[str, dict]:
    assets = release.get("assets", [])
    if not isinstance(assets, list):
        raise GenerateError("release.assets must be a list")
    out: dict[str, dict] = {}
    for asset in assets:
        if not isinstance(asset, dict):
            continue
        name = asset.get("name")
        if isinstance(name, str):
            out[name] = asset
    return out


def load_source_config(config_path: Path) -> dict:
    config = tomllib.loads(config_path.read_text(encoding="utf-8"))
    if not isinstance(config, dict):
        raise GenerateError("source config root must be a table")
    return config


def _build_nodejs_dist_base_url(*, major: int) -> str:
    return f"https://nodejs.org/dist/latest-v{major}.x"


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
            raise GenerateError("github source config requires source.repo")
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
            raise GenerateError("nodejs-dist source config requires source.major > 0")
        base_url = _build_nodejs_dist_base_url(major=major)
        return {
            "release": {
                "kind": "node_dist_index",
                "major": major,
                "include_prereleases": bool(source.get("include_prereleases", False)),
            },
            "checksum": {
                "kind": "shasums256",
                "url_template": f"{base_url}/SHASUMS256.txt",
            },
            "asset": {"kind": "templated", "base_url": base_url},
        }

    raise GenerateError(
        "source config must define either release/checksum/asset tables or a supported legacy provider"
    )


def render_source_text(chunks: list[str], source: dict) -> None:
    normalized = normalize_source(source)
    release = normalized["release"]
    checksum = normalized["checksum"]
    asset = normalized["asset"]

    chunks.append("[source.release]")
    chunks.append(_line("kind", release["kind"]))
    for key in ("repo", "tag_prefix", "include_prereleases", "major", "python_major_minor"):
        if key in release:
            chunks.append(_line(key, release[key]))
    chunks.append("")

    chunks.append("[source.checksum]")
    chunks.append(_line("kind", checksum["kind"]))
    if "url_template" in checksum:
        chunks.append(_line("url_template", checksum["url_template"]))
    chunks.append("")

    chunks.append("[source.asset]")
    chunks.append(_line("kind", asset["kind"]))
    if "base_url" in asset:
        chunks.append(_line("base_url", asset["base_url"]))
    chunks.append("")


def _build_nodejs_dist_release_artifacts(
    *,
    config: dict,
    version: str,
    shasums_by_name: dict[str, str] | None,
) -> list[dict]:
    source = config.get("source")
    if not isinstance(source, dict):
        raise GenerateError("source config requires a source table")
    normalized = normalize_source(source)
    release = normalized["release"]
    asset = normalized["asset"]
    major = release.get("major")
    if isinstance(major, bool) or not isinstance(major, int) or major <= 0:
        raise GenerateError("node dist release config requires release.major > 0")
    if not NODE_DIST_VERSION_RE.fullmatch(version):
        raise GenerateError("node dist releases require a normalized semver version")
    if shasums_by_name is None:
        raise GenerateError("node dist generation requires shasums_by_name")

    base_url = asset.get("base_url")
    if not isinstance(base_url, str) or not base_url.startswith("https://"):
        raise GenerateError("templated asset config requires source.asset.base_url")
    release_artifacts: list[dict] = []
    for artifact in config.get("artifacts", []):
        target = artifact["target"]
        asset_name = artifact["asset"].format(version=version, target=target)
        sha256 = shasums_by_name.get(asset_name)
        if sha256 is None:
            raise GenerateError(
                f"missing SHASUMS256 entry '{asset_name}' for target '{target}'"
            )
        release_artifacts.append(
            {
                "target": target,
                "url": f"{base_url}/{asset_name}",
                "sha256": sha256,
            }
        )

    return release_artifacts


def _build_go_dist_release_artifacts(*, config: dict, release: dict, version: str) -> list[dict]:
    files = release.get("files")
    if not isinstance(files, list):
        raise GenerateError("go dist release requires files")
    shasums_by_name = {
        file["filename"]: file["sha256"]
        for file in files
        if isinstance(file, dict)
        and isinstance(file.get("filename"), str)
        and isinstance(file.get("sha256"), str)
    }
    release_artifacts: list[dict] = []
    for artifact in config.get("artifacts", []):
        target = artifact["target"]
        asset_name = artifact["asset"].format(version=version, target=target)
        sha256 = shasums_by_name.get(asset_name)
        if sha256 is None:
            raise GenerateError(f"missing Go dist checksum '{asset_name}' for target '{target}'")
        release_artifacts.append(
            {"target": target, "url": f"https://go.dev/dl/{asset_name}", "sha256": sha256}
        )
    return release_artifacts


def _build_rustup_static_release_artifacts(*, config: dict, version: str) -> list[dict]:
    release_artifacts: list[dict] = []
    for artifact in config.get("artifacts", []):
        target = artifact["target"]
        asset_name = artifact["asset"].format(version=version, target=target)
        url = f"https://static.rust-lang.org/rustup/archive/{version}/{asset_name}"
        with tempfile.TemporaryDirectory(prefix="manifest-gen-") as tmp:
            checksum_path = Path(tmp) / "rustup.sha256"
            download(f"{url}.sha256", checksum_path)
            checksum_parts = checksum_path.read_text(encoding="utf-8").split()
        if not checksum_parts:
            raise GenerateError(f"missing rustup checksum for target '{target}'")
        release_artifacts.append({"target": target, "url": url, "sha256": checksum_parts[0]})
    return release_artifacts


def _build_zig_download_release_artifacts(*, config: dict, release: dict) -> list[dict]:
    release_artifacts: list[dict] = []
    for artifact in config.get("artifacts", []):
        target = artifact["target"]
        asset_key = artifact["asset"].format(version=release["version"], target=target)
        asset = release.get(asset_key)
        if not isinstance(asset, dict):
            raise GenerateError(f"missing Zig download index asset '{asset_key}' for target '{target}'")
        url = asset.get("tarball")
        sha256 = asset.get("shasum")
        if not isinstance(url, str) or not url.startswith("https://"):
            raise GenerateError(f"missing Zig download URL for target '{target}'")
        if not isinstance(sha256, str) or not re.fullmatch(r"[0-9a-fA-F]{64}", sha256):
            raise GenerateError(f"missing Zig checksum for target '{target}'")
        release_artifacts.append({"target": target, "url": url, "sha256": sha256})
    return release_artifacts


def _build_python_standalone_release_artifacts(
    *, config: dict, release: dict, version: str
) -> list[dict]:
    assets = _asset_map(release)
    release_artifacts: list[dict] = []
    for artifact in config.get("artifacts", []):
        target = artifact["target"]
        asset_name = artifact["asset"].format(version=version, target=target)
        release_asset = assets.get(asset_name)
        if release_asset is None:
            raise GenerateError(
                f"missing python-build-standalone asset '{asset_name}' for target '{target}'"
            )
        url = release_asset.get("browser_download_url")
        digest = release_asset.get("digest")
        if not isinstance(url, str) or not url.startswith("https://"):
            raise GenerateError(f"invalid download URL for asset '{asset_name}'")
        if not isinstance(digest, str) or not digest.startswith("sha256:"):
            raise GenerateError(f"missing GitHub sha256 digest for asset '{asset_name}'")
        release_artifacts.append(
            {"target": target, "url": url, "sha256": digest.removeprefix("sha256:")}
        )
    return release_artifacts


def generate_package_text(*, config_path: Path) -> str:
    return render_package_text(load_source_config(config_path))


def generate_release_text(
    *,
    config_path: Path,
    version: str,
    release: dict,
    downloader=download,
    shasums_by_name: dict[str, str] | None = None,
) -> str:
    config = load_source_config(config_path)
    source = config.get("source")
    if not isinstance(source, dict):
        raise GenerateError("source config root requires a source table")
    normalized = normalize_source(source)
    release_kind = normalized["release"].get("kind")
    if release_kind == "node_dist_index":
        release_artifacts = _build_nodejs_dist_release_artifacts(
            config=config,
            version=version,
            shasums_by_name=shasums_by_name,
        )
    elif release_kind == "go_dist_index":
        release_artifacts = _build_go_dist_release_artifacts(
            config=config, release=release, version=version
        )
    elif release_kind == "rustup_static":
        release_artifacts = _build_rustup_static_release_artifacts(config=config, version=version)
    elif release_kind == "zig_download_index":
        release_artifacts = _build_zig_download_release_artifacts(config=config, release=release)
    elif release_kind == "python_build_standalone":
        release_artifacts = _build_python_standalone_release_artifacts(
            config=config, release=release, version=version
        )
    else:
        assets = _asset_map(release)
        release_artifacts: list[dict] = []

        for artifact in config.get("artifacts", []):
            target = artifact["target"]
            asset_name = artifact["asset"].format(version=version, target=target)
            release_asset = assets.get(asset_name)
            if release_asset is None:
                raise GenerateError(
                    f"missing release asset '{asset_name}' for target '{target}'"
                )

            url = release_asset.get("browser_download_url")
            if not isinstance(url, str) or not url.startswith("https://"):
                raise GenerateError(f"invalid download URL for asset '{asset_name}'")

            with tempfile.TemporaryDirectory(prefix="manifest-gen-") as tmp:
                artifact_path = Path(tmp) / asset_name
                downloader(url, artifact_path)
                checksum = sha256_file(artifact_path)

            release_artifacts.append(
                {
                    "target": target,
                    "url": url,
                    "sha256": checksum,
                }
            )

    document = {
        "name": config["name"],
        "version": version,
        "artifacts": release_artifacts,
    }
    return render_release_text(document)


# Backward-compat helper for scripts importing the old symbol name.
generate_manifest_text = generate_release_text


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        description="Generate registry release manifest from source config"
    )
    parser.add_argument(
        "--config", required=True, type=Path, help="registry/sources/<pkg>.toml"
    )
    parser.add_argument("--version", required=True, help="Release version (semver)")
    parser.add_argument(
        "--release-json", required=True, type=Path, help="Path to release JSON"
    )
    parser.add_argument(
        "--output", required=True, type=Path, help="Output release manifest path"
    )
    args = parser.parse_args(argv)

    release = json.loads(args.release_json.read_text(encoding="utf-8"))
    text = generate_release_text(
        config_path=args.config,
        version=args.version,
        release=release,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(text, encoding="utf-8")
    print(f"Generated {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(__import__("sys").argv[1:]))
