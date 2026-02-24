#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import platform
import shutil
import sys
import tarfile
import tempfile
import tomllib
import urllib.request
import zipfile
from pathlib import Path, PurePosixPath


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


def choose_artifact(artifacts: list[dict]) -> dict:
    target = runner_target()
    if target:
        for artifact in artifacts:
            if artifact.get("target") == target:
                return artifact
    return artifacts[0]


def download(url: str, dest: Path) -> None:
    req = urllib.request.Request(url, headers={"User-Agent": "crosspack-registry-ci/1.0"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        with dest.open("wb") as out:
            shutil.copyfileobj(resp, out)


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
        mode = {"tar.gz": "r:gz", "tgz": "r:gz", "tar.xz": "r:xz"}[archive]
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
    else:
        raise ValueError(f"Unsupported archive format: {archive}")


def smoke_manifest(path: Path) -> tuple[bool, str]:
    doc = tomllib.loads(path.read_text(encoding="utf-8"))
    artifacts = doc.get("artifacts", [])
    if not artifacts:
        return False, f"{path}: no artifacts available for smoke-install"

    artifact = choose_artifact(artifacts)
    url = artifact["url"]
    expected_sha = artifact["sha256"].lower()
    archive = artifact.get("archive")
    strip_components = int(artifact.get("strip_components", 0))
    binaries = artifact.get("binaries", [])

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
                f"{path}: checksum mismatch for {url} (expected {expected_sha}, got {actual_sha})",
            )

        if archive:
            extract_archive(payload, install_root, archive, strip_components)
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
            return False, f"{path}: smoke-install failed, missing extracted binaries: {missing_csv}"

    target = artifact.get("target", "unknown")
    return True, f"{path}: smoke-install ok via target={target}"


def main() -> int:
    parser = argparse.ArgumentParser(description="Run smoke-install checks for registry manifests")
    parser.add_argument("manifests", nargs="+", help="Manifest paths to smoke-test")
    args = parser.parse_args()

    failures = []
    for manifest in args.manifests:
        path = Path(manifest)
        try:
            ok, message = smoke_manifest(path)
        except Exception as exc:  # noqa: BLE001
            ok, message = False, f"{path}: smoke-install crashed ({exc})"

        if ok:
            print(message)
        else:
            failures.append(message)

    if failures:
        print("Smoke-install checks failed:", file=sys.stderr)
        for failure in failures:
            print(f"  - {failure}", file=sys.stderr)
        return 1

    print(f"Smoke-install checks passed for {len(args.manifests)} manifest(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
