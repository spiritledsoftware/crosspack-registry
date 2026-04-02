import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts" / "registry-validate-seed-definitions.py"


class RegistryValidateSeedDefinitionsTests(unittest.TestCase):
    def test_valid_seed_definitions_pass(self) -> None:
        with tempfile.TemporaryDirectory(prefix="seed-definitions-") as tmp:
            tmp_path = Path(tmp)
            seeds = tmp_path / "registry" / "seed-definitions.toml"
            packages_root = tmp_path / "packages"
            sources_root = tmp_path / "registry" / "sources"
            releases_root = tmp_path / "releases"
            packages_root.mkdir(parents=True, exist_ok=True)
            sources_root.mkdir(parents=True, exist_ok=True)
            (releases_root / "demo").mkdir(parents=True, exist_ok=True)

            seeds.write_text(
                textwrap.dedent(
                    """
                    [[seeds]]
                    package = "demo"
                    category = "cli"
                    rationale = "Representative command-line package"
                    review_notes = "Keep source and release coverage current"
                    """
                ).strip()
                + "\n",
                encoding="utf-8",
            )
            (packages_root / "demo.toml").write_text('name = "demo"\n', encoding="utf-8")
            (sources_root / "demo.toml").write_text('name = "demo"\n', encoding="utf-8")
            (releases_root / "demo" / "1.2.3.toml").write_text(
                'name = "demo"\nversion = "1.2.3"\n', encoding="utf-8"
            )

            result = subprocess.run(
                [
                    "python3",
                    str(SCRIPT),
                    str(seeds),
                    "--packages-root",
                    str(packages_root),
                    "--sources-root",
                    str(sources_root),
                    "--releases-root",
                    str(releases_root),
                ],
                cwd=REPO_ROOT,
                text=True,
                capture_output=True,
            )

        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertIn("Seed definition validation passed", result.stdout)

    def test_missing_release_coverage_fails(self) -> None:
        with tempfile.TemporaryDirectory(prefix="seed-definitions-") as tmp:
            tmp_path = Path(tmp)
            seeds = tmp_path / "registry" / "seed-definitions.toml"
            packages_root = tmp_path / "packages"
            sources_root = tmp_path / "registry" / "sources"
            releases_root = tmp_path / "releases"
            packages_root.mkdir(parents=True, exist_ok=True)
            sources_root.mkdir(parents=True, exist_ok=True)
            releases_root.mkdir(parents=True, exist_ok=True)

            seeds.write_text(
                textwrap.dedent(
                    """
                    [[seeds]]
                    package = "demo"
                    category = "cli"
                    rationale = "Representative command-line package"
                    review_notes = "Keep source and release coverage current"
                    """
                ).strip()
                + "\n",
                encoding="utf-8",
            )
            (packages_root / "demo.toml").write_text('name = "demo"\n', encoding="utf-8")
            (sources_root / "demo.toml").write_text('name = "demo"\n', encoding="utf-8")

            result = subprocess.run(
                [
                    "python3",
                    str(SCRIPT),
                    str(seeds),
                    "--packages-root",
                    str(packages_root),
                    "--sources-root",
                    str(sources_root),
                    "--releases-root",
                    str(releases_root),
                ],
                cwd=REPO_ROOT,
                text=True,
                capture_output=True,
            )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("missing release manifests", result.stderr)

    def test_seed_catalog_must_match_package_templates(self) -> None:
        with tempfile.TemporaryDirectory(prefix="seed-definitions-") as tmp:
            tmp_path = Path(tmp)
            seeds = tmp_path / "registry" / "seed-definitions.toml"
            packages_root = tmp_path / "packages"
            sources_root = tmp_path / "registry" / "sources"
            releases_root = tmp_path / "releases"
            packages_root.mkdir(parents=True, exist_ok=True)
            sources_root.mkdir(parents=True, exist_ok=True)
            (releases_root / "demo").mkdir(parents=True, exist_ok=True)

            seeds.write_text(
                textwrap.dedent(
                    """
                    [[seeds]]
                    package = "demo"
                    category = "cli"
                    rationale = "Representative command-line package"
                    review_notes = "Keep source and release coverage current"
                    """
                ).strip()
                + "\n",
                encoding="utf-8",
            )
            (packages_root / "demo.toml").write_text('name = "demo"\n', encoding="utf-8")
            (packages_root / "extra.toml").write_text('name = "extra"\n', encoding="utf-8")
            (sources_root / "demo.toml").write_text('name = "demo"\n', encoding="utf-8")
            (releases_root / "demo" / "1.2.3.toml").write_text(
                'name = "demo"\nversion = "1.2.3"\n', encoding="utf-8"
            )

            result = subprocess.run(
                [
                    "python3",
                    str(SCRIPT),
                    str(seeds),
                    "--packages-root",
                    str(packages_root),
                    "--sources-root",
                    str(sources_root),
                    "--releases-root",
                    str(releases_root),
                ],
                cwd=REPO_ROOT,
                text=True,
                capture_output=True,
            )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("missing seed definitions", result.stderr)


if __name__ == "__main__":
    unittest.main()
