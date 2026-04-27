import contextlib
import importlib.util
import io
import os
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path
from unittest import mock


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = REPO_ROOT / "scripts" / "upstream-release-bot.py"


def load_module():
    spec = importlib.util.spec_from_file_location("upstream_release_bot", SCRIPT_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load script module from {SCRIPT_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_generator_module():
    generator_path = REPO_ROOT / "scripts" / "registry-generate-manifest.py"
    spec = importlib.util.spec_from_file_location(
        "registry_generate_manifest", generator_path
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load script module from {generator_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class UpstreamReleaseBotTests(unittest.TestCase):
    def setUp(self) -> None:
        self.bot = load_module()

    def _git(self, repo: Path, *args: str, env: dict[str, str] | None = None) -> str:
        completed = subprocess.run(
            ["git", *args],
            cwd=repo,
            check=True,
            text=True,
            capture_output=True,
            env=env,
        )
        return completed.stdout.strip()

    def test_plan_updates_only_considers_latest_release(self) -> None:
        with tempfile.TemporaryDirectory(prefix="release-bot-") as tmp:
            tmp_path = Path(tmp)
            sources_dir = tmp_path / "registry" / "sources"
            sources_dir.mkdir(parents=True)
            config_path = sources_dir / "ripgrep.toml"
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
                    kind = "asset_digest"

                    [source.asset]
                    kind = "release_asset_url"
                    """
                ).strip()
                + "\n",
                encoding="utf-8",
            )

            existing_manifest = tmp_path / "releases" / "ripgrep" / "15.1.0.toml"
            existing_manifest.parent.mkdir(parents=True)
            existing_manifest.write_text('name = "ripgrep"\n', encoding="utf-8")

            releases = [
                {
                    "tag_name": "15.2.0",
                    "draft": False,
                    "prerelease": False,
                    "assets": [],
                },
                {
                    "tag_name": "15.1.0",
                    "draft": False,
                    "prerelease": False,
                    "assets": [],
                },
            ]

            planned = self.bot.plan_updates_for_config(
                config_path=config_path,
                releases_root=tmp_path / "releases",
                releases=releases,
            )

        self.assertEqual(len(planned), 1)
        self.assertEqual(planned[0].version, "15.2.0")

    def test_plan_updates_stops_when_latest_release_exists(self) -> None:
        with tempfile.TemporaryDirectory(prefix="release-bot-") as tmp:
            tmp_path = Path(tmp)
            sources_dir = tmp_path / "registry" / "sources"
            sources_dir.mkdir(parents=True)
            config_path = sources_dir / "ripgrep.toml"
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
                    kind = "asset_digest"

                    [source.asset]
                    kind = "release_asset_url"
                    """
                ).strip()
                + "\n",
                encoding="utf-8",
            )

            existing_manifest = tmp_path / "releases" / "ripgrep" / "15.2.0.toml"
            existing_manifest.parent.mkdir(parents=True)
            existing_manifest.write_text('name = "ripgrep"\n', encoding="utf-8")

            releases = [
                {
                    "tag_name": "15.2.0",
                    "draft": False,
                    "prerelease": False,
                    "assets": [],
                },
                {
                    "tag_name": "15.1.0",
                    "draft": False,
                    "prerelease": False,
                    "assets": [],
                },
            ]

            planned = self.bot.plan_updates_for_config(
                config_path=config_path,
                releases_root=tmp_path / "releases",
                releases=releases,
            )

        self.assertEqual(planned, [])

    def test_plan_updates_python_build_standalone_from_asset_version(self) -> None:
        with tempfile.TemporaryDirectory(prefix="release-bot-") as tmp:
            tmp_path = Path(tmp)
            sources_dir = tmp_path / "registry" / "sources"
            sources_dir.mkdir(parents=True)
            config_path = sources_dir / "python.toml"
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
                    """
                ).strip()
                + "\n",
                encoding="utf-8",
            )

            releases = [
                {
                    "tag_name": "20260414",
                    "draft": False,
                    "prerelease": False,
                    "assets": [
                        {
                            "name": "cpython-3.14.4+20260414-x86_64-unknown-linux-gnu-install_only_stripped.tar.gz"
                        }
                    ],
                }
            ]

            planned = self.bot.plan_updates_for_config(
                config_path=config_path,
                releases_root=tmp_path / "releases",
                releases=releases,
            )

        self.assertEqual(len(planned), 1)
        self.assertEqual(planned[0].version, "3.14.4+20260414")

    def test_skips_incomplete_release_instead_of_failing(self) -> None:
        with tempfile.TemporaryDirectory(prefix="release-bot-") as tmp:
            tmp_path = Path(tmp)
            packages_dir = tmp_path / "packages"
            packages_dir.mkdir(parents=True)
            config_path = packages_dir / "fd.toml"
            config_path.write_text(
                textwrap.dedent(
                    """
                    name = "fd"
                    license = "Apache-2.0 OR MIT"
                    homepage = "https://github.com/sharkdp/fd"

                    [source.release]
                    kind = "github_releases"
                    repo = "sharkdp/fd"
                    tag_prefix = "v"

                    [source.checksum]
                    kind = "download_sha256"

                    [source.asset]
                    kind = "release_asset_url"

                    [[artifacts]]
                    target = "x86_64-apple-darwin"
                    asset = "fd-v{version}-x86_64-apple-darwin.tar.gz"
                    archive = "tar.gz"
                    strip_components = 1

                    [[artifacts.binaries]]
                    name = "fd"
                    path = "fd"
                    """
                ).strip()
                + "\n",
                encoding="utf-8",
            )

            stdout = io.StringIO()
            stderr = io.StringIO()
            generator = load_generator_module()
            previous_cwd = Path.cwd()
            os.chdir(tmp_path)
            try:
                with (
                    mock.patch.object(
                        self.bot,
                        "_load_generator_module",
                        return_value=generator,
                    ),
                    mock.patch.object(
                        self.bot,
                        "fetch_github_releases",
                        return_value=[
                            {
                                "tag_name": "v10.4.1",
                                "draft": False,
                                "prerelease": False,
                                "assets": [
                                    {
                                        "name": "fd-v10.4.1-aarch64-apple-darwin.tar.gz",
                                        "browser_download_url": "https://example.invalid/fd-aarch64.tar.gz",
                                    }
                                ],
                            }
                        ],
                    ),
                    contextlib.redirect_stdout(stdout),
                    contextlib.redirect_stderr(stderr),
                ):
                    result = self.bot.main(["--package", "fd"])
            finally:
                os.chdir(previous_cwd)

            self.assertEqual(result, 0)
            self.assertFalse((tmp_path / "releases" / "fd" / "10.4.1.toml").exists())
            self.assertIn("Skipping fd 10.4.1: incomplete upstream release", stderr.getvalue())
            self.assertIn("skipped 1 incomplete update(s)", stdout.getvalue())
            self.assertIn("wrote 0 release manifest(s)", stdout.getvalue())

    def test_open_or_update_pr_handles_generated_file_that_conflicts_with_remote_branch(self) -> None:
        with tempfile.TemporaryDirectory(prefix="release-bot-git-") as tmp:
            tmp_path = Path(tmp)
            remote = tmp_path / "remote.git"
            repo = tmp_path / "repo"
            subprocess.run(["git", "init", "--bare", remote], check=True)
            subprocess.run(["git", "init", "-b", "main", repo], check=True)

            self._git(repo, "config", "user.name", "Test User")
            self._git(repo, "config", "user.email", "test@example.com")
            self._git(repo, "remote", "add", "origin", str(remote))

            (repo / "README.md").write_text("base\n", encoding="utf-8")
            self._git(repo, "add", "README.md")
            self._git(repo, "commit", "-m", "base")
            self._git(repo, "push", "-u", "origin", "main")

            branch_name = "upstream-release/beekeeper-studio/5.6.0"
            self._git(repo, "switch", "-c", branch_name)
            release_path = repo / "releases" / "beekeeper-studio" / "5.6.0.toml"
            release_path.parent.mkdir(parents=True, exist_ok=True)
            release_path.write_text('version = "old"\n', encoding="utf-8")
            self._git(repo, "add", str(release_path.relative_to(repo)))
            self._git(repo, "commit", "-m", "existing release branch")
            self._git(repo, "push", "-u", "origin", branch_name)

            self._git(repo, "switch", "main")
            release_path.parent.mkdir(parents=True, exist_ok=True)
            release_path.write_text('version = "new"\n', encoding="utf-8")

            fake_bin = tmp_path / "bin"
            fake_bin.mkdir()
            gh = fake_bin / "gh"
            gh.write_text(
                textwrap.dedent(
                    """
                    #!/usr/bin/env python3
                    import sys

                    if sys.argv[1:4] == ["pr", "list", "--head"]:
                        print("[]")
                    elif sys.argv[1:3] == ["pr", "create"]:
                        print("created")
                    else:
                        raise SystemExit(f"unexpected gh args: {sys.argv[1:]}")
                    """
                ).strip()
                + "\n",
                encoding="utf-8",
            )
            gh.chmod(0o755)
            env = os.environ.copy()
            env["PATH"] = f"{fake_bin}:{env['PATH']}"

            previous_path = os.environ.get("PATH", "")
            os.environ["PATH"] = env["PATH"]
            try:
                self.bot._open_or_update_pr(
                    repo_root=repo,
                    staged_paths=[release_path.relative_to(repo)],
                    package="beekeeper-studio",
                    version="5.6.0",
                    base_branch="main",
                    branch_prefix="upstream-release",
                )
            finally:
                os.environ["PATH"] = previous_path

            self.assertEqual(self._git(repo, "branch", "--show-current"), branch_name)
            self.assertEqual(
                release_path.read_text(encoding="utf-8"),
                'version = "new"\n',
            )
            self.assertIn(
                "chore(registry): add beekeeper-studio 5.6.0",
                self._git(repo, "log", "-1", "--pretty=%s"),
            )

    def test_plan_updates_for_nodejs_dist_major_channel(self) -> None:
        with tempfile.TemporaryDirectory(prefix="release-bot-") as tmp:
            tmp_path = Path(tmp)
            sources_dir = tmp_path / "registry" / "sources"
            sources_dir.mkdir(parents=True)
            config_path = sources_dir / "node.toml"
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

            releases = [
                {"version": "v22.22.2", "files": ["linux-x64"]},
                {"version": "v22.21.0", "files": ["linux-x64"]},
            ]

            planned = self.bot.plan_updates_for_config(
                config_path=config_path,
                releases_root=tmp_path / "releases",
                releases=releases,
            )

        self.assertEqual(len(planned), 1)
        self.assertEqual(planned[0].package, "node")
        self.assertEqual(planned[0].version, "22.22.2")

    def test_main_reads_package_templates_by_default(self) -> None:
        with tempfile.TemporaryDirectory(prefix="release-bot-") as tmp:
            tmp_path = Path(tmp)
            packages_dir = tmp_path / "packages"
            packages_dir.mkdir(parents=True)
            config_path = packages_dir / "ripgrep.toml"
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
                    kind = "asset_digest"

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

            stdout = io.StringIO()
            generator = load_generator_module()
            previous_cwd = Path.cwd()
            os.chdir(tmp_path)
            try:
                with (
                    mock.patch.object(
                        self.bot,
                        "_load_generator_module",
                        return_value=generator,
                    ),
                    mock.patch.object(
                        self.bot,
                        "fetch_github_releases",
                        return_value=[
                            {
                                "tag_name": "15.2.0",
                                "draft": False,
                                "prerelease": False,
                                "assets": [
                                    {
                                        "name": "ripgrep-15.2.0-x86_64-unknown-linux-musl.tar.gz",
                                        "browser_download_url": "https://example.invalid/ripgrep.tar.gz",
                                        "digest": "sha256:88fd1ce767091fd8d4a99fdb2356e98c819f93f3b1f8663853a2dee9b438068a",
                                    }
                                ],
                            }
                        ],
                    ),
                    contextlib.redirect_stdout(stdout),
                ):
                    result = self.bot.main(["--dry-run", "--package", "ripgrep"])
            finally:
                os.chdir(previous_cwd)

        self.assertEqual(result, 0)
        self.assertIn("[dry-run] would generate releases/ripgrep/15.2.0.toml", stdout.getvalue())


if __name__ == "__main__":
    unittest.main()
