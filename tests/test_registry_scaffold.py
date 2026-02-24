import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts" / "registry-scaffold-entry.sh"


class RegistryScaffoldTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = Path(tempfile.mkdtemp(prefix="scaffold-test-"))

    def tearDown(self) -> None:
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def run_scaffold(self, *args: str) -> subprocess.CompletedProcess[str]:
        cmd = [str(SCRIPT), *args]
        return subprocess.run(
            cmd,
            cwd=REPO_ROOT,
            text=True,
            capture_output=True,
            env={**os.environ, "LC_ALL": "C"},
        )

    def test_generates_template_entry(self) -> None:
        out_root = self.tmpdir / "index"
        result = self.run_scaffold(
            "--name",
            "demo",
            "--version",
            "1.2.3",
            "--target",
            "x86_64-unknown-linux-gnu",
            "--url",
            "https://example.com/demo-1.2.3.tar.gz",
            "--output-root",
            str(out_root),
        )

        self.assertEqual(result.returncode, 0, msg=result.stderr)
        manifest = out_root / "demo" / "1.2.3.toml"
        self.assertTrue(manifest.exists(), "manifest should be created")
        content = manifest.read_text(encoding="utf-8")
        self.assertIn('name = "demo"', content)
        self.assertIn('version = "1.2.3"', content)
        self.assertIn('target = "x86_64-unknown-linux-gnu"', content)
        self.assertIn('url = "https://example.com/demo-1.2.3.tar.gz"', content)
        self.assertIn('sha256 = "TODO_SHA256"', content)
        self.assertIn('[source]', content)
        self.assertIn('checksum = "TODO_SOURCE_CHECKSUM"', content)
        self.assertIn('signature = "TODO_SOURCE_SIGNATURE"', content)

    def test_rejects_invalid_generated_output_before_write(self) -> None:
        out_root = self.tmpdir / "index"
        result = self.run_scaffold(
            "--name",
            "demo",
            "--version",
            "1.2.3",
            "--target",
            "x86_64-unknown-linux-gnu",
            "--url",
            "   ",  # invalid per schema after trim: non-empty string required
            "--output-root",
            str(out_root),
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Validation failed", result.stderr)
        manifest = out_root / "demo" / "1.2.3.toml"
        self.assertFalse(manifest.exists(), "manifest must not be written on validation failure")


if __name__ == "__main__":
    unittest.main()
