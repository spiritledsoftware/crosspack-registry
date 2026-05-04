#!/usr/bin/env python3
from __future__ import annotations

import argparse
import gzip
import hashlib
import platform
import shutil
import sys
import tarfile
import tempfile
import time
import tomllib
import urllib.error
import urllib.request
import zipfile
from pathlib import Path, PurePosixPath


def fallback_manifest_identifier(path: Path) -> str:
    package = path.parent.name.strip() if path.parent.name else "unknown-package"
    version = path.stem.strip() if path.stem else "unknown-version"
    return f"{package}@{version}"


def manifest_identifier(doc: dict, path: Path) -> str:
    name = doc.get("name")
    version = doc.get("version")
    if (
        isinstance(name, str)
        and name.strip()
        and isinstance(version, str)
        and version.strip()
    ):
        return f"{name}@{version}"
    return fallback_manifest_identifier(path)


def best_effort_manifest_identifier(path: Path) -> str:
    try:
        doc = tomllib.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return fallback_manifest_identifier(path)
    return manifest_identifier(doc, path)


def failure_message(
    path: Path,
    package_id: str,
    reason: str,
    *,
    hint: str,
    failing_binary: str | None = None,
) -> str:
    message = f"{path}: {package_id}: smoke-install failed: {reason}"
    if failing_binary:
        message += f"; failing binary path: {failing_binary}"
    message += f"; remediation hint: {hint}"
    return message


def runner_target() -> str | None:
    system = platform.system().lower()
    machine = platform.machine().lower()

    if system == "linux" and machine in {"x86_64", "amd64"}:
        return "x86_64-unknown-linux-gnu"
    if system == "linux" and machine in {"aarch64", "arm64"}:
        return "aarch64-unknown-linux-gnu"
    if system == "darwin" and machine in {"x86_64", "amd64"}:
        return "x86_64-apple-darwin"
    if system == "darwin" and machine in {"aarch64", "arm64"}:
        return "aarch64-apple-darwin"
    if system == "windows" and machine in {"x86_64", "amd64"}:
        return "x86_64-pc-windows-msvc"
    if system == "windows" and machine in {"aarch64", "arm64"}:
        return "aarch64-pc-windows-msvc"
    return None


def choose_artifact(
    artifacts: list[dict],
    *,
    target: str | None,
    require_target: bool,
) -> dict | None:
    if target:
        for artifact in artifacts:
            if artifact.get("target") == target:
                return artifact
        if require_target:
            return None
    return artifacts[0]


def package_template_path_for_release(path: Path, doc: dict) -> Path | None:
    name = doc.get("name")
    if not isinstance(name, str) or not name.strip():
        return None
    if path.parent.parent.name != "releases":
        return None
    return path.parent.parent.parent / "packages" / f"{name}.toml"


def artifact_layout_from_package_template(path: Path, doc: dict, target: str) -> dict:
    package_template_path = package_template_path_for_release(path, doc)
    if package_template_path is None or not package_template_path.exists():
        return {}

    package_doc = tomllib.loads(package_template_path.read_text(encoding="utf-8"))
    for artifact in package_doc.get("artifacts", []):
        if artifact.get("target") == target:
            return artifact
    return {}


def integrations_from_manifest_or_package_template(path: Path, doc: dict) -> list[dict]:
    integrations = doc.get("integrations", [])
    if integrations:
        return integrations

    package_template_path = package_template_path_for_release(path, doc)
    if package_template_path is None or not package_template_path.exists():
        return []

    package_doc = tomllib.loads(package_template_path.read_text(encoding="utf-8"))
    return package_doc.get("integrations", [])


DOWNLOAD_ATTEMPTS = 3
TRANSIENT_HTTP_STATUS_CODES = {408, 429, 500, 502, 503, 504}


def is_transient_download_error(error: BaseException) -> bool:
    if isinstance(error, urllib.error.HTTPError):
        return error.code in TRANSIENT_HTTP_STATUS_CODES
    return isinstance(error, urllib.error.URLError)


def download(url: str, dest: Path) -> None:
    last_error: BaseException | None = None
    for attempt in range(DOWNLOAD_ATTEMPTS):
        req = urllib.request.Request(
            url, headers={"User-Agent": "crosspack-registry-ci/1.0"}
        )
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                with dest.open("wb") as out:
                    shutil.copyfileobj(resp, out)
            return
        except (urllib.error.HTTPError, urllib.error.URLError) as error:
            if not is_transient_download_error(error) or attempt == DOWNLOAD_ATTEMPTS - 1:
                raise
            last_error = error
            time.sleep(2**attempt)

    if last_error is not None:
        raise last_error
    raise RuntimeError(f"failed to download {url}")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def strip_name(name: str, strip_components: int) -> str | None:
    parts = [p for p in PurePosixPath(name).parts if p not in {"", "."}]
    if len(parts) <= strip_components:
        return None
    if parts[0] == "/" or ".." in parts:
        return None
    return str(PurePosixPath(*parts[strip_components:]))


