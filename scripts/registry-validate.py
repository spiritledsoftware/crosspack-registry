#!/usr/bin/env python3
from __future__ import annotations

import argparse
import re
import subprocess
import sys
import tempfile
import tomllib
from pathlib import Path

SEMVER_RE = re.compile(r"^\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?$")
SHA256_RE = re.compile(r"^[0-9a-fA-F]{64}$")
SIG_RE = re.compile(r"^[0-9a-fA-F]{128}$")
TARGET_RE = re.compile(r"^[A-Za-z0-9._-]+$")
REPO_RE = re.compile(r"^[^/\s]+/[^/\s]+$")
ARCHIVE_VALUES = {"tar.gz", "zip", "tar.xz", "tgz", "gz", "bin"}
RELEASE_KIND_VALUES = {
    "github_releases",
    "json_index",
    "text_endpoint",
}
VERSION_KIND_VALUES = {"asset_name_regex", "github_tag", "prefixed_semver_field", "regex_capture", "semver_field"}
CHECKSUM_KIND_VALUES = {"asset_digest", "download_sha256", "download_index", "shasums256", "url_sha256"}
ASSET_KIND_VALUES = {"json_index_asset", "release_asset_url", "templated"}
INTEGRATION_KIND_VALUES = {"docker_cli_plugin", "path_plugin", "service"}


def err(errors: list[str], path: Path, message: str) -> None:
    errors.append(f"{path}: {message}")


def load_manifest(path: Path, errors: list[str]):
    try:
        return tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as exc:
        err(errors, path, f"invalid TOML ({exc})")
    except OSError as exc:
        err(errors, path, f"cannot read file ({exc})")
    return None


def expect_nonempty_str(value, field: str, errors: list[str], path: Path) -> bool:
    if not isinstance(value, str) or not value.strip():
        err(errors, path, f"missing or invalid `{field}` (must be non-empty string)")
        return False
    return True


def is_relative_without_parent_segments(value: str) -> bool:
    candidate = Path(value)
    return not candidate.is_absolute() and ".." not in candidate.parts


def validate_common_artifact_template_fields(
    *, artifact: dict, prefix: str, path: Path, errors: list[str]
) -> None:
    target = artifact.get("target")
    if not isinstance(target, str) or not TARGET_RE.fullmatch(target):
        err(errors, path, f"{prefix}.target must match {TARGET_RE.pattern}")

    archive = artifact.get("archive")
    if archive is not None and archive not in ARCHIVE_VALUES:
        err(
            errors,
            path,
            f"{prefix}.archive must be one of {', '.join(sorted(ARCHIVE_VALUES))}",
        )

    strip_components = artifact.get("strip_components")
    if strip_components is not None and (
        isinstance(strip_components, bool)
        or not isinstance(strip_components, int)
        or strip_components < 0
    ):
        err(errors, path, f"{prefix}.strip_components must be an integer >= 0")


def validate_integrations(path: Path, doc: dict, errors: list[str]) -> None:
    integrations = doc.get("integrations", [])
    if not isinstance(integrations, list):
        err(errors, path, "integrations must be an array when present")
        return

    seen: set[str] = set()
    for idx, integration in enumerate(integrations, start=1):
        prefix = f"integrations[{idx}]"
        if not isinstance(integration, dict):
            err(errors, path, f"{prefix} must be a table")
            continue
        kind = integration.get("kind")
        if not isinstance(kind, str) or kind not in INTEGRATION_KIND_VALUES:
            err(errors, path, f"{prefix}.kind must be a supported integration kind")
            continue
        source = integration.get("source")
        if not isinstance(source, str) or not source.strip():
            err(errors, path, f"{prefix}.source must be a non-empty string")
        elif not is_relative_without_parent_segments(source):
            err(errors, path, f"{prefix}.source must be relative and must not contain '..'")
        name = integration.get("name")
        if not isinstance(name, str) or not name.strip():
            err(errors, path, f"{prefix}.name must be a non-empty string")
            continue
        if kind == "path_plugin":
            host = integration.get("host")
            if not isinstance(host, str) or not host.strip():
                err(errors, path, f"{prefix}.host must be a non-empty string")
                continue
            key = f"{kind}:{host}:{name}"
        else:
            key = f"{kind}:{name}"
        if kind == "service":
            enable = integration.get("enable")
            if enable is not None and not isinstance(enable, bool):
                err(errors, path, f"{prefix}.enable must be a boolean when present")
        if key in seen:
            err(errors, path, f"duplicate integration declaration `{key}`")
        seen.add(key)


