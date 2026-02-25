import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_SOURCE = REPO_ROOT / "scripts" / "sign-changed-manifests.sh"


class SignChangedManifestsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = Path(tempfile.mkdtemp(prefix="sign-manifests-test-"))
        self.repo_root = self.tmpdir / "repo"
        self.repo_root.mkdir(parents=True)

        scripts_dir = self.repo_root / "scripts"
        scripts_dir.mkdir(parents=True, exist_ok=True)
        self.script_path = scripts_dir / "sign-changed-manifests.sh"
        self.script_path.write_text(
            SCRIPT_SOURCE.read_text(encoding="utf-8"), encoding="utf-8"
        )
        self.script_path.chmod(0o755)

        self.manifest_path = self.repo_root / "index" / "demo" / "1.0.0.toml"
        self.manifest_path.parent.mkdir(parents=True, exist_ok=True)
        self.manifest_path.write_text(
            """name = \"demo\"\nversion = \"1.0.0\"\nlicense = \"MIT\"\nhomepage = \"https://example.com\"\n""",
            encoding="utf-8",
        )
        self.signature_path = self.manifest_path.with_suffix(".toml.sig")

        self.key_path = self.repo_root / "signing-key.pem"
        subprocess.run(
            ["openssl", "genpkey", "-algorithm", "ed25519", "-out", str(self.key_path)],
            cwd=self.repo_root,
            text=True,
            capture_output=True,
            check=True,
        )

        self._write_signature(self.manifest_path, self.signature_path)

        self._git("init")
        self._git("config", "user.name", "test-bot")
        self._git("config", "user.email", "test-bot@example.com")
        self._git("add", "-A")
        self._git("commit", "-m", "initial")
        self.before_sha = self._git_output("rev-parse", "HEAD")

        self.signature_path.unlink()
        self._git("add", "-A")
        self._git("commit", "-m", "delete sidecar")
        self.after_sha = self._git_output("rev-parse", "HEAD")

    def tearDown(self) -> None:
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _git(self, *args: str) -> None:
        subprocess.run(
            ["git", *args],
            cwd=self.repo_root,
            text=True,
            capture_output=True,
            check=True,
        )

    def _git_output(self, *args: str) -> str:
        result = subprocess.run(
            ["git", *args],
            cwd=self.repo_root,
            text=True,
            capture_output=True,
            check=True,
        )
        return result.stdout.strip()

    def _write_signature(self, manifest_path: Path, signature_path: Path) -> None:
        sig_bin_path = self.repo_root / "sig.bin"
        subprocess.run(
            [
                "openssl",
                "pkeyutl",
                "-sign",
                "-rawin",
                "-inkey",
                str(self.key_path),
                "-in",
                str(manifest_path),
                "-out",
                str(sig_bin_path),
            ],
            cwd=self.repo_root,
            text=True,
            capture_output=True,
            check=True,
        )
        signature_path.write_text(
            sig_bin_path.read_bytes().hex() + "\n", encoding="utf-8"
        )
        sig_bin_path.unlink(missing_ok=True)

    def test_resigns_manifest_when_sidecar_deleted_in_range(self) -> None:
        env = {
            **os.environ,
            "SIGNING_PRIVATE_KEY_PEM": self.key_path.read_text(encoding="utf-8"),
            "BEFORE_SHA": self.before_sha,
            "AFTER_SHA": self.after_sha,
            "LC_ALL": "C",
        }

        result = subprocess.run(
            [str(self.script_path)],
            cwd=self.repo_root,
            text=True,
            capture_output=True,
            env=env,
        )

        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertTrue(
            self.signature_path.exists(),
            "expected deleted sidecar to be regenerated for changed manifest",
        )
        self.assertIn("signed index/demo/1.0.0.toml", result.stdout)


if __name__ == "__main__":
    unittest.main()
