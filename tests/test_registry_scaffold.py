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
        out_root = self.tmpdir / "registry"
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
        package_manifest = out_root / "packages" / "demo.toml"
        release_manifest = out_root / "releases" / "demo" / "1.2.3.toml"

        self.assertTrue(package_manifest.exists(), "package template should be created")
        self.assertTrue(release_manifest.exists(), "release manifest should be created")

        package_content = package_manifest.read_text(encoding="utf-8")
        self.assertIn('name = "demo"', package_content)
        self.assertIn('license = "TODO_LICENSE"', package_content)
        self.assertIn(
            'homepage = "https://example.invalid/TODO_HOMEPAGE"', package_content
        )
        self.assertIn("[source]", package_content)
        self.assertIn('provider = "github"', package_content)
        self.assertIn('repo = "TODO_OWNER/TODO_REPO"', package_content)
        self.assertIn('target = "x86_64-unknown-linux-gnu"', package_content)
        self.assertIn('asset = "demo-{version}.tar.gz"', package_content)

        release_content = release_manifest.read_text(encoding="utf-8")
        self.assertIn('name = "demo"', release_content)
        self.assertIn('version = "1.2.3"', release_content)
        self.assertIn('target = "x86_64-unknown-linux-gnu"', release_content)
        self.assertIn('url = "https://example.com/demo-1.2.3.tar.gz"', release_content)
        self.assertIn(
            'sha256 = "0000000000000000000000000000000000000000000000000000000000000000"',
            release_content,
        )

    def test_rejects_invalid_generated_output_before_write(self) -> None:
        out_root = self.tmpdir / "registry"
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
        release_manifest = out_root / "releases" / "demo" / "1.2.3.toml"
        package_manifest = out_root / "packages" / "demo.toml"
        self.assertFalse(
            release_manifest.exists(),
            "release manifest must not be written on validation failure",
        )
        self.assertFalse(
            package_manifest.exists(),
            "package template must not be written on validation failure",
        )

    def test_refuses_to_overwrite_existing_release_manifest_without_force(self) -> None:
        out_root = self.tmpdir / "registry"
        release_manifest = out_root / "releases" / "demo" / "1.2.3.toml"
        release_manifest.parent.mkdir(parents=True, exist_ok=True)
        release_manifest.write_text(
            'name = "demo"\nversion = "1.2.3"\n', encoding="utf-8"
        )

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

        self.assertNotEqual(result.returncode, 0)
        self.assertIn(
            "Refusing to overwrite existing release manifest",
            result.stderr,
        )
        self.assertEqual(
            release_manifest.read_text(encoding="utf-8"),
            'name = "demo"\nversion = "1.2.3"\n',
            "existing release manifest should remain unchanged",
        )

    def test_overwrites_existing_release_manifest_with_force(self) -> None:
        out_root = self.tmpdir / "registry"
        release_manifest = out_root / "releases" / "demo" / "1.2.3.toml"
        release_manifest.parent.mkdir(parents=True, exist_ok=True)
        release_manifest.write_text(
            'name = "demo"\nversion = "1.2.3"\n', encoding="utf-8"
        )

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
            "--force",
        )

        self.assertEqual(result.returncode, 0, msg=result.stderr)
        content = release_manifest.read_text(encoding="utf-8")
        self.assertIn(
            'sha256 = "0000000000000000000000000000000000000000000000000000000000000000"',
            content,
        )


if __name__ == "__main__":
    unittest.main()
