# TDSC Summary Experiment Review - 2026-07-10

Purpose: classify the current experiment corpus by what it actually proves. The
paper must not use a narrow or smoke-test result as evidence for a broader
causal claim.

## Verdict

The current experiment package is useful as a diagnostic artifact, but it is not
submission-ready evidence for the manuscript's strongest claims. Two high-impact
evidence failures dominate:

- the external-PEP comparison is a four-case smoke test with per-query row
  budgeting, not a cumulative-budget baseline;
- the 10k scale benchmark measures small-output queries over a 10k scoped table,
  not disclosure or materialization of 10k rows.

Until repaired, the summary tables should be framed as exploratory diagnostics
only.

## Evidence Matrix

| Experiment family | Primary file(s) | What it currently supports | What it does not support | Required repair |
|---|---|---|---|---|
| External PEP composition | `paper/tdsc/scripts/functional_equivalent_baseline_eval.py`; `paper/tdsc/raw_results/functional_equivalent_baseline_20260710_124931.json` | A toy external PEP can allow one bounded query, deny one per-query over-budget query, deny one credential replay, and demonstrate a direct-role bypass when the PEP is skipped. | Fair equivalence to SessionBound cumulative disclosure accounting; workload-level baseline fairness; causally comparing database-resident and external reference monitors. | Add persistent per-task budget state, cumulative multi-query workloads, matched receipts/audit, repeated pagination/projection cases, and direct-bypass negative controls. |
| Native end-to-end 10k scale | `paper/tdsc/scripts/native_end_to_end_benchmark.py`; `paper/tdsc/raw_results/native_end_to_end_20260710_110541.json` | Query latency over 10k scoped rows for small-output query shapes: GROUP BY/CTE return 10 rows; SELECT/JOIN/window return 50 rows. | Large-result disclosure, 10k-row materialization, or 10k-row returned-result bottleneck. | Add detail-release patterns that return 1k/10k rows, record returned row counts in the manuscript table, and separate scanned-row scale from disclosed-row scale. |
| Hook-only microbenchmark | `paper/tdsc/scripts/hook_microbenchmark.py`; latest `hook_microbenchmark_*.json` | Parse/analyze plus structural guard overhead for non-executing checks. | End-to-end SessionBound latency, budget accounting, receipt insertion, executor result buffering, or data-release performance. | Keep as a separate microbenchmark only; never use it as evidence for full query execution. |
| Adversarial SQL/API suite | `paper/tdsc/scripts/adversarial_sql_eval.py`; latest `adversarial_sql_*.json` | API/wrapper classification for a broad set of tested SQL shapes. | Native parity, complete SQL safety, or semantic inference prevention. | Add native-equivalent tests for every claimed adversarial class; include JSON aggregate aliases and window partition variants. |
| Native hook/direct DB suite | `paper/tdsc/scripts/sessionbound_guard_hook_eval.py`; latest `sessionbound_guard_hook_*.json` | Selected direct DB paths: bound SELECT, prepared/cursor/COPY/EXPLAIN cases, and a few hook-only denials. | Always-on native reference-monitor completeness because static early returns remain in accounting eligibility. | Add tests for role switching after bind, raw-text helper substring confusion, JSON aggregate aliases, and window partition leakage. |
| Rollback audit | `paper/tdsc/scripts/rollback_audit_eval.py`; latest `rollback_audit_*.json` | Some denial/allow receipts survive rollback in tested paths. | Receipt completeness for bypassed accounting paths or untested native denials. | Re-run after native accounting eligibility is rebuilt; include bypass-regression paths. |
| Single-active binding | `paper/tdsc/scripts/single_active_binding_eval.py`; latest `single_active_binding_*.json` | Same-key active-owner fencing under tested races and lifecycle cases. | Overall reference-monitor correctness or disclosure-budget completeness. | Keep as a separate binding/fencing result; do not use it to imply SQL safety. |
| Overhead breakdown | `paper/tdsc/scripts/overhead_breakdown.py`; latest `overhead_breakdown_*.json/csv` | Relative latency of current wrapper/ablation modes on small/default data. | Large-result performance, optimized native accounting, or production throughput. | Preserve as a small-workload ablation; add separate large-output and native repaired runs. |

## Claim Downgrades

The following manuscript interpretations should be removed or rewritten now:

- "10k-row detail-release stress" if the table's measured `rows_returned` is
  10 or 50.
- "Materialization bottleneck for 10k disclosure" unless a run returns and
  accounts 10k detail tuples.
- "Functionally equivalent external PEP" unless it includes cumulative
  multi-query budget state.
- "Native hook/executor enforcement covers the reference monitor" while
  accounting can return early based on raw SQL text or role-switch state.
- "Payload aggregation blocked" unless API and native denylist coverage match.
- "Minimum-group/window inference covered" unless window partition cases are
  structurally checked and tested.

## Replacement Table Requirements

Before the paper can restore a mainline evaluation table, each row should carry:

- scoped rows prepared;
- rows returned/disclosed;
- rows scanned when available from `EXPLAIN (ANALYZE, BUFFERS)` or another
  trusted measurement path;
- whether query budget is cumulative across the workload;
- whether disclosure budget is cumulative across the workload;
- whether allow/deny receipts are emitted and verified;
- whether the mode is API/wrapper, native executor, hook-only, or external PEP.

This will make causal interpretation much harder to accidentally overstate.

## Minimum Rerun Gate

A repaired summary package should include at least:

1. External PEP cumulative baseline: 20+ cases, including repeated pagination,
   projection/alias variants, aggregate payload variants, credential replay,
   direct DB bypass, and per-task audit state.
2. Native bypass suite: role switch after binding, helper-substring confusion,
   JSON aggregate aliases, window partition variants, prepared/cursor/COPY
   coverage after the accounting rewrite.
3. Scale split: one experiment for scoped-row scan cost and one for returned-row
   disclosure/materialization cost, with returned-row counts visible in the
   paper table.
4. Claim table regenerated from raw results, not manually summarized.
