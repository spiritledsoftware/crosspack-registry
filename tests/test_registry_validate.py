import json
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts" / "registry-validate.py"


class RegistryValidateTests(unittest.TestCase):
    def test_valid_package_and_release_docs_pass(self) -> None:
        with tempfile.TemporaryDirectory(prefix="registry-validate-") as tmp:
            tmp_path = Path(tmp)
            package = tmp_path / "packages" / "demo.toml"
            release = tmp_path / "releases" / "demo" / "1.2.3.toml"
            package.parent.mkdir(parents=True, exist_ok=True)
            release.parent.mkdir(parents=True, exist_ok=True)

            package.write_text(
                textwrap.dedent(
                    """
                    name = "demo"
                    license = "MIT"
                    homepage = "https://example.com/demo"

                    [[integrations]]
                    kind = "service"
                    name = "demo"
                    source = "etc/systemd/demo.service"

                    [source]
                    provider = "github"
                    repo = "example/demo"

                    [[artifacts]]
                    target = "x86_64-unknown-linux-gnu"
                    asset = "demo-{version}-x86_64-unknown-linux-gnu.tar.gz"
                    archive = "tar.gz"
                    strip_components = 1

                    [[artifacts.binaries]]
                    name = "demo"
                    path = "demo"
                    """
                ).strip()
                + "\n",
                encoding="utf-8",
            )

            release.write_text(
                textwrap.dedent(
                    """
                    name = "demo"
                    version = "1.2.3"

                    [[integrations]]
                    kind = "service"
                    name = "demo"
                    source = "etc/systemd/demo.service"

                    [[artifacts]]
                    target = "x86_64-unknown-linux-gnu"
                    url = "https://example.com/demo-1.2.3.tar.gz"
                    sha256 = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
                    """
                ).strip()
                + "\n",
                encoding="utf-8",
            )

            result = subprocess.run(
                [
                    "python3",
                    str(SCRIPT),
                    "--allow-missing-signatures",
                    str(package),
                    str(release),
                ],
                cwd=REPO_ROOT,
                text=True,
                capture_output=True,
            )

        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertIn("Validated 2 manifest(s)", result.stdout)

    def test_service_integration_platform_sources_pass(self) -> None:
        with tempfile.TemporaryDirectory(prefix="registry-validate-") as tmp:
            tmp_path = Path(tmp)
            package = tmp_path / "packages" / "demo.toml"
            release = tmp_path / "releases" / "demo" / "1.2.3.toml"
            package.parent.mkdir(parents=True, exist_ok=True)
            release.parent.mkdir(parents=True, exist_ok=True)

            package.write_text(
                textwrap.dedent(
                    """
                    name = "demo"
                    license = "MIT"
                    homepage = "https://example.com/demo"

                    [[integrations]]
                    kind = "service"
                    name = "demo"
                    linux_systemd_user = "etc/linux-systemd/user/demo.service"
                    macos_launch_agent = "etc/macos-launchd/user/demo.plist"
                    windows_service = "etc/windows-service/demo.xml"

                    [source]
                    provider = "github"
                    repo = "example/demo"

                    [[artifacts]]
                    target = "x86_64-unknown-linux-gnu"
                    asset = "demo-{version}.tar.gz"
                    archive = "tar.gz"

                    [[artifacts.binaries]]
                    name = "demo"
                    path = "demo"
                    """
                ).strip()
                + "\n",
                encoding="utf-8",
            )

            release.write_text(
                textwrap.dedent(
                    """
                    name = "demo"
                    version = "1.2.3"

                    [[integrations]]
                    kind = "service"
                    name = "demo"
                    linux_systemd_user = "etc/linux-systemd/user/demo.service"
                    macos_launch_agent = "etc/macos-launchd/user/demo.plist"
                    windows_service = "etc/windows-service/demo.xml"

                    [[artifacts]]
                    target = "x86_64-unknown-linux-gnu"
                    url = "https://example.com/demo-1.2.3.tar.gz"
                    sha256 = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
                    """
                ).strip()
                + "\n",
                encoding="utf-8",
            )

            result = subprocess.run(
                ["python3", str(SCRIPT), "--allow-missing-signatures", str(package), str(release)],
                cwd=REPO_ROOT,
                text=True,
                capture_output=True,
            )

        self.assertEqual(result.returncode, 0, msg=result.stderr)

    def test_integration_sources_reject_unsafe_paths(self) -> None:
        invalid_paths = [
            ".",
            "etc//demo.service",
            "./etc/demo.service",
            "etc\\demo.service",
            "C:/etc/demo.service",
            "etc/demo\x07.service",
            "../etc/demo.service",
        ]
        for invalid_path in invalid_paths:
            with self.subTest(invalid_path=invalid_path):
                with tempfile.TemporaryDirectory(prefix="registry-validate-") as tmp:
                    tmp_path = Path(tmp)
                    package = tmp_path / "packages" / "demo.toml"
                    package.parent.mkdir(parents=True, exist_ok=True)
                    source_literal = json.dumps(invalid_path)
                    package.write_text(
                        textwrap.dedent(
                            f"""
                            name = "demo"
                            license = "MIT"
                            homepage = "https://example.com/demo"

                            [[integrations]]
                            kind = "service"
                            name = "demo"
                            linux_systemd_user = {source_literal}

                            [source]
                            provider = "github"
                            repo = "example/demo"

                            [[artifacts]]
                            target = "x86_64-unknown-linux-gnu"
                            asset = "demo-{{version}}.tar.gz"
                            archive = "tar.gz"
                            """
                        ).strip()
                        + "\n",
                        encoding="utf-8",
                    )

                    result = subprocess.run(
                        ["python3", str(SCRIPT), "--allow-missing-signatures", str(package)],
                        cwd=REPO_ROOT,
                        text=True,
                        capture_output=True,
                    )

                self.assertNotEqual(result.returncode, 0)
                self.assertIn("must be a normalized relative path", result.stderr)

    def test_release_path_name_mismatch_fails(self) -> None:
        with tempfile.TemporaryDirectory(prefix="registry-validate-") as tmp:
            tmp_path = Path(tmp)
            release = tmp_path / "releases" / "demo" / "1.2.3.toml"
            release.parent.mkdir(parents=True, exist_ok=True)
            release.write_text(
                textwrap.dedent(
                    """
                    name = "other"
                    version = "1.2.3"

                    [[artifacts]]
                    target = "x86_64-unknown-linux-gnu"
                    url = "https://example.com/demo-1.2.3.tar.gz"
                    sha256 = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
                    """
                ).strip()
                + "\n",
                encoding="utf-8",
            )

            result = subprocess.run(
                ["python3", str(SCRIPT), "--allow-missing-signatures", str(release)],
                cwd=REPO_ROOT,
                text=True,
                capture_output=True,
            )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("does not match `name`", result.stderr)

    def test_package_without_release_directory_fails(self) -> None:
        with tempfile.TemporaryDirectory(prefix="registry-validate-") as tmp:
            tmp_path = Path(tmp)
            package = tmp_path / "packages" / "demo.toml"
            package.parent.mkdir(parents=True, exist_ok=True)
            package.write_text(
                textwrap.dedent(
                    """
                    name = "demo"
                    license = "MIT"
                    homepage = "https://example.com/demo"

                    [source]
                    provider = "github"
                    repo = "example/demo"

                    [[artifacts]]
                    target = "x86_64-unknown-linux-gnu"
                    asset = "demo-{version}.tar.gz"
                    archive = "tar.gz"

                    [[artifacts.binaries]]
                    name = "demo"
                    path = "demo"
                    """
                ).strip()
                + "\n",
                encoding="utf-8",
            )

            result = subprocess.run(
                ["python3", str(SCRIPT), "--allow-missing-signatures", str(package)],
                cwd=REPO_ROOT,
                text=True,
                capture_output=True,
            )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("missing release directory", result.stderr)

    def test_invalid_signature_fails(self) -> None:
        with tempfile.TemporaryDirectory(prefix="registry-validate-") as tmp:
            tmp_path = Path(tmp)
            key_path = tmp_path / "signing-key.pem"
            registry_pub_path = tmp_path / "registry.pub"
            package = tmp_path / "packages" / "demo.toml"
            release_dir = tmp_path / "releases" / "demo"
            package.parent.mkdir(parents=True, exist_ok=True)
            release_dir.mkdir(parents=True, exist_ok=True)
            package.write_text(
                textwrap.dedent(
                    """
                    name = "demo"
                    license = "MIT"
                    homepage = "https://example.com/demo"

                    [source]
                    provider = "github"
                    repo = "example/demo"

                    [[artifacts]]
                    target = "x86_64-unknown-linux-gnu"
                    asset = "demo-{version}.tar.gz"
                    archive = "tar.gz"

                    [[artifacts.binaries]]
                    name = "demo"
                    path = "demo"
                    """
                ).strip()
                + "\n",
                encoding="utf-8",
            )

            subprocess.run(
                ["openssl", "genpkey", "-algorithm", "ed25519", "-out", str(key_path)],
                cwd=tmp_path,
                text=True,
                capture_output=True,
                check=True,
            )
            pub_der = subprocess.run(
                [
                    "openssl",
                    "pkey",
                    "-in",
                    str(key_path),
                    "-pubout",
                    "-outform",
                    "DER",
                ],
                cwd=tmp_path,
                capture_output=True,
                check=True,
            ).stdout
            registry_pub_path.write_text(pub_der[-32:].hex() + "\n", encoding="utf-8")

            other = tmp_path / "other.toml"
            other.write_text(package.read_text(encoding="utf-8") + "# changed\n", encoding="utf-8")
            sig_bin = tmp_path / "demo.sig.bin"
            subprocess.run(
                [
                    "openssl",
                    "pkeyutl",
                    "-sign",
                    "-rawin",
                    "-inkey",
                    str(key_path),
                    "-in",
                    str(other),
                    "-out",
                    str(sig_bin),
                ],
                cwd=tmp_path,
                text=True,
                capture_output=True,
                check=True,
            )
            package.with_suffix(".toml.sig").write_text(
                sig_bin.read_bytes().hex() + "\n", encoding="utf-8"
            )

            result = subprocess.run(
                [
                    "python3",
                    str(SCRIPT),
                    "--trusted-key",
                    str(registry_pub_path),
                    str(package),
                ],
                cwd=REPO_ROOT,
                text=True,
                capture_output=True,
            )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("invalid signature sidecar", result.stderr)


if __name__ == "__main__":
    unittest.main()
