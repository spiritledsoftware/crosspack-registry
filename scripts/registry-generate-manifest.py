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
        chunks.append("[source]")
        chunks.append(_line("provider", source["provider"]))
        if "repo" in source:
            chunks.append(_line("repo", source["repo"]))
        if "major" in source:
            chunks.append(_line("major", source["major"]))
        if "tag_prefix" in source:
            chunks.append(_line("tag_prefix", source["tag_prefix"]))
        if "include_prereleases" in source:
            chunks.append(_line("include_prereleases", source["include_prereleases"]))
        chunks.append("")

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


def _build_nodejs_dist_release_artifacts(
    *,
    config: dict,
    version: str,
    shasums_by_name: dict[str, str] | None,
) -> list[dict]:
    source = config.get("source")
    if not isinstance(source, dict):
        raise GenerateError("nodejs-dist source config requires a source table")
    major = source.get("major")
    if isinstance(major, bool) or not isinstance(major, int) or major <= 0:
        raise GenerateError("nodejs-dist source config requires source.major > 0")
    if not NODE_DIST_VERSION_RE.fullmatch(version):
        raise GenerateError("nodejs-dist releases require a normalized semver version")
    if shasums_by_name is None:
        raise GenerateError("nodejs-dist generation requires shasums_by_name")

    base_url = _build_nodejs_dist_base_url(major=major)
    release_artifacts: list[dict] = []
    for artifact in config.get("artifacts", []):
        target = artifact["target"]
        asset_name = artifact["asset"].format(version=version)
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

    if source.get("provider") == "nodejs-dist":
        release_artifacts = _build_nodejs_dist_release_artifacts(
            config=config,
            version=version,
            shasums_by_name=shasums_by_name,
        )
    else:
        assets = _asset_map(release)
        release_artifacts: list[dict] = []

        for artifact in config.get("artifacts", []):
            target = artifact["target"]
            asset_name = artifact["asset"].format(version=version)
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
