# TDSC Blocking Audit - 2026-07-10

Status: reject-and-rebuild. This note records the latest evidence check for the
TDSC manuscript and artifact. The issues below are claim-validity problems, not
just missing tests.

## P0 Findings

1. Functionally equivalent external PEP is not equivalent enough.

- Evidence: `paper/tdsc/scripts/functional_equivalent_baseline_eval.py` defines
  only four cases: EQ01--EQ04.
- Evidence: `external_pep(...)` enforces `if len(rows) > max_rows`, which is a
  per-query returned-row check.
- Impact: this does not implement the cumulative disclosure budget emphasized
  by SessionBound. The comparison can show a TCB distinction only under a toy
  PEP path assumption; it cannot support a fairness or equivalence claim.
- Required repair: implement a cumulative multi-query external PEP baseline
  using the same workload, task token semantics, credential binding,
  per-task budget state, deny receipts, and bypass tests.

2. The 10k scale experiment does not disclose 10k rows.

- Evidence: `paper/tdsc/scripts/native_end_to_end_benchmark.py` prepares
  `target_rows`, but SELECT/JOIN/window patterns have `LIMIT 50`; GROUP BY and
  CTE aggregate to small category result sets.
- Evidence: `paper/tdsc/raw_results/native_end_to_end_20260710_110541.json`
  records 10k-target `rows_returned` as 10 for GROUP BY/CTE and 50 for
  SELECT/JOIN/window across all modes.
- Impact: the paper currently uses the run to explain large-result disclosure
  and materialization bottlenecks. The measured result is actually small-output
  queries over a larger scoped table.
- Required repair: either measure actual large detail releases or relabel the
  current experiment as scoped-data/small-output diagnostic evidence.

3. Native reference monitor has bypassable enforcement gates.

- Evidence: `should_account_query(...)` skips accounting if
  `source_is_runtime_helper_call(query_desc->sourceText)` finds raw SQL
  substrings such as `taskbound.run`, `taskbound.native_`, or
  `public.sessionbound_guard_check`.
- Evidence: the same function skips accounting when
  `GetUserId() != GetSessionUserId()`.
- Impact: executor accounting depends on raw SQL text and session/user
  coincidence rather than the analyzed plan. Pre-bound role switching can trigger
  early return. This breaks the native reference-monitor claim.
- Required repair: base accounting eligibility on analyzed/planned statement
  properties and trusted internal execution state, not caller text. Revalidate
  role switching after binding.

- Resolution (2026-07-17): Partially addressed; see
  `paper/revision_notes/native_refmon_text_bypass_fix_20260717.md`. The raw-text
  gate was the genuine bypass and has been removed: `source_is_runtime_helper_call`
  and its call site in `should_account_query` are deleted. It was redundant
  (direct helper calls are denied at plan analysis by
  `function_is_taskbound_private`; guard-internal SELECTs run under
  `guard_in_internal_spi`) and exploitable (any task SELECT could exempt itself
  by embedding a helper substring in a comment/literal/alias). The
  `GetUserId() != GetSessionUserId()` check is intentionally RETAINED: it is the
  load-bearing discriminator between the wrapper path (`taskbound.run` /
  `bind_task` are SECURITY DEFINER, accounted by the PL/pgSQL wrapper itself) and
  the native bare-SELECT path (accounted here), not a bypass -- the utility
  precheck refuses SET ROLE once a binding is active, so a native session cannot
  change effective user to evade it. Verified: guard hook 18/18 unchanged, and
  `paper/tdsc/scripts/native_text_bypass_probe.py` shows the bypass closed on the
  fixed build (before/after: `bypass_closed` false -> true).

4. Native structural checks are incomplete for claimed attack families.

- Evidence: native `function_is_payload_aggregation(...)` blocks `json_agg`,
  `jsonb_agg`, `array_agg`, `string_agg`, `xmlagg`, `row_to_json`,
  `json_build_object`, and `jsonb_build_object`, while the API validator also
  blocks `json_array_agg` and `json_arrayagg`.
- Evidence: native group policy checks HAVING and direct entity GROUP BY, but
  there is no comparable complete window-partition policy.
- Impact: JSON aggregate variants and window partition variants undermine the
  manuscript's native coverage claim.
- Required repair: unify the API and native denylist, add native tests for JSON
  aggregate aliases, and add policy/test coverage for window partitions.

## Broken Claims

The current artifact does not support the manuscript's present form of these
claims:

- I3: approved safe-view relation enforcement is not established as an
  always-on native reference-monitor property under role-switch/helper cases.
- I4: blocked payload aggregation is incomplete in the native path.
- I6: monotonic budget accounting can be bypassed by native accounting early
  returns.
- I7: receipt completeness falls with accounting early returns.
- I9: direct small-group aggregate coverage is incomplete for window-partition
  variants.

## Summary Experiment Review

The current summary experiments should be treated as diagnostic smoke evidence:

- Adversarial/API suites are useful, but they do not prove native parity.
- Hook-only microbenchmarks measure structural parse/analyze checks, not
  accounting-complete execution.
- The 10k scale table measures small-output query shapes over 10k scoped rows.
- External PEP evidence is four-case smoke coverage and per-query budgeting.

The manuscript should remove or sharply qualify causal language that attributes
multi-second behavior to "10k-row disclosure" unless a rerun actually returns
large detail results.

## Novelty Review

The novelty should be narrowed. Recent and adjacent work already covers:

- task-scoped operation authorization for agents, including PAuth;
- database-enforced identity/context-aware access for agentic AI, including
  Oracle Deep Data Security;
- governed data-product and MCP-based enterprise querying, including Data
  Product MCP;
- information-flow-control approaches for agent security, including Fides/IFC;
- classical RLS/VPD, purpose-based databases, query auditing, inference control,
  capabilities, OAuth/delegation, and ABAC/XACML.

The defensible claim is not first task-scoped authorization, not first
database-side agent security, and not first governed enterprise data access.
The remaining possible contribution is a database-centered composition:
task token plus short-lived credential, safe-view registry, cumulative
disclosure budget, drift invalidation, and receipt-bearing execution for
open-ended SQL. That contribution only becomes credible after the native
reference monitor and cumulative-budget evidence are rebuilt.

## Rebuild Path

1. Redesign native accounting eligibility around plan/analyze state.
2. Remove raw SQL substring exemptions from security decisions.
3. Add regression tests for pre-bound role switching.
4. Unify payload aggregation denylist across API and native code.
5. Add window-partition and JSON aggregate adversarial cases.
6. Build a cumulative external-PEP baseline.
7. Rerun large-result experiments with actual large result sets, or rewrite the
   paper around small-output scoped-data diagnostics.
8. Rewrite security invariants, evaluation tables, limitations, and conclusion
   after the rerun.
