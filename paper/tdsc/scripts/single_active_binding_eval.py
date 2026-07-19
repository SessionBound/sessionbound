#!/usr/bin/env python3
"""Evaluate global single-active SessionBound bindings across PostgreSQL backends."""

from __future__ import annotations

import argparse
import csv
import json
import os
import socket
import statistics
import subprocess
import threading
import time
import urllib.error
import urllib.request
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import psycopg


REPO_ROOT = Path(__file__).resolve().parents[3]
BASE_URL = os.environ.get("TDSC_BASE_URL", "http://127.0.0.1:8000")


def git_commit() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=REPO_ROOT,
            text=True,
        ).strip()
    except Exception:
        return "unknown"


def post_json(path: str, body: dict[str, Any]) -> dict[str, Any]:
    req = urllib.request.Request(
        BASE_URL.rstrip("/") + path,
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        try:
            payload = json.loads(exc.read().decode("utf-8"))
        except Exception:
            payload = {"detail": f"HTTP {exc.code}"}
        return {"ok": False, "http_error": exc.code, **payload}
    except (urllib.error.URLError, ConnectionResetError, socket.timeout) as exc:
        return {"ok": False, "error": str(exc)}


def wait_for_api() -> None:
    for _ in range(45):
        try:
            urllib.request.urlopen(BASE_URL.rstrip("/") + "/", timeout=2).read()
            return
        except Exception:
            time.sleep(1)
    raise RuntimeError(f"API did not become ready at {BASE_URL}")


def credential_dsn(credential: dict[str, Any], host: str, port: str, dbname: str) -> str:
    return f"postgresql://{credential['db_user']}:{credential['db_password']}@{host}:{port}/{dbname}"


def admin_dsn(host: str, port: str, dbname: str) -> str:
    return f"postgresql://postgres:postgres@{host}:{port}/{dbname}"


def rows_as_dicts(cur) -> list[dict[str, Any]]:
    columns = [desc.name for desc in cur.description]
    return [dict(zip(columns, row, strict=True)) for row in cur.fetchall()]


def issue_credential(run_id: str) -> dict[str, Any]:
    credential = post_json(
        "/credentials",
        {
            "agent_id": f"single-active-{run_id}",
            "actor": "agent:travel-expense-analyst",
            "ttl_minutes": 30,
        },
    )
    if not credential.get("db_user"):
        raise RuntimeError(f"credential issue failed: {credential}")
    return credential


def issue_task(task_id: str, credential_id: str, *, max_queries: int = 100, max_rows: int = 5000) -> dict[str, Any]:
    task = post_json(
        "/tasks",
        {
            "task_id": task_id,
            "task_type": "monthly_travel_expense_review",
            "delegator": "user:alice",
            "actor": "agent:travel-expense-analyst",
            "credential_id": credential_id,
            "scope": {"expense_month": "2026-06"},
            "max_rows": max_rows,
            "max_queries": max_queries,
        },
    )
    if not task.get("payload_text"):
        raise RuntimeError(f"task issue failed: {task}")
    return task


def bind_once(cur, task: dict[str, Any]) -> dict[str, Any]:
    start = time.perf_counter()
    try:
        cur.execute("SELECT taskbound.bind_task(%s, %s)", (task["payload_text"], task["signature"]))
        return {"ok": True, "bound": cur.fetchone()[0], "latency_ms": (time.perf_counter() - start) * 1000}
    except Exception as exc:
        return {
            "ok": False,
            "sqlstate": getattr(exc, "sqlstate", None),
            "error": str(exc).split("\n")[0],
            "latency_ms": (time.perf_counter() - start) * 1000,
        }


def unbind_quietly(cur) -> None:
    try:
        cur.execute("SELECT taskbound.unbind_task()")
    except Exception:
        pass


def race_once(dsn: str, task: dict[str, Any], contenders: int, hold_ms: int = 30) -> dict[str, Any]:
    barrier = threading.Barrier(contenders)
    lock = threading.Lock()
    results: list[dict[str, Any]] = []

    def worker(index: int) -> None:
        with psycopg.connect(dsn, autocommit=True) as conn:
            with conn.cursor() as cur:
                barrier.wait()
                result = bind_once(cur, task)
                result["contender"] = index
                with lock:
                    results.append(result)
                if result["ok"]:
                    time.sleep(hold_ms / 1000)
                    unbind_quietly(cur)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(contenders)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=20)
    if any(thread.is_alive() for thread in threads):
        raise RuntimeError("race worker did not finish")

    success_count = sum(1 for item in results if item["ok"])
    denied = [item for item in results if not item["ok"]]
    denial_reasons = Counter(item.get("error") or item.get("sqlstate") or "unknown" for item in denied)
    return {
        "contenders": contenders,
        "success_count": success_count,
        "denied_count": len(denied),
        "dual_success": int(success_count > 1),
        "zero_success": int(success_count == 0),
        "unexpected_error_count": sum(
            1
            for item in denied
            if item.get("sqlstate") != "55P03" or "ACTIVE_BINDING_EXISTS" not in (item.get("error") or "")
        ),
        "denial_reason_counts": dict(denial_reasons),
        "latencies_ms": [item["latency_ms"] for item in results],
        "results": results,
    }