def validate_package_manifest(path: Path, doc: dict, errors: list[str]) -> None:
    name_ok = expect_nonempty_str(doc.get("name"), "name", errors, path)
    expect_nonempty_str(doc.get("license"), "license", errors, path)
    homepage_ok = expect_nonempty_str(doc.get("homepage"), "homepage", errors, path)

    if homepage_ok and not str(doc["homepage"]).startswith("https://"):
        err(errors, path, "invalid `homepage` (must start with https://)")

    validate_integrations(path, doc, errors)

    source = doc.get("source")
    if not isinstance(source, dict):
        err(errors, path, "missing or invalid `source` (must be a table)")
    else:
        release = source.get("release")
        checksum = source.get("checksum")
        asset = source.get("asset")
        if isinstance(release, dict) and isinstance(checksum, dict) and isinstance(asset, dict):
            release_kind_ok = expect_nonempty_str(
                release.get("kind"), "source.release.kind", errors, path
            )
            release_kind = release.get("kind") if release_kind_ok else None
            if release_kind_ok and release_kind not in RELEASE_KIND_VALUES:
                err(
                    errors,
                    path,
                    "source.release.kind must be a supported release strategy",
                )
            elif release_kind == "github_releases":
                repo_ok = expect_nonempty_str(
                    release.get("repo"), "source.release.repo", errors, path
                )
                if repo_ok and not REPO_RE.fullmatch(str(release["repo"])):
                    err(errors, path, "source.release.repo must look like owner/name")
            elif release_kind in {"json_index", "text_endpoint"}:
                url_ok = expect_nonempty_str(release.get("url"), "source.release.url", errors, path)
                if url_ok and not str(release["url"]).startswith("https://"):
                    err(errors, path, "source.release.url must start with https://")
                if release_kind == "json_index":
                    entries = release.get("entries")
                    if entries is not None and entries not in {"array", "object_values"}:
                        err(errors, path, "source.release.entries must be 'array' or 'object_values'")
                    version_from_key = release.get("version_from_key")
                    if version_from_key is not None and not isinstance(version_from_key, bool):
                        err(errors, path, "source.release.version_from_key must be a boolean")
                    skip_keys = release.get("skip_keys")
                    if skip_keys is not None and (
                        not isinstance(skip_keys, list)
                        or not all(isinstance(item, str) and item for item in skip_keys)
                    ):
                        err(errors, path, "source.release.skip_keys must be a string array")
                    stable_field = release.get("stable_field")
                    if stable_field is not None and not isinstance(stable_field, str):
                        err(errors, path, "source.release.stable_field must be a string")
                    sort_semver_field = release.get("sort_semver_field")
                    if sort_semver_field is not None and not isinstance(sort_semver_field, str):
                        err(errors, path, "source.release.sort_semver_field must be a string")
                else:
                    expect_nonempty_str(release.get("version_regex"), "source.release.version_regex", errors, path)

            include_prereleases = release.get("include_prereleases")
            if include_prereleases is not None and not isinstance(
                include_prereleases, bool
            ):
                err(errors, path, "source.release.include_prereleases must be a boolean")

            tag_prefix = release.get("tag_prefix")
            if tag_prefix is not None and not isinstance(tag_prefix, str):
                err(errors, path, "source.release.tag_prefix must be a string")

            version = source.get("version")
            if isinstance(version, dict):
                version_kind_ok = expect_nonempty_str(
                    version.get("kind"), "source.version.kind", errors, path
                )
                version_kind = version.get("kind") if version_kind_ok else None
                if version_kind_ok and version_kind not in VERSION_KIND_VALUES:
                    err(errors, path, "source.version.kind must be a supported version strategy")
                if version_kind in {"prefixed_semver_field", "regex_capture", "semver_field"}:
                    expect_nonempty_str(version.get("field"), "source.version.field", errors, path)
                if version_kind in {"asset_name_regex", "regex_capture"}:
                    expect_nonempty_str(version.get("pattern"), "source.version.pattern", errors, path)
                for key in ("prefix", "require_prefix"):
                    if key in version and not isinstance(version[key], str):
                        err(errors, path, f"source.version.{key} must be a string")

            checksum_kind_ok = expect_nonempty_str(
                checksum.get("kind"), "source.checksum.kind", errors, path
            )
            checksum_kind = checksum.get("kind") if checksum_kind_ok else None
            if checksum_kind_ok and checksum_kind not in CHECKSUM_KIND_VALUES:
                err(
                    errors,
                    path,
                    "source.checksum.kind must be a supported checksum strategy",
                )
            elif checksum_kind in {"shasums256", "url_sha256"}:
                checksum_url_ok = expect_nonempty_str(
                    checksum.get("url_template"),
                    "source.checksum.url_template",
                    errors,
                    path,
                )
                if (
                    checksum_kind == "shasums256"
                    and checksum_url_ok
                    and not str(checksum["url_template"]).startswith("https://")
                ):
                    err(errors, path, "source.checksum.url_template must start with https://")

            asset_kind_ok = expect_nonempty_str(
                asset.get("kind"), "source.asset.kind", errors, path
            )
            asset_kind = asset.get("kind") if asset_kind_ok else None
            if asset_kind_ok and asset_kind not in ASSET_KIND_VALUES:
                err(
                    errors,
                    path,
                    "source.asset.kind must be a supported asset strategy",
                )
            elif asset_kind == "templated":
                if "url_template" in asset:
                    expect_nonempty_str(asset.get("url_template"), "source.asset.url_template", errors, path)
                else:
                    base_url_ok = expect_nonempty_str(
                        asset.get("base_url"), "source.asset.base_url", errors, path
                    )
                    if base_url_ok and not str(asset["base_url"]).startswith("https://"):
                        err(errors, path, "source.asset.base_url must start with https://")
            elif asset_kind == "json_index_asset":
                array_field = asset.get("asset_array_field")
                if array_field is not None:
                    if not isinstance(array_field, str) or not array_field:
                        err(errors, path, "source.asset.asset_array_field must be a string")
                    expect_nonempty_str(asset.get("name_field"), "source.asset.name_field", errors, path)
                if "url_field" in asset:
                    expect_nonempty_str(asset.get("url_field"), "source.asset.url_field", errors, path)
                else:
                    url_template_ok = expect_nonempty_str(
                        asset.get("url_template"), "source.asset.url_template", errors, path
                    )
                    if url_template_ok and not str(asset["url_template"]).startswith("https://"):
                        err(errors, path, "source.asset.url_template must start with https://")
                expect_nonempty_str(asset.get("checksum_field"), "source.asset.checksum_field", errors, path)
        else:
            provider_ok = expect_nonempty_str(
                source.get("provider"), "source.provider", errors, path
            )
            provider = source.get("provider") if provider_ok else None
            if provider == "github":
                repo_ok = expect_nonempty_str(source.get("repo"), "source.repo", errors, path)
                if repo_ok and not REPO_RE.fullmatch(str(source["repo"])):
                    err(errors, path, "source.repo must look like owner/name")
            elif provider == "nodejs-dist":
                major = source.get("major")
                if isinstance(major, bool) or not isinstance(major, int) or major <= 0:
                    err(errors, path, "source.major must be an integer > 0")
            elif provider_ok:
                err(errors, path, "source.provider must be 'github' or 'nodejs-dist'")

            include_prereleases = source.get("include_prereleases")
            if include_prereleases is not None and not isinstance(
                include_prereleases, bool
            ):
                err(errors, path, "source.include_prereleases must be a boolean")

            tag_prefix = source.get("tag_prefix")
            if tag_prefix is not None and not isinstance(tag_prefix, str):
                err(errors, path, "source.tag_prefix must be a string")

    if len(path.parts) < 2 or path.parts[-2] != "packages":
        err(errors, path, "package manifest must live under packages/<name>.toml")
    else:
        file_pkg = path.stem
        if name_ok and file_pkg != doc["name"]:
            err(
                errors,
                path,
                f"package filename `{file_pkg}` does not match `name` `{doc['name']}`",
            )
        release_dir = path.parent.parent / "releases" / file_pkg
        if not release_dir.is_dir():
            err(
                errors,
                path,
                f"missing release directory `{release_dir.as_posix()}` for package template",
            )

    artifacts = doc.get("artifacts")
    if not isinstance(artifacts, list) or not artifacts:
        err(errors, path, "missing or invalid `artifacts` (must be a non-empty array)")
        artifacts = []

    for idx, artifact in enumerate(artifacts, start=1):
        prefix = f"artifacts[{idx}]"
        if not isinstance(artifact, dict):
            err(errors, path, f"{prefix} must be a table")
            continue

        validate_common_artifact_template_fields(
            artifact=artifact,
            prefix=prefix,
            path=path,
            errors=errors,
        )

        asset = artifact.get("asset")
        if not isinstance(asset, str) or not asset.strip():
            err(errors, path, f"{prefix}.asset must be a non-empty string")

        binaries = artifact.get("binaries")
        if not isinstance(binaries, list) or not binaries:
            err(errors, path, f"{prefix}.binaries must be a non-empty array")
        else:
            for bidx, binary in enumerate(binaries, start=1):
                bprefix = f"{prefix}.binaries[{bidx}]"
                if not isinstance(binary, dict):
                    err(errors, path, f"{bprefix} must be a table")
                    continue

                bname = binary.get("name")
                bpath = binary.get("path")
                if not isinstance(bname, str) or not bname.strip():
                    err(errors, path, f"{bprefix}.name must be a non-empty string")
                if not isinstance(bpath, str) or not bpath.strip():
                    err(errors, path, f"{bprefix}.path must be a non-empty string")
                elif not is_relative_without_parent_segments(bpath):
                    err(
                        errors,
                        path,
                        f"{bprefix}.path must be relative and must not contain '..'",
                    )

        completions = artifact.get("completions", [])
        if not isinstance(completions, list):
            err(errors, path, f"{prefix}.completions must be an array when present")
        else:
            for cidx, completion in enumerate(completions, start=1):
                cprefix = f"{prefix}.completions[{cidx}]"
                if not isinstance(completion, dict):
                    err(errors, path, f"{cprefix} must be a table")
                    continue
                if (
                    not isinstance(completion.get("shell"), str)
                    or not str(completion.get("shell")).strip()
                ):
                    err(errors, path, f"{cprefix}.shell must be a non-empty string")
                completion_path = completion.get("path")
                if not isinstance(completion_path, str) or not completion_path.strip():
                    err(errors, path, f"{cprefix}.path must be a non-empty string")
                elif not is_relative_without_parent_segments(completion_path):
                    err(
                        errors,
                        path,
                        f"{cprefix}.path must be relative and must not contain '..'",
                    )

        gui_apps = artifact.get("gui_apps", [])
        if not isinstance(gui_apps, list):
            err(errors, path, f"{prefix}.gui_apps must be an array when present")
        else:
            for gidx, gui_app in enumerate(gui_apps, start=1):
                gprefix = f"{prefix}.gui_apps[{gidx}]"
                if not isinstance(gui_app, dict):
                    err(errors, path, f"{gprefix} must be a table")
                    continue

                for field in ("app_id", "display_name", "exec"):
                    field_value = gui_app.get(field)
                    if not isinstance(field_value, str) or not field_value.strip():
                        err(
                            errors,
                            path,
                            f"{gprefix}.{field} must be a non-empty string",
                        )

                categories = gui_app.get("categories")
                if not isinstance(categories, list) or not categories:
                    err(errors, path, f"{gprefix}.categories must be a non-empty array")
                else:
                    for cat_idx, category in enumerate(categories, start=1):
                        if not isinstance(category, str) or not category.strip():
                            err(
                                errors,
                                path,
                                f"{gprefix}.categories[{cat_idx}] must be a non-empty string",
                            )


