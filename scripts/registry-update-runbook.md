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
