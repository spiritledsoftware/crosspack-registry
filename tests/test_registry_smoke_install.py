import hashlib
import importlib.util
import io
import tempfile
import textwrap
import unittest
import zipfile
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

    def test_main_accepts_app_bundle_canary_without_explicit_manifests(self) -> None:
        canary_calls = []

        def fake_smoke_manifest(path: Path) -> tuple[bool, str]:
            canary_calls.append(path)
            return True, f"{path}: smoke-install ok"

        with mock.patch.object(self.smoke, "smoke_manifest", side_effect=fake_smoke_manifest):
            with mock.patch("sys.argv", ["registry-smoke-install.py", "--app-bundle-canary"]):
                stdout = io.StringIO()
                stderr = io.StringIO()
                with redirect_stdout(stdout), redirect_stderr(stderr):
                    rc = self.smoke.main()

        self.assertEqual(rc, 0, msg=stderr.getvalue())
        self.assertEqual(len(canary_calls), 1, msg=f"unexpected calls: {canary_calls}")
        self.assertEqual(canary_calls[0], REPO_ROOT / "index" / "jq" / "1.8.1.toml")


if __name__ == "__main__":
    unittest.main()
