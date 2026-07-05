# Receipt Overhead Results

Receipt overhead was not isolated in this v2 experiment pass.

The Full SessionBound performance path includes receipt insertion along
with PL/pgSQL runtime dispatch, SQL checks, safe-view validation, budget
updates, and disclosure accounting. Because no receipt-disabled
SessionBound variant was measured, this run cannot attribute a specific
latency share to receipts alone.
