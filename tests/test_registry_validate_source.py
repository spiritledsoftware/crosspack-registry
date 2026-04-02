import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts" / "registry-validate-source.py"


class RegistryValidateSourceTests(unittest.TestCase):
    def test_valid_zoxide_style_source_config_passes(self) -> None:
        with tempfile.TemporaryDirectory(prefix="source-config-") as tmp:
            config = Path(tmp) / "zoxide.toml"
            config.write_text(
                textwrap.dedent(
                    """
                    name = "zoxide"
                    license = "MIT"
                    homepage = "https://github.com/ajeetdsouza/zoxide"

                    [source]
                    provider = "github"
                    repo = "ajeetdsouza/zoxide"
                    tag_prefix = "v"
                    include_prereleases = false

                    [[artifacts]]
                    target = "x86_64-unknown-linux-gnu"
                    asset = "zoxide-{version}-x86_64-unknown-linux-musl.tar.gz"
                    archive = "tar.gz"
                    strip_components = 0

                    [[artifacts.binaries]]
                    name = "zoxide"
                    path = "zoxide"

                    [[artifacts.completions]]
                    shell = "bash"
                    path = "completions/zoxide.bash"
                    """
                ).strip()
                + "\n",
                encoding="utf-8",
            )

            result = subprocess.run(
                ["python3", str(SCRIPT), str(config)],
                cwd=REPO_ROOT,
                text=True,
                capture_output=True,
            )

        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertIn("Validation passed", result.stdout)

    def test_every_packaged_package_has_source_config(self) -> None:
        packages_root = REPO_ROOT / "packages"
        sources_root = REPO_ROOT / "registry" / "sources"
        packaged_packages = {
            p.stem
            for p in packages_root.glob("*.toml")
            if p.is_file() and p.suffix == ".toml"
        }
        source_packages = {p.stem for p in sources_root.glob("*.toml")}

        missing = sorted(packaged_packages - source_packages)
        self.assertEqual(missing, [], msg=f"Missing source configs: {missing}")

    def test_valid_source_config_passes(self) -> None:
        with tempfile.TemporaryDirectory(prefix="source-config-") as tmp:
            config = Path(tmp) / "ripgrep.toml"
            config.write_text(
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

            result = subprocess.run(
                ["python3", str(SCRIPT), str(config)],
                cwd=REPO_ROOT,
                text=True,
                capture_output=True,
            )

        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertIn("Validation passed", result.stdout)

    def test_missing_required_source_fields_fails(self) -> None:
        with tempfile.TemporaryDirectory(prefix="source-config-") as tmp:
            config = Path(tmp) / "ripgrep.toml"
            config.write_text(
                textwrap.dedent(
                    """
                    name = "ripgrep"
                    license = "MIT OR Unlicense"
                    homepage = "https://github.com/BurntSushi/ripgrep"

                    [source]
                    provider = "github"
                    """
                ).strip()
                + "\n",
                encoding="utf-8",
            )

            result = subprocess.run(
                ["python3", str(SCRIPT), str(config)],
                cwd=REPO_ROOT,
                text=True,
                capture_output=True,
            )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("manifest.source.repo", result.stderr)

    def test_strip_components_rejects_boolean_values(self) -> None:
        with tempfile.TemporaryDirectory(prefix="source-config-") as tmp:
            config = Path(tmp) / "ripgrep.toml"
            config.write_text(
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
                    strip_components = true

                    [[artifacts.binaries]]
                    name = "rg"
                    path = "rg"
                    """
                ).strip()
                + "\n",
                encoding="utf-8",
            )

            result = subprocess.run(
                ["python3", str(SCRIPT), str(config)],
                cwd=REPO_ROOT,
                text=True,
                capture_output=True,
            )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("strip_components", result.stderr)


if __name__ == "__main__":
    unittest.main()
