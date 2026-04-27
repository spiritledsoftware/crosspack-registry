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

                    [source.release]
                    kind = "github_releases"
                    repo = "BurntSushi/ripgrep"

                    [source.checksum]
                    kind = "download_sha256"

                    [source.asset]
                    kind = "release_asset_url"

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

    def test_generates_package_and_release_text_with_integrations(self) -> None:
        doc = {
            "name": "kubectx",
            "license": "Apache-2.0",
            "homepage": "https://github.com/ahmetb/kubectx",
            "source": {
                "release": {"kind": "github_releases", "repo": "ahmetb/kubectx"},
                "checksum": {"kind": "asset_digest"},
                "asset": {"kind": "release_asset_url"},
            },
            "integrations": [
                {
                    "kind": "path_plugin",
                    "host": "kubectl",
                    "name": "ctx",
                    "source": "kubectl-ctx",
                }
            ],
            "artifacts": [
                {
                    "target": "x86_64-unknown-linux-gnu",
                    "asset": "kubectx_v{version}_linux_x86_64.tar.gz",
                    "archive": "tar.gz",
                    "strip_components": 0,
                    "binaries": [{"name": "kubectl-ctx", "path": "kubectx"}],
                }
            ],
        }

        package_text = self.generator.render_package_text(doc)
        release_text = self.generator.render_release_text(
            {
                "name": "kubectx",
                "version": "0.9.5",
                "integrations": doc["integrations"],
                "artifacts": [
                    {
                        "target": "x86_64-unknown-linux-gnu",
                        "url": "https://example.invalid/kubectx.tar.gz",
                        "sha256": "a" * 64,
                    }
                ],
            }
        )

        self.assertIn('[[integrations]]\nkind = "path_plugin"', package_text)
        self.assertIn('host = "kubectl"', package_text)
        self.assertIn('[[integrations]]\nkind = "path_plugin"', release_text)

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

                    [source.release]
                    kind = "json_index"
                    url = "https://nodejs.org/dist/index.json"

                    [source.version]
                    kind = "prefixed_semver_field"
                    field = "version"
                    prefix = "v"
                    require_prefix = "v22."

                    [source.checksum]
                    kind = "shasums256"
                    url_template = "https://nodejs.org/dist/latest-v22.x/SHASUMS256.txt"

                    [source.asset]
                    kind = "templated"
                    base_url = "https://nodejs.org/dist/latest-v22.x"

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

                    [source.release]
                    kind = "json_index"
                    url = "https://nodejs.org/dist/index.json"

                    [source.version]
                    kind = "prefixed_semver_field"
                    field = "version"
                    prefix = "v"
                    require_prefix = "v22."

                    [source.checksum]
                    kind = "shasums256"
                    url_template = "https://nodejs.org/dist/latest-v22.x/SHASUMS256.txt"

                    [source.asset]
                    kind = "templated"
                    base_url = "https://nodejs.org/dist/latest-v22.x"

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

            self.assertIn('[source.release]', rendered)
            self.assertIn('kind = "json_index"', rendered)
            self.assertIn('url = "https://nodejs.org/dist/index.json"', rendered)
            self.assertIn('[source.version]', rendered)
            self.assertIn('require_prefix = "v22."', rendered)
            self.assertIn('[source.asset]', rendered)
            self.assertNotIn('repo =', rendered)

    def test_generates_go_dist_release_manifest_from_index(self) -> None:
        with tempfile.TemporaryDirectory(prefix="manifest-gen-") as tmp:
            config_path = Path(tmp) / "go.toml"
            config_path.write_text(
                textwrap.dedent(
                    """
                    name = "go"
                    license = "BSD-3-Clause"
                    homepage = "https://go.dev/"

                    [source.release]
                    kind = "json_index"
                    url = "https://go.dev/dl/?mode=json"

                    [source.version]
                    kind = "prefixed_semver_field"
                    field = "version"
                    prefix = "go"

                    [source.checksum]
                    kind = "download_index"

                    [source.asset]
                    kind = "json_index_asset"
                    asset_array_field = "files"
                    name_field = "filename"
                    checksum_field = "sha256"
                    url_template = "https://go.dev/dl/{asset}"

                    [[artifacts]]
                    target = "x86_64-unknown-linux-gnu"
                    asset = "go{version}.linux-amd64.tar.gz"
                    archive = "tar.gz"
                    strip_components = 1

                    [[artifacts.binaries]]
                    name = "go"
                    path = "bin/go"
                    """
                ).strip()
                + "\n",
                encoding="utf-8",
            )

            rendered = self.generator.generate_release_text(
                config_path=config_path,
                version="1.26.2",
                release={
                    "version": "go1.26.2",
                    "files": [
                        {
                            "filename": "go1.26.2.linux-amd64.tar.gz",
                            "sha256": "990e6b4bbba816dc3ee129eaeaf4b42f17c2800b88a2166c265ac1a200262282",
                        }
                    ],
                },
            )

            self.assertIn('url = "https://go.dev/dl/go1.26.2.linux-amd64.tar.gz"', rendered)
            self.assertIn('sha256 = "990e6b4bbba816dc3ee129eaeaf4b42f17c2800b88a2166c265ac1a200262282"', rendered)

    def test_generates_python_standalone_release_manifest_from_github_digest(self) -> None:
        with tempfile.TemporaryDirectory(prefix="manifest-gen-") as tmp:
            config_path = Path(tmp) / "python.toml"
            config_path.write_text(
                textwrap.dedent(
                    """
                    name = "python"
                    license = "Python-2.0"
                    homepage = "https://github.com/astral-sh/python-build-standalone"

                    [source.release]
                    kind = "github_releases"
                    repo = "astral-sh/python-build-standalone"

                    [source.version]
                    kind = "asset_name_regex"
                    pattern = '^cpython-(3\\.14\\.\\d+\\+{tag_name})-'

                    [source.checksum]
                    kind = "asset_digest"

                    [source.asset]
                    kind = "release_asset_url"

                    [[artifacts]]
                    target = "x86_64-unknown-linux-gnu"
                    asset = "cpython-{version}-x86_64-unknown-linux-gnu-install_only_stripped.tar.gz"
                    archive = "tar.gz"
                    strip_components = 1

                    [[artifacts.binaries]]
                    name = "python"
                    path = "bin/python3"
                    """
                ).strip()
                + "\n",
                encoding="utf-8",
            )

            rendered = self.generator.generate_release_text(
                config_path=config_path,
                version="3.14.4+20260414",
                release={
                    "assets": [
                        {
                            "name": "cpython-3.14.4+20260414-x86_64-unknown-linux-gnu-install_only_stripped.tar.gz",
                            "browser_download_url": "https://example.invalid/python.tar.gz",
                            "digest": "sha256:fe9a9c32d13870af632cbac3dfc7528ae53597e94472aa4c7d6a42e8166136cd",
                        }
                    ]
                },
            )

            self.assertIn('url = "https://example.invalid/python.tar.gz"', rendered)
            self.assertIn('sha256 = "fe9a9c32d13870af632cbac3dfc7528ae53597e94472aa4c7d6a42e8166136cd"', rendered)


if __name__ == "__main__":
    unittest.main()
