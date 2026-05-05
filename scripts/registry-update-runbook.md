# Registry update runbook

Use this runbook when reviewing or updating Crosspack Registry package and release metadata.

## Source of truth

- Package templates and upstream automation config: `packages/*.toml`
- Release manifests: `releases/<package>/*.toml`

## Review checklist

1. Confirm each package template has package identity, `[source.*]` automation metadata, and artifact templates.
2. Confirm each package has at least one release manifest under `releases/<package>/` before publishing it as supported.
3. If package metadata changed, validate the corresponding package and release manifests.
4. If upstream automation changed, run the release bot in dry-run mode for the affected package.

## Validation commands

Run these commands from the `registry/` submodule root.

```bash
python3 scripts/registry-validate-source.py packages/*.toml
REGISTRY_PREFLIGHT_ALL=1 REGISTRY_PREFLIGHT_SKIP_SMOKE=1 ./scripts/registry-preflight.sh
python3 -m unittest \
  tests.test_registry_validate_source \
  tests.test_registry_validate \
  tests.test_registry_generate_manifest \
  tests.test_upstream_release_bot -v
```

## Update procedure

1. Edit `packages/<package>.toml` for shared metadata, source strategy, or artifact-template changes.
2. Add or regenerate `releases/<package>/<version>.toml` for concrete version metadata.
3. Run the validation commands above.
4. Open a PR summarizing package/release coverage changes and the validation commands you ran.

## Rolling Upstream Release Bot

The scheduled upstream release bot maintains one rolling PR from `upstream-release/rolling`. Each write run starts from current `main`, regenerates valid package updates, writes `state/upstream-release-bot.json`, force-updates only that bot-owned branch with `--force-with-lease`, and enables automerge. Dry runs use the same discovery path without writing metadata or opening PRs.

Bot state is runtime-normalized to schema v2. Checked-in schema v1 state is migrated on read:

- `schema_version`: currently `2` after runtime migration.
- `sources`: source-level cache/audit state keyed by strategy and upstream identity, such as `github_releases:owner/repo`.
- `packages`: package-level audit state, including source identity/kind, latest seen version, last successful generated version, `last_checked_at`, transient failure fields, and optional `backoff_until`.
- `quarantine`: package-level records keyed by package name with `reason_code`, `detail`, `first_seen_at`, `last_seen_at`, `attempted_version`, and optional `last_good_version`.

Malformed generated package updates are recorded under `quarantine` and omitted from generated metadata until a later run regenerates valid metadata and package validation passes. Quarantine is package-scoped; unrelated packages continue in the same run. Rate-limited or transient upstream failures update the affected package's `backoff_until` and skip only that package/source until the timestamp expires.

Bot output includes stable summary accounting for automation consumers:

```text
registry_update package=<name> status=quarantined reason=metadata-malformed attempted=<version>
registry_update package=<name> status=skipped reason=<rate-limited|upstream-error|backoff-active> reset_at=<iso8601>
registry_update_summary updated=<n> up_to_date=<n> quarantined=<n> transient_failed=<n> skipped=<n>
```

Crosspack clients may skip package-level poison during broad package discovery and print additive warnings such as:

```text
warning: registry_package_skipped package="<name>" reason="package-metadata-invalid" source="<source>" detail="<detail>"
```

Source-level trust still fails closed. Missing or invalid `registry.pub`, configured fingerprint mismatches, missing ready snapshots, and missing or invalid metadata signatures are fatal for the source.

Bot PR bodies summarize updated packages, generated package/release counts, state-only changes, quarantine additions/updates/clears, backoff packages, and validation commands. Bot PRs may contain unsigned `packages/*.toml` and `releases/*/*.toml` files; the `sign-manifests-on-merge` workflow signs changed sidecars after merge.

Recovery commands:

```bash
python3 scripts/upstream-release-bot.py --dry-run --package <package>
python3 scripts/registry-validate-source.py packages/<package>.toml
python3 scripts/registry-validate.py --allow-missing-signatures packages/<package>.toml releases/<package>/<version>.toml
git push --force-with-lease origin upstream-release/rolling
```
