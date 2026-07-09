# Receipt Overhead Results

Raw file: `raw_results/ablation_1783224624.json`.

The ablation run compares the full SessionBound runtime with
receipt-disabled, budget-accounting-disabled, and both-disabled variants
over three query patterns. Each variant used 10 warmup and 60 measured
iterations.

| Variant | Count p50 ms | Detail-25 p50 ms | Group-by p50 ms | Receipt count | Query count |
|---|---:|---:|---:|---:|---:|
| Full SessionBound | 29.047 | 30.945 | 29.764 | 60 | 60 |
| Receipts off | 31.401 | 31.573 | 33.559 | 0 | 60 |
| Budget accounting off | 30.859 | 29.356 | 30.421 | 60 | 0 |
| Receipts and budget off | 30.221 | 30.033 | 27.781 | 0 | 0 |

The control flags behaved as intended: receipt-disabled variants emitted
zero receipts, and budget-disabled variants left `query_count` unchanged.
The latency deltas are noisy on the small seed dataset, so this result
should not be read as a stable attribution of overhead to one component.