def extract_archive(src: Path, dest: Path, archive: str, strip_components: int) -> None:
    if archive in {"tar.gz", "tgz", "tar.xz"}:
        if archive in {"tar.gz", "tgz"}:
            mode = "r:gz"
        else:
            mode = "r:xz"
        with tarfile.open(src, mode) as tf:
            for member in tf.getmembers():
                if not member.name:
                    continue
                stripped = strip_name(member.name, strip_components)
                if not stripped:
                    continue
                target = dest / stripped
                if member.isdir():
                    target.mkdir(parents=True, exist_ok=True)
                    continue

                parent = target.parent
                parent.mkdir(parents=True, exist_ok=True)
                extracted = tf.extractfile(member)
                if extracted is None:
                    continue
                with extracted, target.open("wb") as out:
                    shutil.copyfileobj(extracted, out)
                if member.mode & 0o111:
                    target.chmod(target.stat().st_mode | 0o755)
    elif archive == "zip":
        with zipfile.ZipFile(src) as zf:
            for member in zf.infolist():
                stripped = strip_name(member.filename, strip_components)
                if not stripped:
                    continue
                target = dest / stripped
                if member.is_dir():
                    target.mkdir(parents=True, exist_ok=True)
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                with zf.open(member, "r") as zsrc, target.open("wb") as out:
                    shutil.copyfileobj(zsrc, out)
    elif archive == "gz":
        target = dest / "payload"
        target.parent.mkdir(parents=True, exist_ok=True)
        with gzip.open(src, "rb") as zsrc, target.open("wb") as out:
            shutil.copyfileobj(zsrc, out)
        target.chmod(target.stat().st_mode | 0o755)
    elif archive == "bin":
        target = dest / "payload.bin"
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, target)
        target.chmod(target.stat().st_mode | 0o755)
    else:
        raise ValueError(f"Unsupported archive format: {archive}")


def smoke_manifest(
    path: Path, *, require_runner_target: bool = False
) -> tuple[bool, str]:
    doc = tomllib.loads(path.read_text(encoding="utf-8"))
    package_id = manifest_identifier(doc, path)
    artifacts = doc.get("artifacts", [])
    if not artifacts:
        return (
            False,
            failure_message(
                path,
                package_id,
                "no artifacts available for smoke-install",
                hint="add at least one [[artifacts]] entry with url, sha256, and binaries metadata",
            ),
        )

    resolved_runner_target = runner_target()
    artifact = choose_artifact(
        artifacts,
        target=resolved_runner_target,
        require_target=require_runner_target,
    )
    if artifact is None:
        expected_target = resolved_runner_target or "unknown-runner-target"
        return (
            False,
            failure_message(
                path,
                package_id,
                f"no artifact matched runner target={expected_target}",
                hint="add an artifact for this runner target or run without --require-runner-target",
            ),
        )

    artifact_target = artifact.get("target", "unknown")
    artifact_layout = artifact_layout_from_package_template(path, doc, artifact_target)
    url = artifact["url"]
    expected_sha = artifact["sha256"].lower()
    archive = artifact.get("archive", artifact_layout.get("archive"))
    strip_components = int(
        artifact.get("strip_components", artifact_layout.get("strip_components", 0))
    )
    binaries = artifact.get("binaries", artifact_layout.get("binaries", []))

    with tempfile.TemporaryDirectory(prefix="registry-smoke-") as tmp:
        tmpdir = Path(tmp)
        payload = tmpdir / "payload"
        install_root = tmpdir / "install"
        install_root.mkdir(parents=True, exist_ok=True)

        download(url, payload)
        actual_sha = sha256_file(payload)
        if actual_sha != expected_sha:
            return (
                False,
                failure_message(
                    path,
                    package_id,
                    f"checksum mismatch for {url} (expected {expected_sha}, got {actual_sha})",
                    hint="update artifacts[].sha256 to match the published asset bytes for this target",
                ),
            )

        if archive in {"tar.gz", "tgz", "tar.xz", "zip"}:
            extract_archive(payload, install_root, archive, strip_components)
        elif archive == "gz":
            if len(binaries) != 1:
                return (
                    False,
                    failure_message(
                        path,
                        package_id,
                        "gzip single-binary artifacts must define exactly one binary",
                        hint="use one artifacts[].binaries entry for archive='gz' artifacts",
                    ),
                )
            binary_path = Path(binaries[0]["path"])
            dest = install_root / binary_path
            dest.parent.mkdir(parents=True, exist_ok=True)
            with gzip.open(payload, "rb") as zsrc, dest.open("wb") as out:
                shutil.copyfileobj(zsrc, out)
            dest.chmod(dest.stat().st_mode | 0o755)
        elif archive == "bin":
            for binary in binaries:
                binary_path = Path(binary["path"])
                dest = install_root / binary_path
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(payload, dest)
                dest.chmod(dest.stat().st_mode | 0o755)
        else:
            for binary in binaries:
                binary_path = Path(binary["path"])
                dest = install_root / binary_path
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(payload, dest)
                dest.chmod(dest.stat().st_mode | 0o755)

        missing = []
        for binary in binaries:
            bpath = install_root / Path(binary["path"])
            if not bpath.exists() or not bpath.is_file():
                missing.append(binary["path"])

        if missing:
            missing_csv = ", ".join(missing)
            return (
                False,
                failure_message(
                    path,
                    package_id,
                    f"missing extracted binaries for target={artifact_target}: {missing_csv}",
                    failing_binary=missing_csv,
                    hint="verify artifacts[].binaries[].path and strip_components against the extracted archive layout",
                ),
            )

        missing_integrations = []
        for integration in integrations_from_manifest_or_package_template(path, doc):
            source = integration.get("source")
            if not isinstance(source, str) or not source.strip():
                continue
            source_path = install_root / Path(source)
            if not source_path.exists() or not source_path.is_file():
                missing_integrations.append(source)

        if missing_integrations:
            missing_csv = ", ".join(missing_integrations)
            return (
                False,
                failure_message(
                    path,
                    package_id,
                    f"missing extracted integration sources for target={artifact_target}: {missing_csv}",
                    hint="verify integrations[].source and strip_components against the extracted archive layout",
                ),
            )

    return True, f"{path}: {package_id}: smoke-install ok via target={artifact_target}"