def validate_release_manifest(path: Path, doc: dict, errors: list[str]) -> None:
    name_ok = expect_nonempty_str(doc.get("name"), "name", errors, path)
    version_ok = expect_nonempty_str(doc.get("version"), "version", errors, path)

    validate_integrations(path, doc, errors)

    if version_ok and not SEMVER_RE.fullmatch(str(doc["version"])):
        err(
            errors,
            path,
            f"invalid `version` format: {doc['version']!r} (expected semver)",
        )

    if len(path.parts) < 3 or path.parts[-3] != "releases":
        err(
            errors,
            path,
            "release manifest must live under releases/<name>/<version>.toml",
        )
    else:
        file_pkg = path.parent.name
        file_ver = path.stem
        if name_ok and file_pkg != doc["name"]:
            err(
                errors,
                path,
                f"release directory `{file_pkg}` does not match `name` `{doc['name']}`",
            )
        if version_ok and file_ver != doc["version"]:
            err(
                errors,
                path,
                f"release filename `{file_ver}` does not match `version` `{doc['version']}`",
            )

    artifacts = doc.get("artifacts")
    if not isinstance(artifacts, list) or not artifacts:
        err(errors, path, "missing or invalid `artifacts` (must be a non-empty array)")
        artifacts = []

    for idx, artifact in enumerate(artifacts, start=1):
        prefix = f"artifacts[{idx}]"
        if not isinstance(artifact, dict):
            err(errors, path, f"{prefix} must be a table")
            continue

        validate_common_artifact_template_fields(
            artifact=artifact,
            prefix=prefix,
            path=path,
            errors=errors,
        )

        url = artifact.get("url")
        if not isinstance(url, str) or not url.startswith("https://"):
            err(errors, path, f"{prefix}.url must start with https://")

        sha256 = artifact.get("sha256")
        if not isinstance(sha256, str) or not SHA256_RE.fullmatch(sha256):
            err(errors, path, f"{prefix}.sha256 must be 64 hex characters")