def repeated_races(dsn: str, task: dict[str, Any], rounds: int) -> dict[str, Any]:
    records = [race_once(dsn, task, 2) for _ in range(rounds)]
    denial_counts: Counter[str] = Counter()
    latencies: list[float] = []
    for record in records:
        denial_counts.update(record["denial_reason_counts"])
        latencies.extend(record["latencies_ms"])
    return {
        "rounds": rounds,
        "single_winner_rounds": sum(1 for record in records if record["success_count"] == 1),
        "dual_success_rounds": sum(record["dual_success"] for record in records),
        "zero_winner_rounds": sum(record["zero_success"] for record in records),
        "unexpected_error_count": sum(record["unexpected_error_count"] for record in records),
        "denial_reason_counts": dict(denial_counts),
        "latencies_ms": latencies,
    }


def fetch_state(cur) -> dict[str, Any]:
    cur.execute("SELECT * FROM taskbound.inspect_task_state()")
    rows = rows_as_dicts(cur)
    return rows[0] if rows else {}


def fetch_receipts(cur) -> list[dict[str, Any]]:
    cur.execute(
        """
        SELECT task_id, binding_id, fence_token, decision, rows_returned,
               unique_rows_added, remaining_unique_row_budget, reason
        FROM taskbound.receipts()
        ORDER BY created_at, receipt_id
        """
    )
    return rows_as_dicts(cur)


def active_row(admin_conn, task_id: str) -> dict[str, Any]:
    with admin_conn.cursor() as cur:
        cur.execute(
            """
            SELECT task_id, binding_id::text AS binding_id, fence_token,
                   advisory_lock_key, owner_backend_pid, owner_backend_start,
                   last_seen_at
            FROM taskbound.active_sessions
            WHERE task_id = %s
            """,
            (task_id,),
        )
        rows = rows_as_dicts(cur)
        return rows[0] if rows else {}


def event_count(admin_conn, event_type: str) -> int:
    with admin_conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM taskbound.binding_events WHERE event_type = %s", (event_type,))
        return int(cur.fetchone()[0])


