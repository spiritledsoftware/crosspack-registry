import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


class GitHubWorkflowTests(unittest.TestCase):
    def test_registry_quality_gate_full_scan_runs_after_signing_workflow(self) -> None:
        quality_gate = (
            REPO_ROOT / ".github" / "workflows" / "registry-quality-gate.yml"
        ).read_text(encoding="utf-8")

        self.assertNotIn("\n  push:", quality_gate)
        self.assertIn("\n  workflow_run:", quality_gate)
        self.assertIn("      - Sign Manifests On Merge", quality_gate)
        self.assertIn("      - completed", quality_gate)
        self.assertIn("      - main", quality_gate)

    def test_upstream_release_bot_uses_package_templates_directly(self) -> None:
        workflow = (REPO_ROOT / ".github" / "workflows" / "upstream-release-bot.yml").read_text(
            encoding="utf-8"
        )

        self.assertIn("python3 scripts/registry-validate-source.py packages/*.toml", workflow)
        self.assertNotIn("registry/sources", workflow)
        self.assertNotIn("seed-definitions", workflow)


if __name__ == "__main__":
    unittest.main()
