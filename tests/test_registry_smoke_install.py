import hashlib
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts" / "registry-smoke-install.py"


class RegistrySmokeInstallTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = Path(tempfile.mkdtemp(prefix="smoke-install-test-"))

    def tearDown(self) -> None:
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def run_smoke(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["python3", str(SCRIPT), *args],
            cwd=REPO_ROOT,
            text=True,
            capture_output=True,
            env={**os.environ, "LC_ALL": "C"},
        )

    def test_app_bundle_canary_succeeds(self) -> None:
        result = self.run_smoke("--app-bundle-canary")

        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertIn("app-bundle-canary: target=macOS status=verified", result.stdout)

    def test_require_runner_target_reports_clear_failure(self) -> None:
        payload = self.tmpdir / "demo.bin"
        payload.write_bytes(b"demo-binary")
        digest = hashlib.sha256(payload.read_bytes()).hexdigest()

        manifest = self.tmpdir / "demo" / "1.0.0.toml"
        manifest.parent.mkdir(parents=True, exist_ok=True)
        manifest.write_text(
            "\n".join(
                [
                    'name = "demo"',
                    'version = "1.0.0"',
                    "",
                    "[[artifacts]]",
                    'target = "definitely-not-this-runner"',
                    f'url = "{payload.as_uri()}"',
                    f'sha256 = "{digest}"',
                    "",
                    "[[artifacts.binaries]]",
                    'name = "demo"',
                    'path = "demo"',
                    "",
                ]
            ),
            encoding="utf-8",
        )

        result = self.run_smoke("--require-runner-target", str(manifest))

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("demo@1.0.0", result.stderr)
        self.assertIn("target=", result.stderr)
        self.assertIn("reason=no artifact matched runner target", result.stderr)

    def test_non_strict_mode_falls_back_to_first_artifact(self) -> None:
        payload = self.tmpdir / "demo.bin"
        payload.write_bytes(b"demo-binary")
        digest = hashlib.sha256(payload.read_bytes()).hexdigest()

        manifest = self.tmpdir / "demo" / "1.0.0.toml"
        manifest.parent.mkdir(parents=True, exist_ok=True)
        manifest.write_text(
            "\n".join(
                [
                    'name = "demo"',
                    'version = "1.0.0"',
                    "",
                    "[[artifacts]]",
                    'target = "fallback-target"',
                    f'url = "{payload.as_uri()}"',
                    f'sha256 = "{digest}"',
                    "",
                    "[[artifacts.binaries]]",
                    'name = "demo"',
                    'path = "demo"',
                    "",
                ]
            ),
            encoding="utf-8",
        )

        result = self.run_smoke(str(manifest))

        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertIn("demo@1.0.0", result.stdout)
        self.assertIn("artifact=fallback-target", result.stdout)
        self.assertIn("status=smoke-install ok", result.stdout)


if __name__ == "__main__":
    unittest.main()
