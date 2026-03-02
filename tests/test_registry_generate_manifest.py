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

    def test_generates_manifest_with_downloaded_sha(self) -> None:
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

            rendered = self.generator.generate_manifest_text(
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


if __name__ == "__main__":
    unittest.main()
