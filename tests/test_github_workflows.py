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
        self.assertIn("      - 'releases/**'", quality_gate)

    def test_registry_quality_gate_workflow_run_uses_changed_manifests(self) -> None:
        quality_gate = (
            REPO_ROOT / ".github" / "workflows" / "registry-quality-gate.yml"
        ).read_text(encoding="utf-8")

        self.assertIn("Registry preflight (workflow-run changed manifests)", quality_gate)
        self.assertIn("REGISTRY_BASE_SHA: ${{ github.event.workflow_run.head_sha }}^", quality_gate)
        self.assertIn("ref: ${{ github.event.workflow_run.head_sha || github.sha }}", quality_gate)
        self.assertGreaterEqual(
            quality_gate.count("ref: ${{ github.event.workflow_run.head_sha || github.sha }}"),
            2,
        )
        self.assertIn("if: github.event_name == 'workflow_dispatch'", quality_gate)

        workflow_run_step = quality_gate.split(
            "Registry preflight (workflow-run changed manifests)", 1
        )[1].split("Registry preflight (full scan)", 1)[0]
        self.assertNotIn("REGISTRY_PREFLIGHT_ALL", workflow_run_step)

    def test_registry_preflight_does_not_fallback_to_full_scan(self) -> None:
        preflight = (REPO_ROOT / "scripts" / "registry-preflight.sh").read_text(
            encoding="utf-8"
        )

        self.assertNotIn("No manifest changes detected. Running full registry preflight", preflight)
        self.assertIn("No manifest changes detected. Skipping registry preflight.", preflight)

    def test_upstream_release_bot_uses_package_templates_directly(self) -> None:
        workflow = (REPO_ROOT / ".github" / "workflows" / "upstream-release-bot.yml").read_text(
            encoding="utf-8"
        )

        self.assertIn("python3 scripts/registry-validate-source.py packages/*.toml", workflow)
        self.assertNotIn("registry/sources", workflow)
        self.assertNotIn("seed-definitions", workflow)


if __name__ == "__main__":
    unittest.main()