def run_lifecycle_tests(dsn: str, admin_url: str, credential: dict[str, Any], run_id: str) -> dict[str, Any]:
    records: dict[str, Any] = {}
    with psycopg.connect(admin_url, autocommit=True) as admin:
        task = issue_task(f"single_active_unbind_{run_id}", credential["credential_id"])
        with psycopg.connect(dsn, autocommit=True) as a, psycopg.connect(dsn, autocommit=True) as b:
            ca, cb = a.cursor(), b.cursor()
            first = bind_once(ca, task)
            denied = bind_once(cb, task)
            unbind_quietly(ca)
            second = bind_once(cb, task)
            records["explicit_unbind_recovery"] = first["ok"] and not denied["ok"] and second["ok"]
            unbind_quietly(cb)

        task = issue_task(f"single_active_socket_{run_id}", credential["credential_id"])
        a = psycopg.connect(dsn, autocommit=True)
        ca = a.cursor()
        first = bind_once(ca, task)
        a.close()
        time.sleep(0.2)
        with psycopg.connect(dsn, autocommit=True) as b:
            cb = b.cursor()
            second = bind_once(cb, task)
            records["socket_close_recovery"] = (
                first["ok"] and second["ok"] and second["bound"].get("stale_recovered") is True
            )
            unbind_quietly(cb)

        task = issue_task(f"single_active_terminate_{run_id}", credential["credential_id"], max_rows=5)
        a = psycopg.connect(dsn, autocommit=True)
        ca = a.cursor()
        ca.execute("SELECT pg_backend_pid()")
        victim_pid = ca.fetchone()[0]
        first = bind_once(ca, task)
        ca.execute("SELECT expense_id, amount FROM expenses ORDER BY expense_id LIMIT 2")
        ca.fetchall()
        before_state = fetch_state(ca)
        before_active = active_row(admin, task["payload"]["task_id"])
        with admin.cursor() as cur:
            cur.execute("SELECT pg_terminate_backend(%s)", (victim_pid,))
        time.sleep(0.5)
        with psycopg.connect(dsn, autocommit=True) as b:
            cb = b.cursor()
            recovered = bind_once(cb, task)
            after_state = fetch_state(cb)
            receipts = fetch_receipts(cb)
            after_active = active_row(admin, task["payload"]["task_id"])
            records["backend_terminate_recovery"] = recovered["ok"] and recovered["bound"].get("stale_recovered") is True
            records["remaining_budget_before_crash"] = before_state.get("unique_expense_rows")
            records["remaining_budget_after_recovery"] = after_state.get("unique_expense_rows")
            records["budget_continuity_after_recovery"] = before_state.get("unique_expense_rows") == after_state.get("unique_expense_rows")
            records["receipt_chain_verified"] = bool(receipts) and all(r["task_id"] == task["payload"]["task_id"] for r in receipts)
            records["new_binding_after_terminate"] = before_active.get("binding_id") != after_active.get("binding_id")
            unbind_quietly(cb)

        task = issue_task(f"single_active_idle_{run_id}", credential["credential_id"])
        with psycopg.connect(dsn, autocommit=True) as a, psycopg.connect(dsn, autocommit=True) as b:
            ca, cb = a.cursor(), b.cursor()
            bind_once(ca, task)
            with admin.cursor() as cur:
                cur.execute(
                    "UPDATE taskbound.active_sessions SET last_seen_at = now() - interval '2 hours' WHERE task_id = %s",
                    (task["payload"]["task_id"],),
                )
                cur.execute("SELECT taskbound.reap_stale_bindings()")
                reaped = cur.fetchone()[0]
            denied = bind_once(cb, task)
            denied_active = denied.get("sqlstate") == "55P03" or "ACTIVE_BINDING_EXISTS" in (denied.get("error") or "")
            records["live_owner_non_eviction"] = reaped == 0 and not denied["ok"] and denied_active
            records["live_owner_non_eviction_details"] = {"reaped": reaped, "denied": denied}
            unbind_quietly(ca)

        task = issue_task(f"single_active_pid_reuse_{run_id}", credential["credential_id"])
        a = psycopg.connect(dsn, autocommit=True)
        ca = a.cursor()
        bind_once(ca, task)
        a.close()
        time.sleep(0.2)
        with psycopg.connect(dsn, autocommit=True) as b:
            cb = b.cursor()
            cb.execute("SELECT pg_backend_pid()")
            pid_b = cb.fetchone()[0]
            with admin.cursor() as cur:
                cur.execute(
                    """
                    UPDATE taskbound.active_sessions
                    SET backend_pid = %s,
                        owner_backend_pid = %s,
                        owner_backend_start = owner_backend_start - interval '1 second'
                    WHERE task_id = %s
                    """,
                    (pid_b, pid_b, task["payload"]["task_id"]),
                )
            recovered = bind_once(cb, task)
            records["pid_reuse_protection"] = recovered["ok"] and recovered["bound"].get("stale_recovered") is True
            unbind_quietly(cb)

        task = issue_task(f"single_active_rollback_{run_id}", credential["credential_id"])
        a = psycopg.connect(dsn, autocommit=False)
        ca = a.cursor()
        ca.execute("BEGIN")
        first = bind_once(ca, task)
        ca.execute("ROLLBACK")
        a.autocommit = True
        with psycopg.connect(dsn, autocommit=True) as b:
            cb = b.cursor()
            denied = bind_once(cb, task)
            ca.execute("SELECT * FROM taskbound.run('SELECT expense_id FROM expenses ORDER BY expense_id LIMIT 1')")
            ca.fetchall()
            unbind_quietly(ca)
            takeover = bind_once(cb, task)
            records["rollback_semantics"] = first["ok"] and not denied["ok"] and takeover["ok"]
            unbind_quietly(cb)
        a.close()

        task = issue_task(f"single_active_fence_{run_id}", credential["credential_id"])
        a = psycopg.connect(dsn, autocommit=True)
        ca = a.cursor()
        bind_once(ca, task)
        old = active_row(admin, task["payload"]["task_id"])
        a.close()
        time.sleep(0.2)
        with psycopg.connect(dsn, autocommit=True) as b:
            cb = b.cursor()
            recovered = bind_once(cb, task)
            before_events = event_count(admin, "BINDING_FENCED")
            with admin.cursor() as cur:
                cur.execute(
                    "SELECT taskbound.release_active_binding_row(%s::uuid, %s, 'TEST_OLD_RELEASE')",
                    (old["binding_id"], old["fence_token"]),
                )
                old_release = cur.fetchone()[0]
                cur.execute(
                    "SELECT taskbound.mutation_fence_ok(%s, %s::uuid, %s)",
                    (task["payload"]["task_id"], old["binding_id"], old["fence_token"]),
                )
                old_budget = cur.fetchone()[0]
            after_events = event_count(admin, "BINDING_FENCED")
            still_active = active_row(admin, task["payload"]["task_id"])
            records["old_fence_mutation_rejected"] = (
                recovered["ok"]
                and old_release is False
                and old_budget is False
                and still_active.get("binding_id") == recovered["bound"].get("binding_id")
            )
            records["fence_rejection_count"] = after_events - before_events
            unbind_quietly(cb)

        task = issue_task(f"single_active_tamper_{run_id}", credential["credential_id"])
        with psycopg.connect(dsn, autocommit=True) as a:
            ca = a.cursor()
            bind_once(ca, task)
            tamper_results: dict[str, str] = {}
            for sql in [
                "SELECT pg_advisory_unlock_all()",
                "DISCARD ALL",
                "SELECT public.sessionbound_guard_clear_binding()",
                "DELETE FROM taskbound.active_sessions",
            ]:
                try:
                    ca.execute(sql)
                    tamper_results[sql] = "allowed"
                except Exception as exc:
                    tamper_results[sql] = getattr(exc, "sqlstate", "") or str(exc).split("\n")[0]
            ca.execute("SELECT expense_id FROM expenses ORDER BY expense_id LIMIT 1")
            ca.fetchall()
            records["lock_and_utility_bypass_blocked"] = all(value != "allowed" for value in tamper_results.values())
            records["tamper_results"] = tamper_results
            unbind_quietly(ca)

        task_a = issue_task(f"single_active_diff_a_{run_id}", credential["credential_id"])
        task_b = issue_task(f"single_active_diff_b_{run_id}", credential["credential_id"])
        with psycopg.connect(dsn, autocommit=True) as a, psycopg.connect(dsn, autocommit=True) as b:
            ca, cb = a.cursor(), b.cursor()
            res_a: dict[str, Any] = {}
            res_b: dict[str, Any] = {}
            barrier = threading.Barrier(2)

            def bind_a() -> None:
                barrier.wait()
                res_a.update(bind_once(ca, task_a))

            def bind_b() -> None:
                barrier.wait()
                res_b.update(bind_once(cb, task_b))

            ta = threading.Thread(target=bind_a)
            tb = threading.Thread(target=bind_b)
            ta.start(); tb.start(); ta.join(); tb.join()
            records["different_binding_keys_concurrent_success"] = res_a.get("ok") and res_b.get("ok")
            unbind_quietly(ca); unbind_quietly(cb)

    return records


