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
    "json_index",
    "text_endpoint",
}
CHECKSUM_KIND_VALUES = {"asset_digest", "download_sha256", "download_index", "shasums256", "url_sha256"}
ASSET_KIND_VALUES = {"json_index_asset", "release_asset_url", "templated"}


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


def _render_table(chunks: list[str], header: str, table: dict) -> None:
    chunks.append(header)
    for key, value in table.items():
        chunks.append(_line(key, value))
    chunks.append("")


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
            "version": {"kind": "github_tag"},
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
                "kind": "json_index",
                "url": "https://nodejs.org/dist/index.json",
            },
            "version": {
                "kind": "prefixed_semver_field",
                "field": "version",
                "prefix": "v",
                "require_prefix": f"v{major}.",
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

    _render_table(chunks, "[source.release]", release)
    version = normalized.get("version")
    if isinstance(version, dict):
        _render_table(chunks, "[source.version]", version)
    _render_table(chunks, "[source.checksum]", checksum)
    _render_table(chunks, "[source.asset]", asset)


def _read_checksum_url(url: str) -> str:
    with tempfile.TemporaryDirectory(prefix="manifest-gen-") as tmp:
        checksum_path = Path(tmp) / "artifact.sha256"
        download(url, checksum_path)
        checksum_parts = checksum_path.read_text(encoding="utf-8").split()
    if not checksum_parts:
        raise GenerateError(f"missing checksum from {url}")
    checksum = checksum_parts[0]
    if not re.fullmatch(r"[0-9a-fA-F]{64}", checksum):
        raise GenerateError(f"invalid checksum from {url}")
    return checksum


def _download_checksum(url: str, *, downloader) -> str:
    with tempfile.TemporaryDirectory(prefix="manifest-gen-") as tmp:
        artifact_path = Path(tmp) / "artifact"
        downloader(url, artifact_path)
        return sha256_file(artifact_path)


def _checksum_from_release_asset(*, release_asset: dict, asset_name: str) -> str:
    digest = release_asset.get("digest")
    if not isinstance(digest, str) or not digest.startswith("sha256:"):
        raise GenerateError(f"missing GitHub sha256 digest for asset '{asset_name}'")
    checksum = digest.removeprefix("sha256:")
    if not re.fullmatch(r"[0-9a-fA-F]{64}", checksum):
        raise GenerateError(f"invalid GitHub sha256 digest for asset '{asset_name}'")
    return checksum


def _find_json_index_asset(*, release: dict, asset: dict, asset_name: str, target: str) -> dict:
    array_field = asset.get("asset_array_field")
    if isinstance(array_field, str):
        entries = release.get(array_field)
        if not isinstance(entries, list):
            raise GenerateError(f"json index asset array '{array_field}' missing for target '{target}'")
        name_field = asset.get("name_field")
        if not isinstance(name_field, str) or not name_field:
            raise GenerateError("json_index_asset requires source.asset.name_field with asset_array_field")
        for entry in entries:
            if isinstance(entry, dict) and entry.get(name_field) == asset_name:
                return entry
        raise GenerateError(f"missing JSON index asset '{asset_name}' for target '{target}'")

    entry = release.get(asset_name)
    if not isinstance(entry, dict):
        raise GenerateError(f"missing JSON index asset '{asset_name}' for target '{target}'")
    return entry


def _build_generic_release_artifacts(
    *,
    config: dict,
    release: dict,
    version: str,
    downloader,
    shasums_by_name: dict[str, str] | None,
) -> list[dict]:
    normalized = normalize_source(config["source"])
    checksum = normalized["checksum"]
    checksum_kind = checksum.get("kind")
    asset = normalized["asset"]
    asset_kind = asset.get("kind")
    assets = _asset_map(release)
    release_artifacts: list[dict] = []

    for artifact in config.get("artifacts", []):
        target = artifact["target"]
        asset_name = artifact["asset"].format(version=version, target=target)

        release_asset: dict | None = None
        json_asset: dict | None = None
        if asset_kind == "release_asset_url":
            release_asset = assets.get(asset_name)
            if release_asset is None:
                raise GenerateError(f"missing release asset '{asset_name}' for target '{target}'")
            url = release_asset.get("browser_download_url")
        elif asset_kind == "templated":
            url_template = asset.get("url_template")
            if isinstance(url_template, str) and url_template:
                url = url_template.format(version=version, target=target, asset=asset_name)
            else:
                base_url = asset.get("base_url")
                if not isinstance(base_url, str) or not base_url.startswith("https://"):
                    raise GenerateError("templated asset config requires source.asset.base_url")
                url = f"{base_url.rstrip('/')}/{asset_name}"
        elif asset_kind == "json_index_asset":
            json_asset = _find_json_index_asset(
                release=release, asset=asset, asset_name=asset_name, target=target
            )
            url_field = asset.get("url_field")
            if isinstance(url_field, str):
                url = json_asset.get(url_field)
            else:
                url_template = asset.get("url_template")
                if not isinstance(url_template, str) or not url_template.startswith("https://"):
                    raise GenerateError("json_index_asset requires source.asset.url_template or url_field")
                url = url_template.format(version=version, target=target, asset=asset_name)
        else:
            raise GenerateError(f"unsupported asset strategy '{asset_kind}'")

        if not isinstance(url, str) or not url.startswith("https://"):
            raise GenerateError(f"invalid download URL for asset '{asset_name}'")

        if checksum_kind == "asset_digest":
            if release_asset is None:
                raise GenerateError("asset_digest checksum requires release_asset_url assets")
            sha256 = _checksum_from_release_asset(release_asset=release_asset, asset_name=asset_name)
        elif checksum_kind == "download_sha256":
            sha256 = _download_checksum(url, downloader=downloader)
        elif checksum_kind == "download_index":
            if json_asset is None:
                raise GenerateError("download_index checksum requires json_index_asset assets")
            checksum_field = asset.get("checksum_field")
            if not isinstance(checksum_field, str) or not checksum_field:
                raise GenerateError("download_index checksum requires source.asset.checksum_field")
            sha256 = json_asset.get(checksum_field)
        elif checksum_kind == "shasums256":
            if shasums_by_name is None:
                raise GenerateError("shasums256 generation requires shasums_by_name")
            sha256 = shasums_by_name.get(asset_name)
            if sha256 is None:
                raise GenerateError(f"missing SHASUMS256 entry '{asset_name}' for target '{target}'")
        elif checksum_kind == "url_sha256":
            checksum_template = checksum.get("url_template", "{url}.sha256")
            if not isinstance(checksum_template, str) or not checksum_template:
                raise GenerateError("url_sha256 checksum requires source.checksum.url_template")
            checksum_url = checksum_template.format(
                url=url, version=version, target=target, asset=asset_name
            )
            sha256 = _read_checksum_url(checksum_url)
        else:
            raise GenerateError(f"unsupported checksum strategy '{checksum_kind}'")

        if not isinstance(sha256, str) or not re.fullmatch(r"[0-9a-fA-F]{64}", sha256):
            raise GenerateError(f"invalid checksum for asset '{asset_name}'")
        release_artifacts.append({"target": target, "url": url, "sha256": sha256})

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
    release_artifacts = _build_generic_release_artifacts(
        config=config,
        release=release,
        version=version,
        downloader=downloader,
        shasums_by_name=shasums_by_name,
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