def validate_signature_sidecar(
    path: Path, errors: list[str], trusted_key_path: Path | None
) -> None:
    sig_path = path.with_suffix(path.suffix + ".sig")
    if not sig_path.exists():
        err(errors, path, f"missing signature sidecar `{sig_path.name}`")
        return

    try:
        sig_raw = sig_path.read_text(encoding="utf-8").strip()
    except OSError as exc:
        err(errors, path, f"cannot read signature sidecar ({exc})")
        return

    if not SIG_RE.fullmatch(sig_raw):
        err(
            errors,
            path,
            f"invalid signature format in `{sig_path.name}` (expected 128 hex characters)",
        )
        return

    if trusted_key_path is not None:
        verify_signature(path, sig_path, sig_raw, trusted_key_path, errors)


def verify_signature(
    path: Path, sig_path: Path, sig_raw: str, trusted_key_path: Path, errors: list[str]
) -> None:
    try:
        public_key_hex = trusted_key_path.read_text(encoding="utf-8").strip()
    except OSError as exc:
        err(errors, path, f"cannot read trusted registry key `{trusted_key_path}` ({exc})")
        return

    if not re.fullmatch(r"^[0-9a-fA-F]{64}$", public_key_hex):
        err(errors, trusted_key_path, "trusted registry key must be 64 hex characters")
        return

    try:
        # RFC 8410 Ed25519 SubjectPublicKeyInfo prefix followed by the 32-byte raw key.
        public_der = bytes.fromhex("302a300506032b6570032100" + public_key_hex)
        signature_bytes = bytes.fromhex(sig_raw)
    except ValueError as exc:
        err(errors, path, f"cannot decode signature verification material ({exc})")
        return

    with tempfile.TemporaryDirectory(prefix="registry-validate-sig-") as tmp:
        tmp_path = Path(tmp)
        public_der_path = tmp_path / "registry.pub.der"
        public_pem_path = tmp_path / "registry.pub.pem"
        signature_bin_path = tmp_path / sig_path.name
        public_der_path.write_bytes(public_der)
        signature_bin_path.write_bytes(signature_bytes)

        convert = subprocess.run(
            [
                "openssl",
                "pkey",
                "-pubin",
                "-inform",
                "DER",
                "-in",
                str(public_der_path),
                "-out",
                str(public_pem_path),
            ],
            text=True,
            capture_output=True,
        )
        if convert.returncode != 0:
            err(errors, trusted_key_path, f"cannot load trusted registry key ({convert.stderr.strip()})")
            return

        verify = subprocess.run(
            [
                "openssl",
                "pkeyutl",
                "-verify",
                "-rawin",
                "-pubin",
                "-inkey",
                str(public_pem_path),
                "-sigfile",
                str(signature_bin_path),
                "-in",
                str(path),
            ],
            text=True,
            capture_output=True,
        )
        if verify.returncode != 0:
            detail = verify.stderr.strip() or verify.stdout.strip() or "signature verification failed"
            err(errors, path, f"invalid signature sidecar `{sig_path.name}` ({detail})")


