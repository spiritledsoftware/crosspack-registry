import contextlib
import http.client
import importlib.util
import io
import json
import os
import socket
import subprocess
import tempfile
import textwrap
import urllib.error
from email.message import Message
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

    def test_http_get_json_retries_incomplete_reads(self) -> None:
        class Response:
            def __init__(self, payload: bytes):
                self.payload = payload
                self.headers = Message()

            def __enter__(self):
                return self

            def __exit__(self, _exc_type, _exc, _tb):
                return False

            def read(self):
                return self.payload

        attempts = [http.client.IncompleteRead(b"{", 10), Response(b'{"ok": true}')]

        def fake_urlopen(_request, timeout):
            self.assertEqual(timeout, 30)
            result = attempts.pop(0)
            if isinstance(result, Exception):
                raise result
            return result

        with mock.patch.object(self.bot.urllib.request, "urlopen", side_effect=fake_urlopen):
            payload, _headers = self.bot._http_get_json("https://example.invalid/releases")

        self.assertEqual(payload, {"ok": True})
        self.assertEqual(attempts, [])

    def test_load_bot_state_returns_empty_state_when_missing(self) -> None:
        with tempfile.TemporaryDirectory(prefix="release-bot-state-") as tmp:
            state = self.bot.load_bot_state(Path(tmp) / "state" / "upstream-release-bot.json")

        self.assertEqual(
            state,
            {"schema_version": 2, "sources": {}, "packages": {}, "quarantine": {}},
        )

    def test_load_bot_state_warns_and_rebuilds_when_invalid(self) -> None:
        with tempfile.TemporaryDirectory(prefix="release-bot-state-") as tmp:
            state_path = Path(tmp) / "state" / "upstream-release-bot.json"
            state_path.parent.mkdir(parents=True)
            state_path.write_text("not json\n", encoding="utf-8")
            stderr = io.StringIO()

            with contextlib.redirect_stderr(stderr):
                state = self.bot.load_bot_state(state_path)

        self.assertEqual(
            state,
            {"schema_version": 2, "sources": {}, "packages": {}, "quarantine": {}},
        )
        self.assertIn("Ignoring invalid release bot state", stderr.getvalue())

    def test_write_bot_state_sorts_keys_and_creates_parent(self) -> None:
        with tempfile.TemporaryDirectory(prefix="release-bot-state-") as tmp:
            state_path = Path(tmp) / "state" / "upstream-release-bot.json"

            self.bot.write_bot_state(
                state_path,
                {
                    "schema_version": 2,
                    "sources": {
                        "github_releases:z/z": {"etag": "z"},
                        "github_releases:a/a": {"etag": "a"},
                    },
                    "packages": {},
                    "quarantine": {},
                },
            )

            self.assertEqual(
                json.loads(state_path.read_text(encoding="utf-8")),
                {
                    "packages": {},
                    "quarantine": {},
                    "schema_version": 2,
                    "sources": {
                        "github_releases:a/a": {"etag": "a"},
                        "github_releases:z/z": {"etag": "z"},
                    },
                },
            )

    def test_load_bot_state_migrates_v1_sources_to_v2(self) -> None:
        with tempfile.TemporaryDirectory(prefix="release-bot-state-") as tmp:
            state_path = Path(tmp) / "state" / "upstream-release-bot.json"
            state_path.parent.mkdir(parents=True)
            state_path.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "sources": {
                            "github_releases:BurntSushi/ripgrep": {
                                "etag": "abc",
                                "latest_version": "15.2.0",
                            }
                        },
                    }
                )
                + "\n",
                encoding="utf-8",
            )

            state = self.bot.load_bot_state(state_path)

        self.assertEqual(state["schema_version"], 2)
        self.assertEqual(
            state["sources"]["github_releases:BurntSushi/ripgrep"]["etag"], "abc"
        )
        self.assertEqual(state["packages"], {})
        self.assertEqual(state["quarantine"], {})

    def test_write_bot_state_v2_sorts_all_top_level_maps(self) -> None:
        with tempfile.TemporaryDirectory(prefix="release-bot-state-") as tmp:
            state_path = Path(tmp) / "state" / "upstream-release-bot.json"

            self.bot.write_bot_state(
                state_path,
                {
                    "schema_version": 2,
                    "sources": {"z": {"etag": "z"}, "a": {"etag": "a"}},
                    "packages": {"zpkg": {"latest_version": "2.0.0"}, "apkg": {}},
                    "quarantine": {"zpkg": {"reason_code": "metadata-malformed"}, "apkg": {}},
                },
            )

            written = json.loads(state_path.read_text(encoding="utf-8"))

        self.assertEqual(
            list(written.keys()), ["packages", "quarantine", "schema_version", "sources"]
        )
        self.assertEqual(list(written["sources"].keys()), ["a", "z"])
        self.assertEqual(list(written["packages"].keys()), ["apkg", "zpkg"])
        self.assertEqual(list(written["quarantine"].keys()), ["apkg", "zpkg"])

    def test_rate_limit_reset_header_sets_backoff_until(self) -> None:
        headers = Message()
        headers["x-ratelimit-reset"] = "1770000000"
        error = urllib.error.HTTPError(
            "https://api.github.com/repos/o/r/releases", 403, "rate limit", headers, None
        )

        backoff = self.bot.backoff_from_http_error(error, now_epoch=1769999900)

        self.assertEqual(backoff["reason_code"], "rate-limited")
        self.assertEqual(backoff["backoff_until"], "2026-02-02T02:40:00Z")

    def test_should_skip_package_when_backoff_is_active(self) -> None:
        entry = {"backoff_until": "2099-01-01T00:00:00Z"}

        self.assertTrue(
            self.bot.package_backoff_active(entry, now_iso="2026-05-04T12:00:00Z")
        )

    def test_should_not_skip_package_when_backoff_expired(self) -> None:
        entry = {"backoff_until": "2026-05-04T11:59:59Z"}

        self.assertFalse(
            self.bot.package_backoff_active(entry, now_iso="2026-05-04T12:00:00Z")
        )

    def test_quarantine_update_preserves_first_seen(self) -> None:
        state = self.bot.empty_bot_state()
        quarantine = state["quarantine"]
        quarantine["zig"] = {
            "reason_code": "metadata-malformed",
            "first_seen_at": "2026-05-04T10:00:00Z",
            "last_seen_at": "2026-05-04T10:00:00Z",
            "attempted_version": "0.16.0",
            "last_good_version": "0.15.2",
        }

        changed = self.bot.quarantine_package(
            state,
            package="zig",
            reason_code="metadata-malformed",
            detail="missing artifact url",
            attempted_version="0.16.1",
            last_good_version="0.15.2",
            now_iso="2026-05-04T11:00:00Z",
        )

        self.assertTrue(changed)
        self.assertEqual(quarantine["zig"]["first_seen_at"], "2026-05-04T10:00:00Z")
        self.assertEqual(quarantine["zig"]["last_seen_at"], "2026-05-04T11:00:00Z")
        self.assertEqual(quarantine["zig"]["attempted_version"], "0.16.1")

    def test_clear_quarantine_returns_true_only_when_entry_exists(self) -> None:
        state = self.bot.empty_bot_state()
        state["quarantine"]["zig"] = {"reason_code": "metadata-malformed"}

        self.assertTrue(self.bot.clear_quarantine(state, package="zig"))
        self.assertFalse(self.bot.clear_quarantine(state, package="zig"))
        self.assertNotIn("zig", state["quarantine"])

    def test_fetch_github_releases_sends_conditional_headers(self) -> None:
        captured: dict[str, str | None] = {}

        class FakeResponse:
            headers = Message()

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def read(self) -> bytes:
                return b"[]"

        def fake_urlopen(request, timeout):
            captured["if_none_match"] = request.headers.get("If-none-match")
            captured["if_modified_since"] = request.headers.get("If-modified-since")
            return FakeResponse()

        with mock.patch.object(self.bot.urllib.request, "urlopen", side_effect=fake_urlopen):
            result = self.bot.fetch_github_releases(
                "aquasecurity/trivy",
                token="token",
                state_entry={"etag": "abc", "last_modified": "Mon, 27 Apr 2026 10:00:00 GMT"},
            )

        self.assertEqual(result.releases, [])
        self.assertEqual(captured["if_none_match"], "abc")
        self.assertEqual(captured["if_modified_since"], "Mon, 27 Apr 2026 10:00:00 GMT")

    def test_fetch_github_releases_returns_not_modified_for_304(self) -> None:
        headers = Message()

        with mock.patch.object(
            self.bot.urllib.request,
            "urlopen",
            side_effect=urllib.error.HTTPError(
                "https://api.github.com/repos/aquasecurity/trivy/releases?per_page=20",
                304,
                "Not Modified",
                headers,
                None,
            ),
        ):
            result = self.bot.fetch_github_releases(
                "aquasecurity/trivy",
                state_entry={"etag": "abc"},
            )

        self.assertTrue(result.not_modified)
        self.assertEqual(result.releases, [])

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

    def test_main_counts_checked_package_with_no_planned_update_as_up_to_date(self) -> None:
        with tempfile.TemporaryDirectory(prefix="release-bot-") as tmp:
            tmp_path = Path(tmp)
            packages_dir = tmp_path / "packages"
            packages_dir.mkdir(parents=True)
            (packages_dir / "ripgrep.toml").write_text(
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
            existing_manifest.write_text('name = "ripgrep"\nversion = "15.2.0"\n', encoding="utf-8")

            stdout = io.StringIO()
            generator = load_generator_module()
            previous_cwd = Path.cwd()
            os.chdir(tmp_path)
            try:
                with (
                    mock.patch.object(self.bot, "_load_generator_module", return_value=generator),
                    mock.patch.object(
                        self.bot,
                        "fetch_github_releases",
                        return_value=self.bot.GithubReleaseFetchResult(
                            releases=[
                                {
                                    "tag_name": "15.2.0",
                                    "draft": False,
                                    "prerelease": False,
                                    "assets": [],
                                }
                            ]
                        ),
                    ),
                    contextlib.redirect_stdout(stdout),
                ):
                    result = self.bot.main(["--package", "ripgrep"])
            finally:
                os.chdir(previous_cwd)

        self.assertEqual(result, 0)
        self.assertIn(
            "registry_update_summary updated=0 up_to_date=1 quarantined=0 transient_failed=0 skipped=0",
            stdout.getvalue(),
        )

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
                        return_value=self.bot.GithubReleaseFetchResult(
                            releases=[
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
                            ]
                        ),
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
            self.assertIn(
                "registry_update_summary updated=0 up_to_date=0 quarantined=1 transient_failed=0 skipped=0",
                stdout.getvalue(),
            )

    def test_skips_release_fetch_http_error_instead_of_failing(self) -> None:
        with tempfile.TemporaryDirectory(prefix="release-bot-") as tmp:
            tmp_path = Path(tmp)
            packages_dir = tmp_path / "packages"
            packages_dir.mkdir(parents=True)
            (packages_dir / "trivy.toml").write_text(
                textwrap.dedent(
                    """
                    name = "trivy"
                    license = "Apache-2.0"
                    homepage = "https://github.com/aquasecurity/trivy"

                    [source.release]
                    kind = "github_releases"
                    repo = "aquasecurity/trivy"

                    [source.checksum]
                    kind = "asset_digest"

                    [source.asset]
                    kind = "release_asset_url"
                    """
                ).strip()
                + "\n",
                encoding="utf-8",
            )

            stdout = io.StringIO()
            stderr = io.StringIO()
            generator = load_generator_module()
            headers = Message()
            headers["x-ratelimit-remaining"] = "0"
            headers["x-ratelimit-reset"] = "1770000000"
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
                        side_effect=urllib.error.HTTPError(
                            "https://api.github.com/repos/aquasecurity/trivy/releases?per_page=20",
                            403,
                            "Forbidden",
                            headers,
                            None,
                        ),
                    ),
                    contextlib.redirect_stdout(stdout),
                    contextlib.redirect_stderr(stderr),
                ):
                    result = self.bot.main(["--package", "trivy"])
            finally:
                os.chdir(previous_cwd)

            self.assertEqual(result, 0)
            self.assertFalse((tmp_path / "releases" / "trivy").exists())
            self.assertIn(
                "Skipping trivy: failed to fetch upstream releases (HTTP 403 Forbidden)",
                stderr.getvalue(),
            )
            self.assertIn("skipped 1 release fetch(es)", stdout.getvalue())
            self.assertIn("No new releases detected", stdout.getvalue())
            self.assertIn(
                "registry_update package=trivy status=skipped reason=rate-limited reset_at=2026-02-02T02:40:00Z",
                stderr.getvalue(),
            )
            self.assertIn(
                "registry_update_summary updated=0 up_to_date=0 quarantined=0 transient_failed=1 skipped=1",
                stdout.getvalue(),
            )

    def test_transient_fetch_failure_is_package_scoped_and_continues(self) -> None:
        with tempfile.TemporaryDirectory(prefix="release-bot-") as tmp:
            tmp_path = Path(tmp)
            packages_dir = tmp_path / "packages"
            packages_dir.mkdir(parents=True)
            (packages_dir / "trivy.toml").write_text(
                textwrap.dedent(
                    """
                    name = "trivy"
                    license = "Apache-2.0"
                    homepage = "https://github.com/aquasecurity/trivy"

                    [source.release]
                    kind = "github_releases"
                    repo = "aquasecurity/trivy"

                    [source.checksum]
                    kind = "asset_digest"

                    [source.asset]
                    kind = "release_asset_url"
                    """
                ).strip()
                + "\n",
                encoding="utf-8",
            )
            (packages_dir / "ripgrep.toml").write_text(
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
            stderr = io.StringIO()
            generator = load_generator_module()
            previous_cwd = Path.cwd()
            os.chdir(tmp_path)
            try:
                with (
                    mock.patch.object(self.bot, "_load_generator_module", return_value=generator),
                    mock.patch.object(self.bot, "utc_now_iso", return_value="2026-05-04T12:00:00Z"),
                    mock.patch.object(self.bot, "validate_package_generated_paths"),
                    mock.patch.object(
                        self.bot,
                        "fetch_github_releases",
                        side_effect=[
                            self.bot.GithubReleaseFetchResult(
                                releases=[
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
                                ]
                            ),
                            urllib.error.HTTPError(
                                "https://api.github.com/repos/aquasecurity/trivy/releases?per_page=20",
                                500,
                                "Server Error",
                                Message(),
                                None,
                            ),
                        ],
                    ),
                    contextlib.redirect_stdout(stdout),
                    contextlib.redirect_stderr(stderr),
                ):
                    result = self.bot.main([])
            finally:
                os.chdir(previous_cwd)

            state = json.loads((tmp_path / "state" / "upstream-release-bot.json").read_text(encoding="utf-8"))
            ripgrep_release_exists = (tmp_path / "releases" / "ripgrep" / "15.2.0.toml").exists()

        self.assertEqual(result, 0)
        self.assertTrue(ripgrep_release_exists)
        self.assertIn(
            "registry_update package=trivy status=skipped reason=upstream-error reset_at=2026-05-04T13:00:00Z",
            stderr.getvalue(),
        )
        self.assertIn(
            "registry_update_summary updated=1 up_to_date=0 quarantined=0 transient_failed=1 skipped=1",
            stdout.getvalue(),
        )
        self.assertEqual(state["packages"]["trivy"]["reason_code"], "upstream-error")
        self.assertEqual(state["packages"]["trivy"]["backoff_until"], "2026-05-04T13:00:00Z")

    def test_url_timeout_fetch_failure_is_package_scoped(self) -> None:
        with tempfile.TemporaryDirectory(prefix="release-bot-") as tmp:
            tmp_path = Path(tmp)
            packages_dir = tmp_path / "packages"
            packages_dir.mkdir(parents=True)
            (packages_dir / "trivy.toml").write_text(
                textwrap.dedent(
                    """
                    name = "trivy"
                    license = "Apache-2.0"
                    homepage = "https://github.com/aquasecurity/trivy"

                    [source.release]
                    kind = "github_releases"
                    repo = "aquasecurity/trivy"

                    [source.checksum]
                    kind = "asset_digest"

                    [source.asset]
                    kind = "release_asset_url"
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
                    mock.patch.object(self.bot, "_load_generator_module", return_value=generator),
                    mock.patch.object(self.bot, "utc_now_iso", return_value="2026-05-04T12:00:00Z"),
                    mock.patch.object(
                        self.bot,
                        "fetch_github_releases",
                        side_effect=urllib.error.URLError("timed out"),
                    ),
                    contextlib.redirect_stdout(stdout),
                    contextlib.redirect_stderr(stderr),
                ):
                    result = self.bot.main(["--package", "trivy"])
            finally:
                os.chdir(previous_cwd)

        self.assertEqual(result, 0)
        self.assertIn(
            "registry_update package=trivy status=skipped reason=upstream-error reset_at=2026-05-04T13:00:00Z",
            stderr.getvalue(),
        )
        self.assertIn(
            "registry_update_summary updated=0 up_to_date=0 quarantined=0 transient_failed=1 skipped=1",
            stdout.getvalue(),
        )

    def test_url_dns_fetch_failure_is_not_package_scoped(self) -> None:
        with tempfile.TemporaryDirectory(prefix="release-bot-") as tmp:
            tmp_path = Path(tmp)
            packages_dir = tmp_path / "packages"
            packages_dir.mkdir(parents=True)
            (packages_dir / "trivy.toml").write_text(
                textwrap.dedent(
                    """
                    name = "trivy"
                    license = "Apache-2.0"
                    homepage = "https://github.com/aquasecurity/trivy"

                    [source.release]
                    kind = "github_releases"
                    repo = "aquasecurity/trivy"

                    [source.checksum]
                    kind = "asset_digest"

                    [source.asset]
                    kind = "release_asset_url"
                    """
                ).strip()
                + "\n",
                encoding="utf-8",
            )

            generator = load_generator_module()
            previous_cwd = Path.cwd()
            os.chdir(tmp_path)
            try:
                with (
                    mock.patch.object(self.bot, "_load_generator_module", return_value=generator),
                    mock.patch.object(
                        self.bot,
                        "fetch_github_releases",
                        side_effect=urllib.error.URLError(
                            socket.gaierror(socket.EAI_NONAME, "Name or service not known")
                        ),
                    ),
                ):
                    with self.assertRaises(urllib.error.URLError):
                        self.bot.main(["--package", "trivy"])
            finally:
                os.chdir(previous_cwd)

    def test_release_fetch_non_rate_http_error_still_fails(self) -> None:
        with tempfile.TemporaryDirectory(prefix="release-bot-") as tmp:
            tmp_path = Path(tmp)
            packages_dir = tmp_path / "packages"
            packages_dir.mkdir(parents=True)
            (packages_dir / "badrepo.toml").write_text(
                textwrap.dedent(
                    """
                    name = "badrepo"
                    license = "MIT"
                    homepage = "https://github.com/example/missing"

                    [source.release]
                    kind = "github_releases"
                    repo = "example/missing"

                    [source.checksum]
                    kind = "asset_digest"

                    [source.asset]
                    kind = "release_asset_url"
                    """
                ).strip()
                + "\n",
                encoding="utf-8",
            )

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
                        side_effect=urllib.error.HTTPError(
                            "https://api.github.com/repos/example/missing/releases?per_page=20",
                            404,
                            "Not Found",
                            Message(),
                            None,
                        ),
                    ),
                ):
                    with self.assertRaises(urllib.error.HTTPError):
                        self.bot.main(["--package", "badrepo"])
            finally:
                os.chdir(previous_cwd)

    def test_github_403_without_rate_limit_signal_still_fails(self) -> None:
        with tempfile.TemporaryDirectory(prefix="release-bot-") as tmp:
            tmp_path = Path(tmp)
            packages_dir = tmp_path / "packages"
            packages_dir.mkdir(parents=True)
            (packages_dir / "private.toml").write_text(
                textwrap.dedent(
                    """
                    name = "private"
                    license = "MIT"
                    homepage = "https://github.com/example/private"

                    [source.release]
                    kind = "github_releases"
                    repo = "example/private"

                    [source.checksum]
                    kind = "asset_digest"

                    [source.asset]
                    kind = "release_asset_url"
                    """
                ).strip()
                + "\n",
                encoding="utf-8",
            )

            generator = load_generator_module()
            previous_cwd = Path.cwd()
            os.chdir(tmp_path)
            try:
                with (
                    mock.patch.object(self.bot, "_load_generator_module", return_value=generator),
                    mock.patch.object(self.bot, "utc_now_iso", return_value="2026-05-04T12:00:00Z"),
                    mock.patch.object(
                        self.bot,
                        "fetch_github_releases",
                        side_effect=urllib.error.HTTPError(
                            "https://api.github.com/repos/example/private/releases?per_page=20",
                            403,
                            "Forbidden",
                            Message(),
                            None,
                        ),
                    ),
                ):
                    with self.assertRaises(urllib.error.HTTPError):
                        self.bot.main(["--package", "private"])
            finally:
                os.chdir(previous_cwd)

    def test_github_403_with_reset_but_no_rate_limit_signal_still_fails(self) -> None:
        with tempfile.TemporaryDirectory(prefix="release-bot-") as tmp:
            tmp_path = Path(tmp)
            packages_dir = tmp_path / "packages"
            packages_dir.mkdir(parents=True)
            (packages_dir / "private.toml").write_text(
                textwrap.dedent(
                    """
                    name = "private"
                    license = "MIT"
                    homepage = "https://github.com/example/private"

                    [source.release]
                    kind = "github_releases"
                    repo = "example/private"

                    [source.checksum]
                    kind = "asset_digest"

                    [source.asset]
                    kind = "release_asset_url"
                    """
                ).strip()
                + "\n",
                encoding="utf-8",
            )

            headers = Message()
            headers["x-ratelimit-reset"] = "1770000000"
            headers["x-ratelimit-remaining"] = "42"
            generator = load_generator_module()
            previous_cwd = Path.cwd()
            os.chdir(tmp_path)
            try:
                with (
                    mock.patch.object(self.bot, "_load_generator_module", return_value=generator),
                    mock.patch.object(
                        self.bot,
                        "fetch_github_releases",
                        side_effect=urllib.error.HTTPError(
                            "https://api.github.com/repos/example/private/releases?per_page=20",
                            403,
                            "Forbidden",
                            headers,
                            None,
                        ),
                    ),
                ):
                    with self.assertRaises(urllib.error.HTTPError):
                        self.bot.main(["--package", "private"])
            finally:
                os.chdir(previous_cwd)

    def test_main_skips_planning_when_github_release_not_modified(self) -> None:
        with tempfile.TemporaryDirectory(prefix="release-bot-") as tmp:
            tmp_path = Path(tmp)
            packages_dir = tmp_path / "packages"
            packages_dir.mkdir(parents=True)
            (packages_dir / "trivy.toml").write_text(
                textwrap.dedent(
                    """
                    name = "trivy"
                    license = "Apache-2.0"
                    homepage = "https://github.com/aquasecurity/trivy"

                    [source.release]
                    kind = "github_releases"
                    repo = "aquasecurity/trivy"

                    [source.checksum]
                    kind = "asset_digest"

                    [source.asset]
                    kind = "release_asset_url"
                    """
                ).strip()
                + "\n",
                encoding="utf-8",
            )
            state_path = tmp_path / "state" / "upstream-release-bot.json"
            state_path.parent.mkdir(parents=True)
            state_path.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "sources": {"github_releases:aquasecurity/trivy": {"etag": "abc"}},
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            stdout = io.StringIO()
            generator = load_generator_module()
            previous_cwd = Path.cwd()
            os.chdir(tmp_path)
            try:
                with (
                    mock.patch.object(self.bot, "_load_generator_module", return_value=generator),
                    mock.patch.object(self.bot, "utc_now_iso", return_value="2026-05-04T12:00:00Z"),
                    mock.patch.object(
                        self.bot,
                        "fetch_github_releases",
                        return_value=self.bot.GithubReleaseFetchResult(
                            releases=[], not_modified=True
                        ),
                    ) as fetch,
                    contextlib.redirect_stdout(stdout),
                ):
                    result = self.bot.main(["--package", "trivy"])
            finally:
                os.chdir(previous_cwd)
            persisted_state = json.loads(state_path.read_text(encoding="utf-8"))

        self.assertEqual(result, 0)
        fetch.assert_called_once()
        self.assertIn("No new releases detected", stdout.getvalue())
        self.assertEqual(
            persisted_state,
            {
                "packages": {
                    "trivy": {
                        "last_checked_at": "2026-05-04T12:00:00Z",
                        "source_identity": "github_releases:aquasecurity/trivy",
                        "source_kind": "github_releases",
                    }
                },
                "quarantine": {},
                "schema_version": 2,
                "sources": {
                    "github_releases:aquasecurity/trivy": {
                        "etag": "abc",
                        "last_checked_at": "2026-05-04T12:00:00Z",
                        "source_identity": "github_releases:aquasecurity/trivy",
                        "source_kind": "github_releases",
                    }
                },
            },
        )

    def test_create_prs_reconciles_stale_rolling_pr_for_audit_only_state_change(self) -> None:
        with tempfile.TemporaryDirectory(prefix="release-bot-") as tmp:
            tmp_path = Path(tmp)
            packages_dir = tmp_path / "packages"
            packages_dir.mkdir(parents=True)
            (packages_dir / "trivy.toml").write_text(
                textwrap.dedent(
                    """
                    name = "trivy"
                    license = "Apache-2.0"
                    homepage = "https://github.com/aquasecurity/trivy"

                    [source.release]
                    kind = "github_releases"
                    repo = "aquasecurity/trivy"

                    [source.checksum]
                    kind = "asset_digest"

                    [source.asset]
                    kind = "release_asset_url"
                    """
                ).strip()
                + "\n",
                encoding="utf-8",
            )
            state_path = tmp_path / "state" / "upstream-release-bot.json"
            state_path.parent.mkdir(parents=True)
            original_state = {
                "schema_version": 2,
                "sources": {"github_releases:aquasecurity/trivy": {"etag": "abc"}},
                "packages": {},
                "quarantine": {},
            }
            state_path.write_text(json.dumps(original_state) + "\n", encoding="utf-8")
            stdout = io.StringIO()
            generator = load_generator_module()
            previous_cwd = Path.cwd()
            os.chdir(tmp_path)
            try:
                with (
                    mock.patch.object(self.bot, "_load_generator_module", return_value=generator),
                    mock.patch.object(self.bot, "_run") as run,
                    mock.patch.object(self.bot, "utc_now_iso", return_value="2026-05-04T12:00:00Z"),
                    mock.patch.object(
                        self.bot,
                        "fetch_github_releases",
                        return_value=self.bot.GithubReleaseFetchResult(
                            releases=[], not_modified=True
                        ),
                    ),
                    mock.patch.object(self.bot, "_reconcile_empty_rolling_pr") as reconcile,
                    mock.patch.object(self.bot, "_open_or_update_rolling_pr") as open_or_update,
                    contextlib.redirect_stdout(stdout),
                ):
                    result = self.bot.main(["--package", "trivy", "--create-prs"])
            finally:
                os.chdir(previous_cwd)

            persisted_state = json.loads(state_path.read_text(encoding="utf-8"))

        self.assertEqual(result, 0)
        run.assert_any_call(["git", "fetch", "origin", "main"], cwd=tmp_path)
        reconcile.assert_called_once_with(
            repo_root=tmp_path,
            base_branch="main",
            branch_name="upstream-release/rolling",
        )
        open_or_update.assert_not_called()
        self.assertEqual(persisted_state, original_state)
        self.assertIn("No new releases detected", stdout.getvalue())

    def test_main_refreshes_state_after_successful_github_fetch_with_update(self) -> None:
        with tempfile.TemporaryDirectory(prefix="release-bot-") as tmp:
            tmp_path = Path(tmp)
            packages_dir = tmp_path / "packages"
            packages_dir.mkdir(parents=True)
            (packages_dir / "ripgrep.toml").write_text(
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
            state_path = tmp_path / "state" / "upstream-release-bot.json"
            state_path.parent.mkdir(parents=True)
            state_path.write_text(
                json.dumps(
                    {
                        "schema_version": 2,
                        "sources": {},
                        "packages": {
                            "ripgrep": {
                                "reason_code": "rate-limited",
                                "backoff_until": "2026-04-28T11:00:00Z",
                                "detail": "HTTP 403 rate limit",
                                "last_failed_at": "2026-04-28T10:00:00Z",
                            }
                        },
                        "quarantine": {},
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            generator = load_generator_module()
            previous_cwd = Path.cwd()
            os.chdir(tmp_path)
            try:
                with (
                    mock.patch.object(self.bot, "_load_generator_module", return_value=generator),
                    mock.patch.object(self.bot, "utc_now_iso", return_value="2026-04-28T12:00:00Z"),
                    mock.patch.object(self.bot, "validate_package_generated_paths"),
                    mock.patch.object(
                        self.bot,
                        "fetch_github_releases",
                        return_value=self.bot.GithubReleaseFetchResult(
                            releases=[
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
                            etag="new-etag",
                            last_modified="Tue, 28 Apr 2026 12:00:00 GMT",
                        ),
                    ),
                ):
                    result = self.bot.main(["--package", "ripgrep"])
            finally:
                os.chdir(previous_cwd)

            state = json.loads(
                (tmp_path / "state" / "upstream-release-bot.json").read_text(
                    encoding="utf-8"
                )
            )

        self.assertEqual(result, 0)
        self.assertEqual(
            state["sources"]["github_releases:BurntSushi/ripgrep"],
            {
                "etag": "new-etag",
                "last_modified": "Tue, 28 Apr 2026 12:00:00 GMT",
                "last_checked_at": "2026-04-28T12:00:00Z",
                "latest_version": "15.2.0",
                "latest_seen_version": "15.2.0",
                "source_identity": "github_releases:BurntSushi/ripgrep",
                "source_kind": "github_releases",
            },
        )
        self.assertEqual(
            state["packages"]["ripgrep"],
            {
                "last_checked_at": "2026-04-28T12:00:00Z",
                "last_generated_at": "2026-04-28T12:00:00Z",
                "last_successful_version": "15.2.0",
                "latest_seen_version": "15.2.0",
                "source_identity": "github_releases:BurntSushi/ripgrep",
                "source_kind": "github_releases",
            },
        )

    def test_dry_run_does_not_write_release_bot_state(self) -> None:
        with tempfile.TemporaryDirectory(prefix="release-bot-") as tmp:
            tmp_path = Path(tmp)
            packages_dir = tmp_path / "packages"
            packages_dir.mkdir(parents=True)
            (packages_dir / "trivy.toml").write_text(
                textwrap.dedent(
                    """
                    name = "trivy"
                    license = "Apache-2.0"
                    homepage = "https://github.com/aquasecurity/trivy"

                    [source.release]
                    kind = "github_releases"
                    repo = "aquasecurity/trivy"

                    [source.checksum]
                    kind = "asset_digest"

                    [source.asset]
                    kind = "release_asset_url"
                    """
                ).strip()
                + "\n",
                encoding="utf-8",
            )
            generator = load_generator_module()
            previous_cwd = Path.cwd()
            os.chdir(tmp_path)
            try:
                with (
                    mock.patch.object(self.bot, "_load_generator_module", return_value=generator),
                    mock.patch.object(
                        self.bot,
                        "fetch_github_releases",
                        return_value=self.bot.GithubReleaseFetchResult(releases=[]),
                    ),
                ):
                    result = self.bot.main(["--dry-run", "--package", "trivy"])
            finally:
                os.chdir(previous_cwd)

        self.assertEqual(result, 0)
        self.assertFalse((tmp_path / "state" / "upstream-release-bot.json").exists())

    def test_validate_generated_paths_runs_focused_registry_checks(self) -> None:
        with tempfile.TemporaryDirectory(prefix="release-bot-validate-") as tmp:
            repo = Path(tmp)
            calls: list[list[str]] = []

            def fake_run(cmd, *, cwd):
                calls.append(cmd)
                return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

            with mock.patch.object(self.bot, "_run", side_effect=fake_run):
                self.bot.validate_generated_paths(
                    repo_root=repo,
                    staged_paths=[
                        Path("packages/ripgrep.toml"),
                        Path("releases/ripgrep/15.2.0.toml"),
                        Path("state/upstream-release-bot.json"),
                    ],
                )

        self.assertIn(
            ["python3", "scripts/registry-validate-source.py", "packages/ripgrep.toml"],
            calls,
        )
        self.assertIn(
            [
                "python3",
                "scripts/registry-validate.py",
                "--allow-missing-signatures",
                "packages/ripgrep.toml",
                "releases/ripgrep/15.2.0.toml",
            ],
            calls,
        )

    def test_validate_generated_paths_rejects_unexpected_paths(self) -> None:
        with tempfile.TemporaryDirectory(prefix="release-bot-validate-") as tmp:
            with self.assertRaisesRegex(RuntimeError, "unexpected generated path"):
                self.bot.validate_generated_paths(
                    repo_root=Path(tmp),
                    staged_paths=[Path("scripts/upstream-release-bot.py")],
                )

    def test_render_pr_body_includes_deterministic_audit_details(self) -> None:
        body = self.bot.render_pr_body(
            updated_packages=["ripgrep@15.2.0"],
            quarantine_added=["zig"],
            quarantine_updated=["fd"],
            quarantine_cleared=["node"],
            backoff_packages=["trivy:rate-limited:2026-02-02T02:40:00Z"],
            state_changed=True,
            created_releases=1,
            written_packages=1,
            quarantined_count=2,
            transient_failures=1,
            skipped_fetches=1,
        )

        self.assertIn("- state changed: yes", body)
        self.assertIn("- quarantine added: zig", body)
        self.assertIn("- quarantine updated: fd", body)
        self.assertIn("- quarantine cleared: node", body)
        self.assertIn("- backoff packages: trivy:rate-limited:2026-02-02T02:40:00Z", body)

    def test_workflow_serializes_fixed_rolling_branch_runs(self) -> None:
        workflow = (REPO_ROOT / ".github" / "workflows" / "upstream-release-bot.yml").read_text(
            encoding="utf-8"
        )

        self.assertIn("group: upstream-release-bot-upstream-release-rolling", workflow)
        self.assertNotIn("github.ref", workflow)

    def test_open_or_update_pr_enables_automerge_for_new_pr(self) -> None:
        with tempfile.TemporaryDirectory(prefix="release-bot-pr-") as tmp:
            repo = Path(tmp)
            calls: list[list[str]] = []

            def fake_run(cmd, *, cwd):
                calls.append(cmd)
                if cmd[:3] == ["git", "ls-remote", "--heads"]:
                    return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
                if cmd == ["git", "diff", "--cached", "--name-only"]:
                    return subprocess.CompletedProcess(
                        cmd,
                        0,
                        stdout="packages/ripgrep.toml\nreleases/ripgrep/15.2.0.toml\n",
                        stderr="",
                    )
                if cmd[:5] == ["gh", "pr", "list", "--head", "upstream-release/ripgrep/15.2.0"]:
                    return subprocess.CompletedProcess(cmd, 0, stdout="[]", stderr="")
                return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

            with (
                mock.patch.object(self.bot, "_run", side_effect=fake_run),
                mock.patch.object(self.bot, "validate_generated_paths"),
            ):
                self.bot._open_or_update_pr(
                    repo_root=repo,
                    staged_paths=[
                        Path("packages/ripgrep.toml"),
                        Path("releases/ripgrep/15.2.0.toml"),
                    ],
                    package="ripgrep",
                    version="15.2.0",
                    base_branch="main",
                    branch_prefix="upstream-release",
                )

        self.assertIn(
            ["gh", "pr", "merge", "upstream-release/ripgrep/15.2.0", "--auto", "--squash"],
            calls,
        )

    def test_open_or_update_rolling_pr_regenerates_branch_from_base(self) -> None:
        calls: list[list[str]] = []

        def fake_run(cmd, *, cwd):
            calls.append(cmd)
            stdout = ""
            if cmd[:4] == ["gh", "pr", "list", "--head"]:
                stdout = "[]"
            if cmd == ["git", "diff", "--cached", "--name-only"]:
                stdout = "packages/ripgrep.toml\n"
            if cmd == ["git", "rev-parse", "refs/remotes/origin/upstream-release/rolling"]:
                stdout = "abc123\n"
            return subprocess.CompletedProcess(cmd, 0, stdout=stdout, stderr="")

        with tempfile.TemporaryDirectory(prefix="release-bot-pr-") as tmp:
            repo = Path(tmp)
            (repo / "packages").mkdir()
            (repo / "packages" / "ripgrep.toml").write_text(
                'name = "ripgrep"\n', encoding="utf-8"
            )
            with mock.patch.object(self.bot, "_run", side_effect=fake_run), mock.patch.object(
                self.bot, "validate_generated_paths"
            ), mock.patch.object(self.bot, "_remote_branch_exists", return_value=True):
                self.bot._open_or_update_rolling_pr(
                    repo_root=repo,
                    staged_paths=[Path("packages/ripgrep.toml")],
                    base_branch="main",
                    branch_name="upstream-release/rolling",
                    title="chore(registry): update upstream releases",
                    body="## Summary\n- test\n",
                )

        self.assertIn(["git", "fetch", "origin", "main"], calls)
        self.assertIn(
            ["git", "switch", "-C", "upstream-release/rolling", "origin/main"], calls
        )
        self.assertIn(
            ["git", "fetch", "origin", "main"],
            calls,
        )
        self.assertLess(
            calls.index(["git", "fetch", "origin", "main"]),
            calls.index(
                [
                    "git",
                    "push",
                    "--force-with-lease=refs/heads/upstream-release/rolling:abc123",
                    "-u",
                    "origin",
                    "upstream-release/rolling",
                ]
            ),
        )

    def test_open_or_update_rolling_pr_fetches_branch_before_force_with_lease(self) -> None:
        calls: list[list[str]] = []

        def fake_run(cmd, *, cwd):
            calls.append(cmd)
            stdout = ""
            if cmd[:4] == ["gh", "pr", "list", "--head"]:
                stdout = "[]"
            if cmd == ["git", "diff", "--cached", "--name-only"]:
                stdout = "packages/ripgrep.toml\n"
            if cmd == ["git", "rev-parse", "refs/remotes/origin/upstream-release/rolling"]:
                stdout = "abc123\n"
            return subprocess.CompletedProcess(cmd, 0, stdout=stdout, stderr="")

        with tempfile.TemporaryDirectory(prefix="release-bot-pr-") as tmp:
            repo = Path(tmp)
            (repo / "packages").mkdir()
            (repo / "packages" / "ripgrep.toml").write_text(
                'name = "ripgrep"\n', encoding="utf-8"
            )
            with mock.patch.object(self.bot, "_run", side_effect=fake_run), mock.patch.object(
                self.bot, "validate_generated_paths"
            ), mock.patch.object(self.bot, "_remote_branch_exists", return_value=True):
                self.bot._open_or_update_rolling_pr(
                    repo_root=repo,
                    staged_paths=[Path("packages/ripgrep.toml")],
                    base_branch="main",
                    branch_name="upstream-release/rolling",
                    title="chore(registry): update upstream releases",
                    body="## Summary\n- test\n",
                )

        push_index = calls.index(
            [
                "git",
                "push",
                "--force-with-lease=refs/heads/upstream-release/rolling:abc123",
                "-u",
                "origin",
                "upstream-release/rolling",
            ]
        )
        self.assertIn(
            [
                "git",
                "fetch",
                "origin",
                "+refs/heads/upstream-release/rolling:refs/remotes/origin/upstream-release/rolling",
            ],
            calls[:push_index],
        )

    def test_open_or_update_rolling_pr_skips_validation_when_no_staged_changes(self) -> None:
        calls: list[list[str]] = []

        def fake_run(cmd, *, cwd):
            calls.append(cmd)
            stdout = ""
            if cmd == ["git", "diff", "--cached", "--name-only"]:
                stdout = ""
            if cmd[:4] == ["gh", "pr", "list", "--head"]:
                stdout = '[{"number": 12}]'
            return subprocess.CompletedProcess(cmd, 0, stdout=stdout, stderr="")

        with tempfile.TemporaryDirectory(prefix="release-bot-pr-") as tmp:
            repo = Path(tmp)
            with mock.patch.object(self.bot, "_run", side_effect=fake_run), mock.patch.object(
                self.bot, "validate_generated_paths"
            ) as validate_generated_paths:
                self.bot._open_or_update_rolling_pr(
                    repo_root=repo,
                    staged_paths=[Path("state/upstream-release-bot.json")],
                    base_branch="main",
                    branch_name="upstream-release/rolling",
                    title="chore(registry): update upstream releases",
                    body="## Summary\n- test\n",
                )

        validate_generated_paths.assert_not_called()
        self.assertIn(["gh", "pr", "merge", "12", "--auto", "--squash"], calls)

    def test_reconcile_empty_rolling_pr_resets_branch_and_closes_stale_pr(self) -> None:
        calls: list[list[str]] = []

        def fake_run(cmd, *, cwd):
            calls.append(cmd)
            stdout = ""
            if cmd == ["git", "rev-parse", "refs/remotes/origin/upstream-release/rolling"]:
                stdout = "abc123\n"
            if cmd[:4] == ["gh", "pr", "list", "--head"]:
                stdout = '[{"number": 12}]'
            return subprocess.CompletedProcess(cmd, 0, stdout=stdout, stderr="")

        with tempfile.TemporaryDirectory(prefix="release-bot-pr-") as tmp:
            repo = Path(tmp)
            with mock.patch.object(self.bot, "_run", side_effect=fake_run), mock.patch.object(
                self.bot, "_remote_branch_exists", return_value=True
            ):
                self.bot._reconcile_empty_rolling_pr(
                    repo_root=repo,
                    base_branch="main",
                    branch_name="upstream-release/rolling",
                )

        self.assertIn(
            [
                "git",
                "push",
                "--force-with-lease=refs/heads/upstream-release/rolling:abc123",
                "origin",
                "origin/main:refs/heads/upstream-release/rolling",
            ],
            calls,
        )
        self.assertIn(
            [
                "gh",
                "pr",
                "close",
                "12",
                "--comment",
                "Closing stale rolling release PR: no generated changes remain.",
            ],
            calls,
        )

    def test_open_or_update_rolling_pr_force_with_lease_works_without_tracking_ref(self) -> None:
        with tempfile.TemporaryDirectory(prefix="release-bot-git-") as tmp:
            tmp_path = Path(tmp)
            remote = tmp_path / "remote.git"
            seed = tmp_path / "seed"
            repo = tmp_path / "repo"
            subprocess.run(["git", "init", "--bare", remote], check=True)
            subprocess.run(["git", "init", "-b", "main", seed], check=True)

            self._git(seed, "config", "user.name", "Test User")
            self._git(seed, "config", "user.email", "test@example.com")
            self._git(seed, "remote", "add", "origin", str(remote))
            (seed / "README.md").write_text("base\n", encoding="utf-8")
            self._git(seed, "add", "README.md")
            self._git(seed, "commit", "-m", "base")
            self._git(seed, "push", "-u", "origin", "main")

            self._git(seed, "switch", "-c", "upstream-release/rolling")
            (seed / "packages").mkdir()
            (seed / "packages" / "old.toml").write_text('name = "old"\n', encoding="utf-8")
            self._git(seed, "add", "packages/old.toml")
            self._git(seed, "commit", "-m", "old rolling branch")
            self._git(seed, "push", "-u", "origin", "upstream-release/rolling")

            subprocess.run(
                [
                    "git",
                    "clone",
                    "--single-branch",
                    "--branch",
                    "main",
                    str(remote),
                    str(repo),
                ],
                check=True,
            )
            self._git(repo, "config", "user.name", "Test User")
            self._git(repo, "config", "user.email", "test@example.com")
            self.assertNotIn(
                "origin/upstream-release/rolling",
                self._git(repo, "branch", "-r"),
            )

            package_path = repo / "packages" / "ripgrep.toml"
            package_path.parent.mkdir()
            package_path.write_text('name = "ripgrep"\n', encoding="utf-8")

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
                    elif sys.argv[1:3] == ["pr", "merge"]:
                        print("automerge enabled")
                    else:
                        raise SystemExit(f"unexpected gh args: {sys.argv[1:]}")
                    """
                ).strip()
                + "\n",
                encoding="utf-8",
            )
            gh.chmod(0o755)

            previous_path = os.environ.get("PATH", "")
            os.environ["PATH"] = f"{fake_bin}:{previous_path}"
            try:
                with mock.patch.object(self.bot, "validate_generated_paths"):
                    self.bot._open_or_update_rolling_pr(
                        repo_root=repo,
                        staged_paths=[package_path.relative_to(repo)],
                        base_branch="main",
                        branch_name="upstream-release/rolling",
                        title="chore(registry): update upstream releases",
                        body="## Summary\n- test\n",
                    )
            finally:
                os.environ["PATH"] = previous_path

            self.assertIn(
                "origin/upstream-release/rolling",
                self._git(repo, "branch", "-r"),
            )
            remote_package = subprocess.run(
                [
                    "git",
                    f"--git-dir={remote}",
                    "show",
                    "refs/heads/upstream-release/rolling:packages/ripgrep.toml",
                ],
                check=True,
                text=True,
                capture_output=True,
            ).stdout
            self.assertEqual(
                'name = "ripgrep"\n',
                remote_package,
            )

    def test_open_or_update_rolling_pr_refreshes_stale_tracking_ref_with_force_fetch(self) -> None:
        with tempfile.TemporaryDirectory(prefix="release-bot-git-") as tmp:
            tmp_path = Path(tmp)
            remote = tmp_path / "remote.git"
            seed = tmp_path / "seed"
            repo = tmp_path / "repo"
            subprocess.run(["git", "init", "--bare", remote], check=True)
            subprocess.run(["git", "init", "-b", "main", seed], check=True)

            self._git(seed, "config", "user.name", "Test User")
            self._git(seed, "config", "user.email", "test@example.com")
            self._git(seed, "remote", "add", "origin", str(remote))
            (seed / "README.md").write_text("base\n", encoding="utf-8")
            self._git(seed, "add", "README.md")
            self._git(seed, "commit", "-m", "base")
            self._git(seed, "push", "-u", "origin", "main")

            self._git(seed, "switch", "-c", "upstream-release/rolling")
            (seed / "packages").mkdir()
            (seed / "packages" / "old.toml").write_text('name = "old"\n', encoding="utf-8")
            self._git(seed, "add", "packages/old.toml")
            self._git(seed, "commit", "-m", "old rolling branch")
            self._git(seed, "push", "-u", "origin", "upstream-release/rolling")

            subprocess.run(["git", "clone", str(remote), str(repo)], check=True)
            self._git(repo, "config", "user.name", "Test User")
            self._git(repo, "config", "user.email", "test@example.com")
            self.assertIn("origin/upstream-release/rolling", self._git(repo, "branch", "-r"))

            self._git(seed, "switch", "upstream-release/rolling")
            self._git(seed, "reset", "--hard", "origin/main")
            (seed / "packages").mkdir()
            (seed / "packages" / "new-remote.toml").write_text(
                'name = "new-remote"\n', encoding="utf-8"
            )
            self._git(seed, "add", "packages/new-remote.toml")
            self._git(seed, "commit", "-m", "rewritten rolling branch")
            self._git(seed, "push", "--force", "origin", "upstream-release/rolling")

            package_path = repo / "packages" / "ripgrep.toml"
            package_path.parent.mkdir()
            package_path.write_text('name = "ripgrep"\n', encoding="utf-8")

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
                    elif sys.argv[1:3] == ["pr", "merge"]:
                        print("automerge enabled")
                    else:
                        raise SystemExit(f"unexpected gh args: {sys.argv[1:]}")
                    """
                ).strip()
                + "\n",
                encoding="utf-8",
            )
            gh.chmod(0o755)

            previous_path = os.environ.get("PATH", "")
            os.environ["PATH"] = f"{fake_bin}:{previous_path}"
            try:
                with mock.patch.object(self.bot, "validate_generated_paths"):
                    self.bot._open_or_update_rolling_pr(
                        repo_root=repo,
                        staged_paths=[package_path.relative_to(repo)],
                        base_branch="main",
                        branch_name="upstream-release/rolling",
                        title="chore(registry): update upstream releases",
                        body="## Summary\n- test\n",
                    )
            finally:
                os.environ["PATH"] = previous_path

            remote_package = subprocess.run(
                [
                    "git",
                    f"--git-dir={remote}",
                    "show",
                    "refs/heads/upstream-release/rolling:packages/ripgrep.toml",
                ],
                check=True,
                text=True,
                capture_output=True,
            ).stdout
            self.assertEqual('name = "ripgrep"\n', remote_package)

    def test_open_or_update_rolling_pr_does_not_fetch_missing_remote_branch(self) -> None:
        calls: list[list[str]] = []

        def fake_run(cmd, *, cwd):
            calls.append(cmd)
            if cmd == ["git", "fetch", "origin", "upstream-release/rolling"]:
                self.fail("missing rolling branch should not be fetched")
            stdout = ""
            if cmd[:4] == ["gh", "pr", "list", "--head"]:
                stdout = "[]"
            if cmd == ["git", "diff", "--cached", "--name-only"]:
                stdout = "packages/ripgrep.toml\n"
            return subprocess.CompletedProcess(cmd, 0, stdout=stdout, stderr="")

        with tempfile.TemporaryDirectory(prefix="release-bot-pr-") as tmp:
            repo = Path(tmp)
            (repo / "packages").mkdir()
            (repo / "packages" / "ripgrep.toml").write_text(
                'name = "ripgrep"\n', encoding="utf-8"
            )
            with mock.patch.object(self.bot, "_run", side_effect=fake_run), mock.patch.object(
                self.bot, "validate_generated_paths"
            ), mock.patch.object(self.bot, "_remote_branch_exists", return_value=False):
                self.bot._open_or_update_rolling_pr(
                    repo_root=repo,
                    staged_paths=[Path("packages/ripgrep.toml")],
                    base_branch="main",
                    branch_name="upstream-release/rolling",
                    title="chore(registry): update upstream releases",
                    body="## Summary\n- test\n",
                )

        self.assertIn(
            ["git", "push", "--force-with-lease", "-u", "origin", "upstream-release/rolling"],
            calls,
        )

    def test_open_or_update_pr_enables_automerge_for_existing_pr(self) -> None:
        with tempfile.TemporaryDirectory(prefix="release-bot-pr-") as tmp:
            repo = Path(tmp)
            calls: list[list[str]] = []

            def fake_run(cmd, *, cwd):
                calls.append(cmd)
                if cmd[:3] == ["git", "ls-remote", "--heads"]:
                    return subprocess.CompletedProcess(
                        cmd,
                        0,
                        stdout="abc\trefs/heads/upstream-release/ripgrep/15.2.0\n",
                        stderr="",
                    )
                if cmd == ["git", "diff", "--cached", "--name-only"]:
                    return subprocess.CompletedProcess(
                        cmd,
                        0,
                        stdout="packages/ripgrep.toml\nreleases/ripgrep/15.2.0.toml\n",
                        stderr="",
                    )
                if cmd[:5] == ["gh", "pr", "list", "--head", "upstream-release/ripgrep/15.2.0"]:
                    return subprocess.CompletedProcess(cmd, 0, stdout='[{"number": 12}]', stderr="")
                if cmd[:3] == ["git", "cat-file", "-e"]:
                    return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="")
                return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

            with (
                mock.patch.object(self.bot, "_run", side_effect=fake_run),
                mock.patch.object(self.bot, "validate_generated_paths"),
                mock.patch.object(self.bot, "_stash_paths_for_branch_switch", return_value=None),
            ):
                self.bot._open_or_update_pr(
                    repo_root=repo,
                    staged_paths=[
                        Path("packages/ripgrep.toml"),
                        Path("releases/ripgrep/15.2.0.toml"),
                    ],
                    package="ripgrep",
                    version="15.2.0",
                    base_branch="main",
                    branch_prefix="upstream-release",
                )

        self.assertIn(["gh", "pr", "merge", "12", "--auto", "--squash"], calls)

    def test_open_or_update_pr_enables_automerge_when_existing_branch_has_no_diff(self) -> None:
        with tempfile.TemporaryDirectory(prefix="release-bot-pr-") as tmp:
            repo = Path(tmp)
            calls: list[list[str]] = []

            def fake_run(cmd, *, cwd):
                calls.append(cmd)
                if cmd[:3] == ["git", "ls-remote", "--heads"]:
                    return subprocess.CompletedProcess(
                        cmd,
                        0,
                        stdout="abc\trefs/heads/upstream-release/ripgrep/15.2.0\n",
                        stderr="",
                    )
                if cmd == ["git", "diff", "--cached", "--name-only"]:
                    return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
                if cmd[:5] == ["gh", "pr", "list", "--head", "upstream-release/ripgrep/15.2.0"]:
                    return subprocess.CompletedProcess(cmd, 0, stdout='[{"number": 12}]', stderr="")
                return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

            with (
                mock.patch.object(self.bot, "_run", side_effect=fake_run),
                mock.patch.object(self.bot, "validate_generated_paths"),
                mock.patch.object(self.bot, "_stash_paths_for_branch_switch", return_value=None),
            ):
                self.bot._open_or_update_pr(
                    repo_root=repo,
                    staged_paths=[
                        Path("packages/ripgrep.toml"),
                        Path("releases/ripgrep/15.2.0.toml"),
                    ],
                    package="ripgrep",
                    version="15.2.0",
                    base_branch="main",
                    branch_prefix="upstream-release",
                )

        self.assertIn(["gh", "pr", "merge", "12", "--auto", "--squash"], calls)
        self.assertNotIn(["git", "commit", "-m", "chore(registry): add ripgrep 15.2.0"], calls)

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
                    elif sys.argv[1:3] == ["pr", "merge"]:
                        print("automerge enabled")
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
                with mock.patch.object(self.bot, "validate_generated_paths"):
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

    def test_open_or_update_pr_restores_generated_state_removed_by_branch_switch(self) -> None:
        with tempfile.TemporaryDirectory(prefix="release-bot-git-") as tmp:
            tmp_path = Path(tmp)
            remote = tmp_path / "remote.git"
            repo = tmp_path / "repo"
            subprocess.run(["git", "init", "--bare", remote], check=True)
            subprocess.run(["git", "init", "-b", "main", repo], check=True)

            self._git(repo, "config", "user.name", "Test User")
            self._git(repo, "config", "user.email", "test@example.com")
            self._git(repo, "remote", "add", "origin", str(remote))

            packages_dir = repo / "packages"
            packages_dir.mkdir()
            package_path = packages_dir / "gh.toml"
            package_path.write_text('name = "gh"\n', encoding="utf-8")
            self._git(repo, "add", "packages/gh.toml")
            self._git(repo, "commit", "-m", "base")
            self._git(repo, "push", "-u", "origin", "main")

            self._git(repo, "switch", "-c", "upstream-release/deno/2.7.14")
            state_path = repo / "state" / "upstream-release-bot.json"
            state_path.parent.mkdir()
            state_path.write_text('{"schema_version":1}\n', encoding="utf-8")
            self._git(repo, "add", "state/upstream-release-bot.json")
            self._git(repo, "commit", "-m", "existing release branch")

            release_path = repo / "releases" / "gh" / "2.92.0.toml"
            release_path.parent.mkdir(parents=True)
            release_path.write_text('version = "2.92.0"\n', encoding="utf-8")

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
                    elif sys.argv[1:3] == ["pr", "merge"]:
                        print("automerge enabled")
                    else:
                        raise SystemExit(f"unexpected gh args: {sys.argv[1:]}")
                    """
                ).strip()
                + "\n",
                encoding="utf-8",
            )
            gh.chmod(0o755)

            previous_path = os.environ.get("PATH", "")
            os.environ["PATH"] = f"{fake_bin}:{previous_path}"
            try:
                with mock.patch.object(self.bot, "validate_generated_paths"):
                    self.bot._open_or_update_pr(
                        repo_root=repo,
                        staged_paths=[
                            package_path.relative_to(repo),
                            release_path.relative_to(repo),
                            state_path.relative_to(repo),
                        ],
                        package="gh",
                        version="2.92.0",
                        base_branch="main",
                        branch_prefix="upstream-release",
                    )
            finally:
                os.environ["PATH"] = previous_path

            self.assertEqual(
                self._git(repo, "branch", "--show-current"),
                "upstream-release/gh/2.92.0",
            )
            self.assertTrue(state_path.exists())
            self.assertIn(
                "chore(registry): add gh 2.92.0",
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
                        return_value=self.bot.GithubReleaseFetchResult(
                            releases=[
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
                            ]
                        ),
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
