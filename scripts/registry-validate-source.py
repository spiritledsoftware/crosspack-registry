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


ARCHIVE_VALUES = {"tar.gz", "zip", "tar.xz", "tgz", "gz", "bin"}
TARGET_RE = re.compile(r"^[A-Za-z0-9._-]+$")
REPO_RE = re.compile(r"^[^/\s]+/[^/\s]+$")
RELEASE_KIND_VALUES = {
    "github_releases",
    "json_index",
    "text_endpoint",
}
VERSION_KIND_VALUES = {"asset_name_regex", "github_tag", "prefixed_semver_field", "regex_capture", "semver_field"}
CHECKSUM_KIND_VALUES = {"asset_digest", "download_sha256", "download_index", "shasums256", "url_sha256"}
ASSET_KIND_VALUES = {"json_index_asset", "release_asset_url", "templated"}


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


def _expect_optional_bool(obj: dict, key: str, ctx: str) -> None:
    if key in obj and not isinstance(obj[key], bool):
        raise ValidationError(f"{ctx}.{key} must be a boolean")


def _expect_optional_str_array(obj: dict, key: str, ctx: str) -> None:
    if key not in obj:
        return
    value = obj[key]
    if not isinstance(value, list) or not all(isinstance(item, str) and item for item in value):
        raise ValidationError(f"{ctx}.{key} must be a string array")


def validate_source_config(doc: dict) -> None:
    _expect_non_empty_str(doc, "name", "manifest")
    _expect_non_empty_str(doc, "license", "manifest")
    homepage = _expect_non_empty_str(doc, "homepage", "manifest")
    if not homepage.startswith("https://"):
        raise ValidationError("manifest.homepage must start with https://")

    source = doc.get("source")
    if not isinstance(source, dict):
        raise ValidationError("manifest.source must be a table")

    release = source.get("release")
    checksum = source.get("checksum")
    asset = source.get("asset")

    if isinstance(release, dict) and isinstance(checksum, dict) and isinstance(asset, dict):
        release_kind = _expect_non_empty_str(release, "kind", "manifest.source.release")
        if release_kind not in RELEASE_KIND_VALUES:
            raise ValidationError(
                "manifest.source.release.kind must be a supported release strategy"
            )
        if release_kind == "github_releases":
            repo = _expect_non_empty_str(release, "repo", "manifest.source.release")
            if not REPO_RE.fullmatch(repo):
                raise ValidationError("manifest.source.release.repo must look like owner/name")
        elif release_kind in {"json_index", "text_endpoint"}:
            url = _expect_non_empty_str(release, "url", "manifest.source.release")
            if not url.startswith("https://"):
                raise ValidationError("manifest.source.release.url must start with https://")
            if release_kind == "json_index":
                entries = release.get("entries")
                if entries is not None and entries not in {"array", "object_values"}:
                    raise ValidationError("manifest.source.release.entries must be 'array' or 'object_values'")
                _expect_optional_bool(release, "version_from_key", "manifest.source.release")
                _expect_optional_str_array(release, "skip_keys", "manifest.source.release")
                stable_field = release.get("stable_field")
                if stable_field is not None and not isinstance(stable_field, str):
                    raise ValidationError("manifest.source.release.stable_field must be a string")
                sort_semver_field = release.get("sort_semver_field")
                if sort_semver_field is not None and not isinstance(sort_semver_field, str):
                    raise ValidationError("manifest.source.release.sort_semver_field must be a string")
            else:
                _expect_non_empty_str(release, "version_regex", "manifest.source.release")

        include_prereleases = release.get("include_prereleases")
        if include_prereleases is not None and not isinstance(include_prereleases, bool):
            raise ValidationError("manifest.source.release.include_prereleases must be a boolean")

        tag_prefix = release.get("tag_prefix")
        if tag_prefix is not None and not isinstance(tag_prefix, str):
            raise ValidationError("manifest.source.release.tag_prefix must be a string")

        version = source.get("version")
        if isinstance(version, dict):
            version_kind = _expect_non_empty_str(version, "kind", "manifest.source.version")
            if version_kind not in VERSION_KIND_VALUES:
                raise ValidationError("manifest.source.version.kind must be a supported version strategy")
            if version_kind in {"prefixed_semver_field", "regex_capture", "semver_field"}:
                _expect_non_empty_str(version, "field", "manifest.source.version")
            if version_kind in {"asset_name_regex", "regex_capture"}:
                _expect_non_empty_str(version, "pattern", "manifest.source.version")
            for key in ("prefix", "require_prefix"):
                value = version.get(key)
                if value is not None and not isinstance(value, str):
                    raise ValidationError(f"manifest.source.version.{key} must be a string")

        checksum_kind = _expect_non_empty_str(checksum, "kind", "manifest.source.checksum")
        if checksum_kind not in CHECKSUM_KIND_VALUES:
            raise ValidationError(
                "manifest.source.checksum.kind must be a supported checksum strategy"
            )
        if checksum_kind in {"shasums256", "url_sha256"}:
            url_template = _expect_non_empty_str(
                checksum, "url_template", "manifest.source.checksum"
            )
            if checksum_kind == "shasums256" and not url_template.startswith("https://"):
                raise ValidationError(
                    "manifest.source.checksum.url_template must start with https://"
                )

        asset_kind = _expect_non_empty_str(asset, "kind", "manifest.source.asset")
        if asset_kind not in ASSET_KIND_VALUES:
            raise ValidationError(
                "manifest.source.asset.kind must be a supported asset strategy"
            )
        if asset_kind == "templated":
            if "url_template" in asset:
                _expect_non_empty_str(asset, "url_template", "manifest.source.asset")
            else:
                base_url = _expect_non_empty_str(asset, "base_url", "manifest.source.asset")
                if not base_url.startswith("https://"):
                    raise ValidationError("manifest.source.asset.base_url must start with https://")
        elif asset_kind == "json_index_asset":
            array_field = asset.get("asset_array_field")
            if array_field is not None:
                if not isinstance(array_field, str) or not array_field:
                    raise ValidationError("manifest.source.asset.asset_array_field must be a string")
                _expect_non_empty_str(asset, "name_field", "manifest.source.asset")
            if "url_field" in asset:
                _expect_non_empty_str(asset, "url_field", "manifest.source.asset")
            else:
                url_template = _expect_non_empty_str(asset, "url_template", "manifest.source.asset")
                if not url_template.startswith("https://"):
                    raise ValidationError("manifest.source.asset.url_template must start with https://")
            _expect_non_empty_str(asset, "checksum_field", "manifest.source.asset")
    else:
        provider = _expect_non_empty_str(source, "provider", "manifest.source")
        if provider == "github":
            repo = _expect_non_empty_str(source, "repo", "manifest.source")
            if not REPO_RE.fullmatch(repo):
                raise ValidationError("manifest.source.repo must look like owner/name")
        elif provider == "nodejs-dist":
            major = source.get("major")
            if isinstance(major, bool) or not isinstance(major, int) or major <= 0:
                raise ValidationError("manifest.source.major must be an integer > 0")
        else:
            raise ValidationError("manifest.source.provider must be 'github' or 'nodejs-dist'")

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
        description="Validate registry package source configuration"
    )
    parser.add_argument("configs", nargs="+", type=Path, help="packages/*.toml")
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

    if errors:
        print("Package source validation failed:", file=sys.stderr)
        for error in errors:
            print(f"  - {error}", file=sys.stderr)
        return 1

    print(f"Validation passed: {len(args.configs)} package source config(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