def app_bundle_canary() -> tuple[bool, str]:
    with tempfile.TemporaryDirectory(prefix="registry-smoke-app-bundle-") as tmp:
        tmpdir = Path(tmp)
        payload = tmpdir / "neovide-style.zip"
        install_root = tmpdir / "install"
        install_root.mkdir(parents=True, exist_ok=True)

        with zipfile.ZipFile(payload, mode="w") as zf:
            zf.writestr("Neovide.app/Contents/MacOS/neovide", b"#!/bin/sh\nexit 0\n")

        extract_archive(payload, install_root, "zip", 0)
        expected = install_root / "Neovide.app" / "Contents" / "MacOS" / "neovide"
        if not expected.exists() or not expected.is_file():
            return (
                False,
                "app-bundle-canary: target=macOS reason=missing Neovide.app/Contents/MacOS/neovide after extraction",
            )

    return (
        True,
        "app-bundle-canary: target=macOS status=verified Neovide.app/Contents/MacOS/neovide extraction",
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run smoke-install checks for registry manifests"
    )
    parser.add_argument("manifests", nargs="*", help="Manifest paths to smoke-test")
    parser.add_argument(
        "--require-runner-target",
        action="store_true",
        help="Fail when a manifest has no artifact for the current runner target",
    )
    parser.add_argument(
        "--app-bundle-canary",
        action="store_true",
        help="Run local app-bundle extraction canary (.app/Contents/MacOS path)",
    )
    args = parser.parse_args()

    if not args.manifests and not args.app_bundle_canary:
        parser.error("provide at least one manifest path or --app-bundle-canary")

    deduped_manifests: list[Path] = []
    seen: set[str] = set()
    for manifest in args.manifests:
        key = str(manifest)
        if key in seen:
            continue
        seen.add(key)
        deduped_manifests.append(Path(manifest))

    failures = []
    completed_checks = 0

    for path in deduped_manifests:
        try:
            ok, message = smoke_manifest(
                path, require_runner_target=args.require_runner_target
            )
        except Exception as exc:  # noqa: BLE001
            ok, message = (
                False,
                failure_message(
                    path,
                    best_effort_manifest_identifier(path),
                    f"smoke-install crashed ({exc})",
                    hint="run scripts/registry-validate.py for the manifest and verify artifact metadata fields",
                ),
            )

        if ok:
            print(message)
            completed_checks += 1
        else:
            failures.append(message)

    if args.app_bundle_canary:
        try:
            ok, message = app_bundle_canary()
        except Exception as exc:  # noqa: BLE001
            ok, message = (
                False,
                f"app-bundle-canary: target=macOS reason=crashed ({exc})",
            )

        if ok:
            print(message)
            completed_checks += 1
        else:
            failures.append(message)

    if failures:
        print("Smoke-install checks failed:", file=sys.stderr)
        for failure in failures:
            print(f"  - {failure}", file=sys.stderr)
        return 1

    print(
        f"Smoke-install checks passed for {completed_checks} check(s) "
        f"({len(deduped_manifests)} manifest(s), app_bundle_canary={args.app_bundle_canary})."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
