import importlib.util
import tempfile
import textwrap
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = REPO_ROOT / "scripts" / "upstream-release-bot.py"


def load_module():
    spec = importlib.util.spec_from_file_location("upstream_release_bot", SCRIPT_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load script module from {SCRIPT_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class UpstreamReleaseBotTests(unittest.TestCase):
    def setUp(self) -> None:
        self.bot = load_module()

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

                    [source]
                    provider = "github"
                    repo = "BurntSushi/ripgrep"
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

                    [source]
                    provider = "github"
                    repo = "BurntSushi/ripgrep"
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


if __name__ == "__main__":
    unittest.main()
