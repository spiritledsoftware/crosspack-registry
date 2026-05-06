import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts" / "registry-validate-source.py"


def run_source_validator(content: str) -> subprocess.CompletedProcess[str]:
    with tempfile.TemporaryDirectory(prefix="source-config-") as tmp:
        config = Path(tmp) / "package.toml"
        config.write_text(textwrap.dedent(content).strip() + "\n", encoding="utf-8")
        return subprocess.run(
            ["python3", str(SCRIPT), str(config)],
            cwd=REPO_ROOT,
            text=True,
            capture_output=True,
        )


def shell_init_manifest(shell_init: str, first_artifact_extra: str = "") -> str:
    return f"""
        name = "starship"
        license = "ISC"
        homepage = "https://github.com/starship/starship"

        [source.release]
        kind = "github_releases"
        repo = "starship/starship"

        [source.checksum]
        kind = "asset_digest"

        [source.asset]
        kind = "release_asset_url"

        {shell_init}

        [[artifacts]]
        target = "x86_64-unknown-linux-gnu"
        asset = "starship-x86_64-unknown-linux-gnu.tar.gz"
        archive = "tar.gz"
        strip_components = 0

        [[artifacts.binaries]]
        name = "starship"
        path = "starship"

        {first_artifact_extra}

        [[artifacts]]
        target = "aarch64-apple-darwin"
        asset = "starship-aarch64-apple-darwin.tar.gz"
        archive = "tar.gz"
        strip_components = 0

        [[artifacts.binaries]]
        name = "starship"
        path = "starship"
    """


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

    def test_package_templates_are_the_only_source_configs(self) -> None:
        self.assertTrue((REPO_ROOT / "packages").is_dir())
        self.assertFalse((REPO_ROOT / "registry" / "sources").exists())

    def test_valid_source_config_passes(self) -> None:
        with tempfile.TemporaryDirectory(prefix="source-config-") as tmp:
            config = Path(tmp) / "ripgrep.toml"
            config.write_text(
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

            result = subprocess.run(
                ["python3", str(SCRIPT), str(config)],
                cwd=REPO_ROOT,
                text=True,
                capture_output=True,
            )

        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertIn("Validation passed", result.stdout)

    def test_valid_source_config_with_integrations_passes(self) -> None:
        with tempfile.TemporaryDirectory(prefix="source-config-") as tmp:
            config = Path(tmp) / "docker-compose.toml"
            config.write_text(
                textwrap.dedent(
                    """
                    name = "docker-compose"
                    license = "Apache-2.0"
                    homepage = "https://github.com/docker/compose"

                    [source.release]
                    kind = "github_releases"
                    repo = "docker/compose"

                    [source.checksum]
                    kind = "asset_digest"

                    [source.asset]
                    kind = "release_asset_url"

                    [[integrations]]
                    kind = "docker_cli_plugin"
                    name = "compose"
                    source = "docker-compose"

                    [[artifacts]]
                    target = "x86_64-unknown-linux-gnu"
                    asset = "docker-compose-linux-x86_64"
                    archive = "bin"
                    strip_components = 0

                    [[artifacts.binaries]]
                    name = "docker-compose"
                    path = "docker-compose"
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

    def test_valid_source_config_with_shell_init_passes(self) -> None:
        result = run_source_validator(
            shell_init_manifest(
                """
                [[shell_init]]
                name = "starship"
                binary = "starship"
                strategy = "eval_stdout"
                bash = ["init", "bash"]
                zsh = ["init", "zsh"]
                fish = ["init", "fish"]
                powershell = ["init", "powershell"]
                """
            )
        )

        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertIn("Validation passed", result.stdout)

    def test_shell_init_rejects_missing_binary(self) -> None:
        result = run_source_validator(
            shell_init_manifest(
                """
                [[shell_init]]
                name = "starship"
                binary = "missing"
                strategy = "eval_stdout"
                bash = ["init", "bash"]
                """
            )
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("binary must match an artifact binary", result.stderr)

    def test_shell_init_rejects_binary_missing_from_one_artifact(self) -> None:
        result = run_source_validator(
            shell_init_manifest(
                """
                [[shell_init]]
                name = "starship-alt"
                binary = "starship-alt"
                strategy = "eval_stdout"
                bash = ["init", "bash"]
                """
                ,
                first_artifact_extra="""
                    [[artifacts.binaries]]
                    name = "starship-alt"
                    path = "starship"
                """,
            )
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("binary must match an artifact binary in every artifact", result.stderr)

    def test_shell_init_rejects_duplicate_names(self) -> None:
        result = run_source_validator(
            shell_init_manifest(
                """
                [[shell_init]]
                name = "starship"
                binary = "starship"
                strategy = "eval_stdout"
                bash = ["init", "bash"]

                [[shell_init]]
                name = "starship"
                binary = "starship"
                strategy = "eval_stdout"
                zsh = ["init", "zsh"]
                """
            )
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("duplicate shell init declaration starship", result.stderr)

    def test_shell_init_rejects_missing_shell_args(self) -> None:
        result = run_source_validator(
            shell_init_manifest(
                """
                [[shell_init]]
                name = "starship"
                binary = "starship"
                strategy = "eval_stdout"
                """
            )
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("must declare at least one shell args field", result.stderr)

    def test_integration_source_path_rejects_parent_segments(self) -> None:
        with tempfile.TemporaryDirectory(prefix="source-config-") as tmp:
            config = Path(tmp) / "kubectx.toml"
            config.write_text(
                textwrap.dedent(
                    """
                    name = "kubectx"
                    license = "Apache-2.0"
                    homepage = "https://github.com/ahmetb/kubectx"

                    [source.release]
                    kind = "github_releases"
                    repo = "ahmetb/kubectx"

                    [source.checksum]
                    kind = "asset_digest"

                    [source.asset]
                    kind = "release_asset_url"

                    [[integrations]]
                    kind = "path_plugin"
                    host = "kubectl"
                    name = "ctx"
                    source = "../kubectl-ctx"

                    [[artifacts]]
                    target = "x86_64-unknown-linux-gnu"
                    asset = "kubectx_v{version}_linux_x86_64.tar.gz"
                    archive = "tar.gz"
                    strip_components = 0

                    [[artifacts.binaries]]
                    name = "kubectl-ctx"
                    path = "kubectx"
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
        self.assertIn("integrations[1].source", result.stderr)

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

    def test_valid_nodejs_dist_source_config_passes(self) -> None:
        with tempfile.TemporaryDirectory(prefix="source-config-") as tmp:
            config = Path(tmp) / "node.toml"
            config.write_text(
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

                    [[artifacts.binaries]]
                    name = "npm"
                    path = "bin/npm"
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

    def test_valid_language_runtime_source_strategies_pass(self) -> None:
        with tempfile.TemporaryDirectory(prefix="source-config-") as tmp:
            tmp_path = Path(tmp)
            configs = {
                "go.toml": """
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
                """,
                "python.toml": """
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
                """,
                "rustup-init.toml": """
                    name = "rustup-init"
                    license = "MIT OR Apache-2.0"
                    homepage = "https://rustup.rs/"

                    [source.release]
                    kind = "text_endpoint"
                    url = "https://static.rust-lang.org/rustup/release-stable.toml"
                    version_regex = "^version\\\\s*=\\\\s*'([^']+)'"

                    [source.version]
                    kind = "semver_field"
                    field = "version"

                    [source.checksum]
                    kind = "url_sha256"
                    url_template = "{url}.sha256"

                    [source.asset]
                    kind = "templated"
                    base_url = "https://static.rust-lang.org/rustup/archive/{version}"

                    [[artifacts]]
                    target = "x86_64-unknown-linux-gnu"
                    asset = "{target}/rustup-init"
                    archive = "bin"

                    [[artifacts.binaries]]
                    name = "rustup-init"
                    path = "rustup-init"
                """,
                "zig.toml": """
                    name = "zig"
                    license = "MIT"
                    homepage = "https://ziglang.org/"

                    [source.release]
                    kind = "json_index"
                    url = "https://ziglang.org/download/index.json"
                    entries = "object_values"
                    version_from_key = true
                    skip_keys = ["master"]

                    [source.version]
                    kind = "semver_field"
                    field = "version"

                    [source.checksum]
                    kind = "download_index"

                    [source.asset]
                    kind = "json_index_asset"
                    url_field = "tarball"
                    checksum_field = "shasum"

                    [[artifacts]]
                    target = "x86_64-unknown-linux-gnu"
                    asset = "x86_64-linux"
                    archive = "tar.xz"
                    strip_components = 1

                    [[artifacts.binaries]]
                    name = "zig"
                    path = "zig"
                """,
            }
            paths = []
            for filename, body in configs.items():
                path = tmp_path / filename
                path.write_text(textwrap.dedent(body).strip() + "\n", encoding="utf-8")
                paths.append(str(path))

            result = subprocess.run(
                ["python3", str(SCRIPT), *paths],
                cwd=REPO_ROOT,
                text=True,
                capture_output=True,
            )

        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertIn("Validation passed", result.stdout)


if __name__ == "__main__":
    unittest.main()
