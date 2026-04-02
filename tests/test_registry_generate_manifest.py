import hashlib
import importlib.util
import tempfile
import textwrap
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = REPO_ROOT / "scripts" / "registry-generate-manifest.py"


def load_module():
    spec = importlib.util.spec_from_file_location(
        "registry_generate_manifest", SCRIPT_PATH
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load script module from {SCRIPT_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class RegistryGenerateManifestTests(unittest.TestCase):
    def setUp(self) -> None:
        self.generator = load_module()

    def test_generates_zoxide_release_manifest_with_downloaded_sha(self) -> None:
        with tempfile.TemporaryDirectory(prefix="manifest-gen-") as tmp:
            tmp_path = Path(tmp)
            config_path = tmp_path / "zoxide.toml"
            config_path.write_text(
                textwrap.dedent(
                    """
                    name = "zoxide"
                    license = "MIT"
                    homepage = "https://github.com/ajeetdsouza/zoxide"

                    [source]
                    provider = "github"
                    repo = "ajeetdsouza/zoxide"
                    tag_prefix = "v"

                    [[artifacts]]
                    target = "x86_64-unknown-linux-gnu"
                    asset = "zoxide-{version}-x86_64-unknown-linux-musl.tar.gz"
                    archive = "tar.gz"
                    strip_components = 0

                    [[artifacts.binaries]]
                    name = "zoxide"
                    path = "zoxide"
                    """
                ).strip()
                + "\n",
                encoding="utf-8",
            )

            release = {
                "tag_name": "v0.9.9",
                "assets": [
                    {
                        "name": "zoxide-0.9.9-x86_64-unknown-linux-musl.tar.gz",
                        "browser_download_url": "https://example.invalid/zoxide.tar.gz",
                    }
                ],
            }

            payload = b"zoxide-linux-payload"
            expected_sha = hashlib.sha256(payload).hexdigest()

            def fake_download(_url: str, dest: Path) -> None:
                dest.write_bytes(payload)

            rendered = self.generator.generate_release_text(
                config_path=config_path,
                version="0.9.9",
                release=release,
                downloader=fake_download,
            )

            self.assertIn('name = "zoxide"', rendered)
            self.assertIn('version = "0.9.9"', rendered)
            self.assertIn(
                'url = "https://example.invalid/zoxide.tar.gz"',
                rendered,
            )
            self.assertIn(f'sha256 = "{expected_sha}"', rendered)

    def test_generates_release_manifest_with_downloaded_sha(self) -> None:
        with tempfile.TemporaryDirectory(prefix="manifest-gen-") as tmp:
            tmp_path = Path(tmp)
            config_path = tmp_path / "ripgrep.toml"
            config_path.write_text(
                textwrap.dedent(
                    """
                    name = "ripgrep"
                    license = "MIT OR Unlicense"
                    homepage = "https://github.com/BurntSushi/ripgrep"

                    [source]
                    provider = "github"
                    repo = "BurntSushi/ripgrep"

                    [[artifacts]]
                    target = "x86_64-unknown-linux-gnu"
                    asset = "ripgrep-{version}-x86_64-unknown-linux-musl.tar.gz"
                    archive = "tar.gz"
                    strip_components = 1

                    [[artifacts.binaries]]
                    name = "rg"
                    path = "rg"
                    """
                ).strip()
                + "\n",
                encoding="utf-8",
            )

            release = {
                "tag_name": "15.1.0",
                "assets": [
                    {
                        "name": "ripgrep-15.1.0-x86_64-unknown-linux-musl.tar.gz",
                        "browser_download_url": "https://example.invalid/ripgrep.tar.gz",
                    }
                ],
            }

            payload = b"ripgrep-linux-payload"
            expected_sha = hashlib.sha256(payload).hexdigest()

            def fake_download(_url: str, dest: Path) -> None:
                dest.write_bytes(payload)

            rendered = self.generator.generate_release_text(
                config_path=config_path,
                version="15.1.0",
                release=release,
                downloader=fake_download,
            )

            self.assertIn('name = "ripgrep"', rendered)
            self.assertIn('version = "15.1.0"', rendered)
            self.assertIn(
                'url = "https://example.invalid/ripgrep.tar.gz"',
                rendered,
            )
            self.assertIn(f'sha256 = "{expected_sha}"', rendered)
            self.assertNotIn("license =", rendered)
            self.assertNotIn("homepage =", rendered)


if __name__ == "__main__":
    unittest.main()
