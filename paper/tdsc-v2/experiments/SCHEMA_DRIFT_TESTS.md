# Schema Drift Tests

Raw file: `raw_results/credential_token_1783224156.json`.

Schema drift invalidation is now implemented in the measured prototype
path. Task tokens carry a safe-view registry snapshot with:

- `safe_view_registry_version`
- per-view `policy_version`
- per-view and aggregate `view_definition_hash`

At `taskbound.bind_task`, the runtime recomputes the safe-view registry
snapshot and rejects stale task tokens. The credential-token test suite
confirms denial for registry-version drift, policy-version drift, and
view-definition hash mismatch.
