import hashlib
import importlib.util
import tempfile
import textwrap
import unittest
import urllib.error
from pathlib import Path
from unittest import mock


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

    def test_download_retries_transient_http_error(self) -> None:
        with tempfile.TemporaryDirectory(prefix="manifest-gen-") as tmp:
            dest = Path(tmp) / "artifact.tar.gz"
            payload = b"artifact-payload"
            failures = [
                urllib.error.HTTPError(
                    "https://example.invalid/artifact.tar.gz",
                    502,
                    "Bad Gateway",
                    hdrs=None,
                    fp=None,
                )
            ]

            class FakeResponse:
                def __enter__(self):
                    return self

                def __exit__(self, _exc_type, _exc, _traceback):
                    return False

                def read(self) -> bytes:
                    return payload

            def fake_urlopen(_req, timeout: int):
                self.assertEqual(timeout, 30)
                if failures:
                    raise failures.pop()
                return FakeResponse()

            with mock.patch.object(
                self.generator.urllib.request, "urlopen", side_effect=fake_urlopen
            ) as urlopen, mock.patch.object(self.generator.time, "sleep") as sleep:
                self.generator.download("https://example.invalid/artifact.tar.gz", dest)

            self.assertEqual(dest.read_bytes(), payload)
            self.assertEqual(urlopen.call_count, 2)
            sleep.assert_called_once_with(1)

    def test_download_fails_fast_for_non_transient_http_error(self) -> None:
        with tempfile.TemporaryDirectory(prefix="manifest-gen-") as tmp:
            dest = Path(tmp) / "artifact.tar.gz"
            error = urllib.error.HTTPError(
                "https://example.invalid/missing.tar.gz",
                404,
                "Not Found",
                hdrs=None,
                fp=None,
            )

            with mock.patch.object(
                self.generator.urllib.request, "urlopen", side_effect=error
            ) as urlopen, mock.patch.object(self.generator.time, "sleep") as sleep:
                with self.assertRaisesRegex(
                    self.generator.DownloadError,
                    "https://example.invalid/missing.tar.gz.*HTTP 404 Not Found",
                ):
                    self.generator.download("https://example.invalid/missing.tar.gz", dest)

            self.assertFalse(dest.exists())
            self.assertEqual(urlopen.call_count, 1)
            sleep.assert_not_called()

    def test_download_reports_url_after_exhausting_transient_errors(self) -> None:
        with tempfile.TemporaryDirectory(prefix="manifest-gen-") as tmp:
            dest = Path(tmp) / "artifact.tar.gz"
            error = urllib.error.HTTPError(
                "https://example.invalid/flaky.tar.gz",
                502,
                "Bad Gateway",
                hdrs=None,
                fp=None,
            )

            with mock.patch.object(
                self.generator.urllib.request, "urlopen", side_effect=error
            ) as urlopen, mock.patch.object(self.generator.time, "sleep") as sleep:
                with self.assertRaisesRegex(
                    self.generator.DownloadError,
                    "https://example.invalid/flaky.tar.gz.*3 attempts.*HTTP 502 Bad Gateway",
                ):
                    self.generator.download("https://example.invalid/flaky.tar.gz", dest)

            self.assertFalse(dest.exists())
            self.assertEqual(urlopen.call_count, 3)
            self.assertEqual([call.args for call in sleep.call_args_list], [(1,), (2,)])

    def test_generates_nodejs_dist_release_manifest_from_shasums(self) -> None:
        with tempfile.TemporaryDirectory(prefix="manifest-gen-") as tmp:
            tmp_path = Path(tmp)
            config_path = tmp_path / "node.toml"
            config_path.write_text(
                textwrap.dedent(
                    """
                    name = "node"
                    license = "MIT"
                    homepage = "https://nodejs.org/"

                    [source]
                    provider = "nodejs-dist"
                    major = 22
                    include_prereleases = false

                    [[artifacts]]
                    target = "x86_64-unknown-linux-gnu"
                    asset = "node-v{version}-linux-x64.tar.xz"
                    archive = "tar.xz"
                    strip_components = 1

                    [[artifacts.binaries]]
                    name = "node"
                    path = "bin/node"

                    [[artifacts]]
                    target = "aarch64-unknown-linux-gnu"
                    asset = "node-v{version}-linux-arm64.tar.xz"
                    archive = "tar.xz"
                    strip_components = 1

                    [[artifacts.binaries]]
                    name = "node"
                    path = "bin/node"
                    """
                ).strip()
                + "\n",
                encoding="utf-8",
            )

            release = {
                "version": "v22.22.2",
                "files": ["linux-x64", "linux-arm64"],
            }
            shasums_by_name = {
                "node-v22.22.2-linux-x64.tar.xz": "88fd1ce767091fd8d4a99fdb2356e98c819f93f3b1f8663853a2dee9b438068a",
                "node-v22.22.2-linux-arm64.tar.xz": "e9e1930fd321a470e29bb68f30318bf58e3ecb4acb4f1533fb19c58328a091fe",
            }

            rendered = self.generator.generate_release_text(
                config_path=config_path,
                version="22.22.2",
                release=release,
                shasums_by_name=shasums_by_name,
            )

            self.assertIn('name = "node"', rendered)
            self.assertIn('version = "22.22.2"', rendered)
            self.assertIn(
                'url = "https://nodejs.org/dist/latest-v22.x/node-v22.22.2-linux-x64.tar.xz"',
                rendered,
            )
            self.assertIn(
                'sha256 = "88fd1ce767091fd8d4a99fdb2356e98c819f93f3b1f8663853a2dee9b438068a"',
                rendered,
            )
            self.assertIn(
                'url = "https://nodejs.org/dist/latest-v22.x/node-v22.22.2-linux-arm64.tar.xz"',
                rendered,
            )
            self.assertIn(
                'sha256 = "e9e1930fd321a470e29bb68f30318bf58e3ecb4acb4f1533fb19c58328a091fe"',
                rendered,
            )

    def test_generates_nodejs_dist_package_text_without_repo(self) -> None:
        with tempfile.TemporaryDirectory(prefix="manifest-gen-") as tmp:
            tmp_path = Path(tmp)
            config_path = tmp_path / "node.toml"
            config_path.write_text(
                textwrap.dedent(
                    """
                    name = "node"
                    license = "MIT"
                    homepage = "https://nodejs.org/"

                    [source]
                    provider = "nodejs-dist"
                    major = 22
                    include_prereleases = false

                    [[artifacts]]
                    target = "x86_64-unknown-linux-gnu"
                    asset = "node-v{version}-linux-x64.tar.xz"
                    archive = "tar.xz"
                    strip_components = 1

                    [[artifacts.binaries]]
                    name = "node"
                    path = "bin/node"
                    """
                ).strip()
                + "\n",
                encoding="utf-8",
            )

            rendered = self.generator.generate_package_text(config_path=config_path)

            self.assertIn('provider = "nodejs-dist"', rendered)
            self.assertIn('major = 22', rendered)
            self.assertNotIn('repo =', rendered)


if __name__ == "__main__":
    unittest.main()
