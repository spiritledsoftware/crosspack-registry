# Registry seed definitions runbook

Use this runbook when reviewing or updating the first Crosspack Registry seed set.

## Seed source of truth

- Seed catalog: `registry/seed-definitions.toml`
- Package templates: `packages/*.toml`
- Source configs: `registry/sources/*.toml`
- Release manifests: `releases/<package>/*.toml`

## Review checklist

1. Confirm every package template in `packages/` has exactly one `[[seeds]]` entry.
2. Confirm each seed entry has:
   - `package`
   - `category`
   - `rationale`
   - `review_notes`
3. Check that each seeded package still has:
   - a matching source config in `registry/sources/`
   - at least one release manifest under `releases/<package>/`
4. If package metadata changed, validate the corresponding package and release manifests too.

## Validation commands

```bash
python3 scripts/registry-validate-seed-definitions.py registry/seed-definitions.toml
python3 scripts/registry-validate-source.py --require-package-coverage registry/sources/*.toml
REGISTRY_PREFLIGHT_ALL=1 REGISTRY_PREFLIGHT_SKIP_SMOKE=1 ./scripts/registry-preflight.sh
python3 -m unittest \
  tests.test_registry_validate_seed_definitions \
  tests.test_registry_validate_source \
  tests.test_registry_validate \
  tests.test_registry_generate_manifest \
  tests.test_upstream_release_bot -v
```

## Update procedure

1. Edit `registry/seed-definitions.toml`.
2. If adding or removing a package from the seed set, update package/source/release coverage in the repo first.
3. Run the validation commands above.
4. Open a PR summarizing:
   - which seed entries changed
   - whether package/source/release coverage changed
   - the validation commands you ran

## Scope guardrails

- Keep the seed file tightly focused on the flagship package set already present in the registry.
- Do not add speculative fields or alternate registry schemas in this file.
- Treat the runbook and validator as the repeatable operator path for future seed reviews.