def percentile(values: list[float], p: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, int(round((p / 100) * (len(ordered) - 1)))))
    return ordered[index]


def postgres_version(admin_url: str) -> str:
    with psycopg.connect(admin_url, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute("SHOW server_version")
            return cur.fetchone()[0]


def extension_version(admin_url: str) -> str:
    with psycopg.connect(admin_url, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT installed_version FROM pg_available_extensions WHERE name = 'sessionbound_guard'")
            row = cur.fetchone()
            return row[0] if row and row[0] else "unknown"


def run_eval(host: str, port: str, dbname: str, rounds: int, contenders: int) -> dict[str, Any]:
    wait_for_api()
    run_id = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    credential = issue_credential(run_id)
    dsn = credential_dsn(credential, host, port, dbname)
    admin_url = admin_dsn(host, port, dbname)
    race_task = issue_task(f"single_active_race_{run_id}", credential["credential_id"])

    two_backend = race_once(dsn, race_task, 2)
    high_contention = race_once(dsn, race_task, contenders)
    repeated = repeated_races(dsn, race_task, rounds)
    lifecycle = run_lifecycle_tests(dsn, admin_url, credential, run_id)
    all_latencies = two_backend["latencies_ms"] + high_contention["latencies_ms"] + repeated["latencies_ms"]

    summary = {
        "timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "git_commit": git_commit(),
        "postgres_version": postgres_version(admin_url),
        "extension_version": extension_version(admin_url),
        "round_count": rounds,
        "contender_count": contenders,
        "success_count": high_contention["success_count"],
        "denial_count": high_contention["denied_count"],
        "dual_success_count": repeated["dual_success_rounds"],
        "zero_success_count": repeated["zero_winner_rounds"],
        "unexpected_error_count": (
            two_backend["unexpected_error_count"]
            + high_contention["unexpected_error_count"]
            + repeated["unexpected_error_count"]
        ),
        "explicit_unbind_recovery": lifecycle["explicit_unbind_recovery"],
        "socket_close_recovery": lifecycle["socket_close_recovery"],
        "backend_terminate_recovery": lifecycle["backend_terminate_recovery"],
        "rollback_semantics": lifecycle["rollback_semantics"],
        "stale_recovery_count": sum(
            1
            for key in ["socket_close_recovery", "backend_terminate_recovery", "pid_reuse_protection"]
            if lifecycle.get(key)
        ),
        "fence_rejection_count": lifecycle["fence_rejection_count"],
        "remaining_budget_before_crash": lifecycle["remaining_budget_before_crash"],
        "remaining_budget_after_recovery": lifecycle["remaining_budget_after_recovery"],
        "receipt_chain_verified": lifecycle["receipt_chain_verified"],
        "latency_p50_ms": percentile(all_latencies, 50),
        "latency_p95_ms": percentile(all_latencies, 95),
        "latency_p99_ms": percentile(all_latencies, 99),
        "passed": (
            two_backend["success_count"] == 1
            and high_contention["success_count"] == 1
            and repeated["dual_success_rounds"] == 0
            and repeated["zero_winner_rounds"] == 0
            and repeated["unexpected_error_count"] == 0
            and all(
                bool(lifecycle.get(key))
                for key in [
                    "explicit_unbind_recovery",
                    "socket_close_recovery",
                    "backend_terminate_recovery",
                    "budget_continuity_after_recovery",
                    "receipt_chain_verified",
                    "live_owner_non_eviction",
                    "pid_reuse_protection",
                    "rollback_semantics",
                    "old_fence_mutation_rejected",
                    "lock_and_utility_bypass_blocked",
                    "different_binding_keys_concurrent_success",
                ]
            )
        ),
    }

    return {
        "run": summary,
        "two_backend_same_key": two_backend,
        "high_contention_same_key": high_contention,
        "repeated_races": repeated,
        "lifecycle": lifecycle,
    }


def write_csv(path: Path, result: dict[str, Any]) -> None:
    row = result["run"]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(row.keys()))
        writer.writeheader()
        writer.writerow(row)


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate global single-active SessionBound bindings")
    parser.add_argument("--db-host", default=os.environ.get("TDSC_DB_HOST", "localhost"))
    parser.add_argument("--db-port", default=os.environ.get("TDSC_DB_PORT", "15432"))
    parser.add_argument("--db-name", default=os.environ.get("TDSC_DB_NAME", "travel"))
    parser.add_argument("--rounds", type=int, default=100)
    parser.add_argument("--full", action="store_true", help="run the paper-mode 1000 repeated races")
    parser.add_argument("--contenders", type=int, default=20)
    parser.add_argument("--output-dir", default=str(REPO_ROOT / "paper/tdsc/raw_results"))
    args = parser.parse_args()
    rounds = 1000 if args.full else args.rounds
    result = run_eval(args.db_host, args.db_port, args.db_name, rounds, args.contenders)

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    json_path = output_dir / f"single_active_binding_{stamp}.json"
    csv_path = output_dir / f"single_active_binding_{stamp}.csv"
    json_path.write_text(json.dumps(result, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
    write_csv(csv_path, result)
    print(json_path)
    print(csv_path)
    print(json.dumps(result["run"], ensure_ascii=False, indent=2, default=str))
    return 0 if result["run"]["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
