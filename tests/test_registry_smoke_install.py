import hashlib
import importlib.util
import io
import urllib.error
import tempfile
import tarfile
import textwrap
import unittest
import zipfile
from email.message import Message
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = REPO_ROOT / "scripts" / "registry-smoke-install.py"


def load_module():
    spec = importlib.util.spec_from_file_location("registry_smoke_install", SCRIPT_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load script module from {SCRIPT_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class RegistrySmokeInstallTests(unittest.TestCase):
    def setUp(self) -> None:
        self.smoke = load_module()

    def test_missing_binary_failure_includes_package_binary_and_hint(self) -> None:
        with tempfile.TemporaryDirectory(prefix="smoke-test-") as tmp:
            tmp_path = Path(tmp)
            payload_path = tmp_path / "empty.zip"
            with zipfile.ZipFile(payload_path, "w"):
                pass
            payload_bytes = payload_path.read_bytes()
            payload_sha = hashlib.sha256(payload_bytes).hexdigest()

            manifest_path = tmp_path / "demo.toml"
            manifest_path.write_text(
                textwrap.dedent(
                    f"""
                    name = "demo"
                    version = "1.2.3"

                    [[artifacts]]
                    target = "x86_64-pc-windows-msvc"
                    url = "https://example.invalid/demo.zip"
                    sha256 = "{payload_sha}"
                    archive = "zip"
                    strip_components = 0

                    [[artifacts.binaries]]
                    name = "demo"
                    path = "demo.exe"
                    """
                ).strip()
                + "\n",
                encoding="utf-8",
            )

            def fake_download(_url: str, dest: Path) -> None:
                dest.write_bytes(payload_bytes)

            with mock.patch.object(self.smoke, "download", side_effect=fake_download):
                ok, message = self.smoke.smoke_manifest(manifest_path)

        self.assertFalse(ok)
        self.assertIn("demo@1.2.3", message)
        self.assertIn("demo.exe", message)
        self.assertIn("hint:", message.lower())

    def test_require_runner_target_reports_clear_failure(self) -> None:
        with tempfile.TemporaryDirectory(prefix="smoke-test-") as tmp:
            tmp_path = Path(tmp)
            payload_path = tmp_path / "demo.bin"
            payload_path.write_bytes(b"demo-binary")
            payload_sha = hashlib.sha256(payload_path.read_bytes()).hexdigest()

            manifest_path = tmp_path / "demo.toml"
            manifest_path.write_text(
                textwrap.dedent(
                    f"""
                    name = "demo"
                    version = "1.0.0"

                    [[artifacts]]
                    target = "definitely-not-this-runner"
                    url = "https://example.invalid/demo.bin"
                    sha256 = "{payload_sha}"

                    [[artifacts.binaries]]
                    name = "demo"
                    path = "demo"
                    """
                ).strip()
                + "\n",
                encoding="utf-8",
            )

            def fake_download(_url: str, dest: Path) -> None:
                dest.write_bytes(payload_path.read_bytes())

            with mock.patch.object(self.smoke, "download", side_effect=fake_download):
                ok, message = self.smoke.smoke_manifest(
                    manifest_path,
                    require_runner_target=True,
                )

        self.assertFalse(ok)
        self.assertIn("demo@1.0.0", message)
        self.assertIn("no artifact matched runner target=", message)

    def test_non_strict_mode_falls_back_to_first_artifact(self) -> None:
        with tempfile.TemporaryDirectory(prefix="smoke-test-") as tmp:
            tmp_path = Path(tmp)
            payload_path = tmp_path / "demo.bin"
            payload_path.write_bytes(b"demo-binary")
            payload_sha = hashlib.sha256(payload_path.read_bytes()).hexdigest()

            manifest_path = tmp_path / "demo.toml"
            manifest_path.write_text(
                textwrap.dedent(
                    f"""
                    name = "demo"
                    version = "1.0.0"

                    [[artifacts]]
                    target = "fallback-target"
                    url = "https://example.invalid/demo.bin"
                    sha256 = "{payload_sha}"

                    [[artifacts.binaries]]
                    name = "demo"
                    path = "demo"
                    """
                ).strip()
                + "\n",
                encoding="utf-8",
            )

            def fake_download(_url: str, dest: Path) -> None:
                dest.write_bytes(payload_path.read_bytes())

            with mock.patch.object(self.smoke, "download", side_effect=fake_download):
                ok, message = self.smoke.smoke_manifest(
                    manifest_path,
                    require_runner_target=False,
                )

        self.assertTrue(ok)
        self.assertIn("demo@1.0.0", message)
        self.assertIn("target=fallback-target", message)

    def test_release_manifest_uses_package_template_integrations(self) -> None:
        with tempfile.TemporaryDirectory(prefix="smoke-test-") as tmp:
            tmp_path = Path(tmp)
            payload_path = tmp_path / "demo.tar.gz"
            payload_root = tmp_path / "payload-root"
            (payload_root / "demo-1.0.0" / "bin").mkdir(parents=True)
            (payload_root / "demo-1.0.0" / "etc").mkdir(parents=True)
            (payload_root / "demo-1.0.0" / "bin" / "demo").write_bytes(b"demo")
            (payload_root / "demo-1.0.0" / "etc" / "demo.service").write_text(
                "[Service]\n",
                encoding="utf-8",
            )
            with tarfile.open(payload_path, "w:gz") as tf:
                tf.add(payload_root / "demo-1.0.0", arcname="demo-1.0.0")
            payload_bytes = payload_path.read_bytes()
            payload_sha = hashlib.sha256(payload_bytes).hexdigest()

            packages_dir = tmp_path / "packages"
            releases_dir = tmp_path / "releases" / "demo"
            packages_dir.mkdir()
            releases_dir.mkdir(parents=True)
            (packages_dir / "demo.toml").write_text(
                textwrap.dedent(
                    """
                    name = "demo"

                    [[integrations]]
                    kind = "service"
                    name = "demo"
                    source = "etc/demo.service"

                    [[artifacts]]
                    target = "fallback-target"
                    archive = "tar.gz"
                    strip_components = 1

                    [[artifacts.binaries]]
                    name = "demo"
                    path = "bin/demo"
                    """
                ).strip()
                + "\n",
                encoding="utf-8",
            )
            manifest_path = releases_dir / "1.0.0.toml"
            manifest_path.write_text(
                textwrap.dedent(
                    f"""
                    name = "demo"
                    version = "1.0.0"

                    [[artifacts]]
                    target = "fallback-target"
                    url = "https://example.invalid/demo.tar.gz"
                    sha256 = "{payload_sha}"
                    """
                ).strip()
                + "\n",
                encoding="utf-8",
            )

            def fake_download(_url: str, dest: Path) -> None:
                dest.write_bytes(payload_bytes)

            with mock.patch.object(self.smoke, "download", side_effect=fake_download):
                ok, message = self.smoke.smoke_manifest(manifest_path)

        self.assertTrue(ok, msg=message)
        self.assertIn("demo@1.0.0", message)

    def test_tar_extraction_rewrites_stripped_hardlink_targets(self) -> None:
        with tempfile.TemporaryDirectory(prefix="smoke-test-") as tmp:
            tmp_path = Path(tmp)
            payload_path = tmp_path / "payload.tar.gz"
            install_root = tmp_path / "install"
            with tarfile.open(payload_path, "w:gz") as tf:
                binary = b"clang"
                clang = tarfile.TarInfo("xpack-clang-1/bin/clang")
                clang.size = len(binary)
                clang.mode = 0o755
                tf.addfile(clang, io.BytesIO(binary))

                llvm_ml = tarfile.TarInfo("xpack-clang-1/bin/llvm-ml")
                llvm_ml.type = tarfile.LNKTYPE
                llvm_ml.linkname = "xpack-clang-1/bin/clang"
                tf.addfile(llvm_ml)

            self.smoke.extract_archive(payload_path, install_root, "tar.gz", 1)

            self.assertEqual((install_root / "bin" / "clang").read_bytes(), b"clang")
            self.assertTrue((install_root / "bin" / "llvm-ml").exists())

    def test_stripped_tar_member_clones_without_replace(self) -> None:
        member = tarfile.TarInfo("root/bin/tool")
        member.type = tarfile.SYMTYPE
        member.linkname = "tool-real"
        member.mode = 0o755
        member.size = 123
        member.mtime = 456
        member.uid = 7
        member.gid = 8
        member.uname = "builder"
        member.gname = "builders"
        member.devmajor = 9
        member.devminor = 10
        member.pax_headers = {"comment": "metadata"}

        stripped = self.smoke.stripped_tar_member(member, 1)

        self.assertIsNot(stripped, member)
        self.assertEqual(stripped.name, "bin/tool")
        self.assertEqual(stripped.type, tarfile.SYMTYPE)
        self.assertEqual(stripped.linkname, "tool-real")
        self.assertEqual(stripped.mode, 0o755)
        self.assertEqual(stripped.size, 123)
        self.assertEqual(stripped.mtime, 456)
        self.assertEqual(stripped.uid, 7)
        self.assertEqual(stripped.gid, 8)
        self.assertEqual(stripped.uname, "builder")
        self.assertEqual(stripped.gname, "builders")
        self.assertEqual(stripped.devmajor, 9)
        self.assertEqual(stripped.devminor, 10)
        self.assertEqual(stripped.pax_headers, {"comment": "metadata"})

    def test_download_retries_transient_http_error(self) -> None:
        with tempfile.TemporaryDirectory(prefix="smoke-test-") as tmp:
            dest = Path(tmp) / "payload"
            attempts = 0

            class FakeResponse:
                def __init__(self) -> None:
                    self._sent = False

                def __enter__(self):
                    return self

                def __exit__(self, exc_type, exc, tb):
                    return False

                def read(self, _size: int = -1) -> bytes:
                    if self._sent:
                        return b""
                    self._sent = True
                    return b"demo-payload"

            def fake_urlopen(_request, timeout):
                nonlocal attempts
                attempts += 1
                if attempts == 1:
                    raise urllib.error.HTTPError(
                        "https://example.invalid/demo.tar.gz",
                        502,
                        "Bad Gateway",
                        Message(),
                        None,
                    )
                return FakeResponse()

            with (
                mock.patch.object(self.smoke.urllib.request, "urlopen", side_effect=fake_urlopen),
                mock.patch.object(self.smoke.time, "sleep"),
            ):
                self.smoke.download("https://example.invalid/demo.tar.gz", dest)
            payload = dest.read_bytes()

        self.assertEqual(attempts, 2)
        self.assertEqual(payload, b"demo-payload")

    def test_download_fails_fast_for_non_transient_http_error(self) -> None:
        with tempfile.TemporaryDirectory(prefix="smoke-test-") as tmp:
            dest = Path(tmp) / "payload"

            with mock.patch.object(
                self.smoke.urllib.request,
                "urlopen",
                side_effect=urllib.error.HTTPError(
                    "https://example.invalid/demo.tar.gz",
                    404,
                    "Not Found",
                    Message(),
                    None,
                ),
            ) as urlopen:
                with self.assertRaises(urllib.error.HTTPError):
                    self.smoke.download("https://example.invalid/demo.tar.gz", dest)

        self.assertEqual(urlopen.call_count, 1)

    def test_app_bundle_canary_succeeds(self) -> None:
        ok, message = self.smoke.app_bundle_canary()

        self.assertTrue(ok)
        self.assertIn("app-bundle-canary: target=macOS status=verified", message)

    def test_main_accepts_app_bundle_canary_without_explicit_manifests(self) -> None:
        with mock.patch.object(
            self.smoke,
            "app_bundle_canary",
            return_value=(True, "app-bundle-canary: target=macOS status=verified"),
        ) as canary_mock:
            with mock.patch.object(self.smoke, "smoke_manifest") as smoke_mock:
                with mock.patch("sys.argv", ["registry-smoke-install.py", "--app-bundle-canary"]):
                    stdout = io.StringIO()
                    stderr = io.StringIO()
                    with redirect_stdout(stdout), redirect_stderr(stderr):
                        rc = self.smoke.main()

        self.assertEqual(rc, 0, msg=stderr.getvalue())
        canary_mock.assert_called_once_with()
        smoke_mock.assert_not_called()


if __name__ == "__main__":
    unittest.main()