def validate_manifest(
    path: Path,
    errors: list[str],
    require_signatures: bool,
    trusted_key_path: Path | None,
) -> None:
    doc = load_manifest(path, errors)
    if doc is None:
        return

    if not isinstance(doc, dict):
        err(errors, path, "manifest root must be a table")
        return

    if len(path.parts) >= 2 and path.parts[-2] == "packages":
        validate_package_manifest(path, doc, errors)
    elif len(path.parts) >= 3 and path.parts[-3] == "releases":
        validate_release_manifest(path, doc, errors)
    else:
        err(
            errors,
            path,
            "manifest path must be under packages/ or releases/<name>/",
        )

    if require_signatures:
        validate_signature_sidecar(path, errors, trusted_key_path)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Validate Crosspack registry package and release manifests"
    )
    parser.add_argument(
        "--allow-missing-signatures",
        action="store_true",
        help="Skip required .toml.sig sidecar checks (for PR pre-merge validation)",
    )
    parser.add_argument(
        "--trusted-key",
        default="registry.pub",
        help="Trusted registry public key used to verify .toml.sig sidecars",
    )
    parser.add_argument("manifests", nargs="+", help="Manifest paths to validate")
    args = parser.parse_args()

    errors: list[str] = []
    manifest_paths = [Path(p) for p in args.manifests]
    trusted_key_path = None if args.allow_missing_signatures else Path(args.trusted_key)
    for path in manifest_paths:
        validate_manifest(
            path,
            errors,
            require_signatures=not args.allow_missing_signatures,
            trusted_key_path=trusted_key_path,
        )

    if errors:
        print("Registry manifest validation failed:", file=sys.stderr)
        for entry in errors:
            print(f"  - {entry}", file=sys.stderr)
        return 1

    if args.allow_missing_signatures:
        print(
            f"Validated {len(manifest_paths)} manifest(s): package/release schema and checksum checks passed."
        )
    else:
        print(
            f"Validated {len(manifest_paths)} manifest(s): package/release schema, checksum, and signature verification checks passed."
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
