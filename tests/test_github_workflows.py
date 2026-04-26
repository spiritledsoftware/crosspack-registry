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


if __name__ == "__main__":
    unittest.main()
