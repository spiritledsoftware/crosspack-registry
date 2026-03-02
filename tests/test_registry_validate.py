import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts" / "registry-validate.py"


class RegistryValidateTests(unittest.TestCase):
    def test_valid_package_and_release_docs_pass(self) -> None:
        with tempfile.TemporaryDirectory(prefix="registry-validate-") as tmp:
            tmp_path = Path(tmp)
            package = tmp_path / "packages" / "demo.toml"
            release = tmp_path / "releases" / "demo" / "1.2.3.toml"
            package.parent.mkdir(parents=True, exist_ok=True)
            release.parent.mkdir(parents=True, exist_ok=True)

            package.write_text(
                textwrap.dedent(
                    """
                    name = "demo"
                    license = "MIT"
                    homepage = "https://example.com/demo"

                    [source]
                    provider = "github"
                    repo = "example/demo"

                    [[artifacts]]
                    target = "x86_64-unknown-linux-gnu"
                    asset = "demo-{version}-x86_64-unknown-linux-gnu.tar.gz"
                    archive = "tar.gz"
                    strip_components = 1

                    [[artifacts.binaries]]
                    name = "demo"
                    path = "demo"
                    """
                ).strip()
                + "\n",
                encoding="utf-8",
            )

            release.write_text(
                textwrap.dedent(
                    """
                    name = "demo"
                    version = "1.2.3"

                    [[artifacts]]
                    target = "x86_64-unknown-linux-gnu"
                    url = "https://example.com/demo-1.2.3.tar.gz"
                    sha256 = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
                    """
                ).strip()
                + "\n",
                encoding="utf-8",
            )

            result = subprocess.run(
                [
                    "python3",
                    str(SCRIPT),
                    "--allow-missing-signatures",
                    str(package),
                    str(release),
                ],
                cwd=REPO_ROOT,
                text=True,
                capture_output=True,
            )

        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertIn("Validated 2 manifest(s)", result.stdout)

    def test_release_path_name_mismatch_fails(self) -> None:
        with tempfile.TemporaryDirectory(prefix="registry-validate-") as tmp:
            tmp_path = Path(tmp)
            release = tmp_path / "releases" / "demo" / "1.2.3.toml"
            release.parent.mkdir(parents=True, exist_ok=True)
            release.write_text(
                textwrap.dedent(
                    """
                    name = "other"
                    version = "1.2.3"

                    [[artifacts]]
                    target = "x86_64-unknown-linux-gnu"
                    url = "https://example.com/demo-1.2.3.tar.gz"
                    sha256 = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
                    """
                ).strip()
                + "\n",
                encoding="utf-8",
            )

            result = subprocess.run(
                ["python3", str(SCRIPT), "--allow-missing-signatures", str(release)],
                cwd=REPO_ROOT,
                text=True,
                capture_output=True,
            )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("does not match `name`", result.stderr)


if __name__ == "__main__":
    unittest.main()
