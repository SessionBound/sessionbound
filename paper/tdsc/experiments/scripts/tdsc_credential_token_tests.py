#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import hmac
import json
import os
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import psycopg


BASE_URL = os.environ.get("TDSC_BASE_URL", "http://127.0.0.1:8000")
OUT_DIR = Path(os.environ.get("TDSC_OUT_DIR", "paper/tdsc/experiments/raw_results"))
DB_HOST = os.environ.get("TDSC_DB_HOST", "postgres")
DB_PORT = os.environ.get("TDSC_DB_PORT", "5432")
DB_NAME = os.environ.get("TDSC_DB_NAME", "travel")
ADMIN_DSN = f"postgresql://postgres:postgres@{DB_HOST}:{DB_PORT}/{DB_NAME}"
SECRET = os.environ.get("TASKBOUND_SECRET", "dev-secret-change-me").encode("utf-8")


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


def sign(payload_text: str) -> str:
    return hmac.new(SECRET, payload_text.encode("utf-8"), hashlib.sha256).hexdigest()


def canonical(payload: dict[str, Any]) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def issue_credential(agent_id: str, actor: str = "agent:travel-expense-analyst") -> dict[str, Any]:
    return post_json("/credentials", {"agent_id": agent_id, "actor": actor, "ttl_minutes": 15})


def issue_task(task_id: str, credential_id: str) -> dict[str, Any]:
    return post_json(
        "/tasks",
        {
            "task_id": task_id,
            "task_type": "monthly_travel_expense_review",
            "delegator": "user:alice",
            "actor": "agent:travel-expense-analyst",
            "credential_id": credential_id,
            "department_id": "dep_sales",
            "scope": {"expense_month": "2026-06", "department_id": "dep_sales"},
            "max_rows": 5000,
            "max_queries": 20,
        },
    )


def task_from_payload(payload: dict[str, Any]) -> dict[str, Any]:
    payload_text = canonical(payload)
    return {
        "payload": payload,
        "payload_text": payload_text,
        "signature": sign(payload_text),
    }


def query(credential: dict[str, Any], task: dict[str, Any], sql: str = "SELECT count(*) AS n FROM expenses") -> dict[str, Any]:
    return post_json(
        "/agent-query",
        {
            "credential": credential,
            "payload_text": task["payload_text"],
            "signature": task["signature"],
            "sql": sql,
        },
    )


def revoke_task(task_id: str) -> None:
    with psycopg.connect(ADMIN_DSN) as conn:
        with conn.cursor() as cur:
            cur.execute("UPDATE taskbound.task_execution_state SET revoked = true WHERE task_id = %s", (task_id,))
        conn.commit()


def bump_safe_view_registry(view_name: str = "expenses") -> int:
    with psycopg.connect(ADMIN_DSN) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE taskbound.safe_view_registry
                SET registry_version = registry_version + 1
                WHERE view_name = %s
                RETURNING registry_version
                """,
                (view_name,),
            )
            new_version = cur.fetchone()[0]
        conn.commit()
    return new_version


def restore_safe_view_registry(version: int, view_name: str = "expenses") -> None:
    with psycopg.connect(ADMIN_DSN) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE taskbound.safe_view_registry SET registry_version = %s WHERE view_name = %s",
                (version, view_name),
            )
        conn.commit()


def replace_safe_view_policy_version(policy_version: str, view_name: str = "expenses") -> str:
    with psycopg.connect(ADMIN_DSN) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE taskbound.safe_view_registry
                SET policy_version = %s
                WHERE view_name = %s
                RETURNING policy_version
                """,
                (policy_version, view_name),
            )
            new_version = cur.fetchone()[0]
        conn.commit()
    return new_version


def main() -> None:
    run_id = str(int(time.time()))
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    tests: list[dict[str, Any]] = []

    cred_a = issue_credential(f"tdsc-cred-a-{run_id}")
    cred_b = issue_credential(f"tdsc-cred-b-{run_id}")
    task_a = issue_task(f"tdsc_cred_task_a_{run_id}", cred_a["credential_id"])
    task_b = issue_task(f"tdsc_cred_task_b_{run_id}", cred_b["credential_id"])

    mismatch = query(cred_a, task_b)
    tests.append({
        "name": "credential_A_with_token_B_mismatch",
        "expected_security_property": "Denied because the signed token credential_id is bound to credential B.",
        "observed": "Allowed" if mismatch.get("ok") else "Denied",
        "result": mismatch,
    })

    replay_first = query(cred_a, task_a)
    replay_second = query(cred_b, task_a)
    tests.append({
        "name": "token_replay_with_second_credential",
        "expected_security_property": "Denied because token A cannot be replayed with credential B.",
        "observed": "Allowed" if replay_second.get("ok") else "Denied",
        "first_result_ok": replay_first.get("ok"),
        "result": replay_second,
    })

    expired_payload = dict(task_a["payload"])
    expired_payload["task_id"] = f"tdsc_expired_{run_id}"
    expired_payload["expires_at"] = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
    expired_text = canonical(expired_payload)
    expired_task = {
        "payload_text": expired_text,
        "signature": sign(expired_text),
    }
    expired = query(cred_a, expired_task)
    tests.append({
        "name": "expired_token",
        "expected_security_property": "Denied.",
        "observed": "Allowed" if expired.get("ok") else "Denied",
        "result": expired,
    })

    wrong_audience_payload = dict(task_a["payload"])
    wrong_audience_payload["task_id"] = f"tdsc_wrong_audience_{run_id}"
    wrong_audience_payload["audience"] = "not-sessionbounddb"
    wrong_audience = query(cred_a, task_from_payload(wrong_audience_payload))
    tests.append({
        "name": "wrong_audience",
        "expected_security_property": "Denied if token audience is validated by the runtime.",
        "observed": "Allowed" if wrong_audience.get("ok") else "Denied",
        "result": wrong_audience,
    })

    wrong_actor_payload = dict(task_a["payload"])
    wrong_actor_payload["task_id"] = f"tdsc_wrong_actor_{run_id}"
    wrong_actor_payload["actor"] = "agent:wrong-actor"
    wrong_actor = query(cred_a, task_from_payload(wrong_actor_payload))
    tests.append({
        "name": "wrong_actor",
        "expected_security_property": "Denied if token actor is bound to the credential actor/runtime principal.",
        "observed": "Allowed" if wrong_actor.get("ok") else "Denied",
        "result": wrong_actor,
    })

    drift_task = issue_task(f"tdsc_schema_drift_{run_id}", cred_a["credential_id"])
    old_registry_version = drift_task["payload"]["safe_view_registry"]["views"]["expenses"]["registry_version"]
    new_registry_version = bump_safe_view_registry("expenses")
    try:
        drift_result = query(cred_a, drift_task)
    finally:
        restore_safe_view_registry(old_registry_version, "expenses")
    tests.append({
        "name": "safe_view_registry_version_drift",
        "expected_security_property": "Denied when safe_view_registry_version/view_definition_hash/policy snapshot no longer matches runtime registry.",
        "observed": "Allowed" if drift_result.get("ok") else "Denied",
        "old_registry_version": old_registry_version,
        "new_registry_version": new_registry_version,
        "result": drift_result,
    })

    policy_task = issue_task(f"tdsc_policy_drift_{run_id}", cred_a["credential_id"])
    old_policy_version = policy_task["payload"]["safe_view_registry"]["views"]["expenses"]["policy_version"]
    new_policy_version = replace_safe_view_policy_version(f"{old_policy_version}-drift", "expenses")
    try:
        policy_result = query(cred_a, policy_task)
    finally:
        replace_safe_view_policy_version(old_policy_version, "expenses")
    tests.append({
        "name": "safe_view_policy_version_drift",
        "expected_security_property": "Denied when the registered safe-view policy_version changes after token approval.",
        "observed": "Allowed" if policy_result.get("ok") else "Denied",
        "old_policy_version": old_policy_version,
        "new_policy_version": new_policy_version,
        "result": policy_result,
    })

    hash_payload = dict(task_a["payload"])
    hash_payload["task_id"] = f"tdsc_hash_mismatch_{run_id}"
    hash_payload["view_definition_hash"] = "0" * 64
    hash_mismatch = query(cred_a, task_from_payload(hash_payload))
    tests.append({
        "name": "view_definition_hash_claim_mismatch",
        "expected_security_property": "Denied when the signed token's view_definition_hash does not match the runtime safe-view snapshot.",
        "observed": "Allowed" if hash_mismatch.get("ok") else "Denied",
        "result": hash_mismatch,
    })

    column_hash_payload = dict(task_a["payload"])
    column_hash_payload["task_id"] = f"tdsc_column_hash_mismatch_{run_id}"
    column_hash_payload["exposed_column_hash"] = "0" * 64
    column_hash_mismatch = query(cred_a, task_from_payload(column_hash_payload))
    tests.append({
        "name": "exposed_column_hash_claim_mismatch",
        "expected_security_property": "Denied when the signed token's exposed_column_hash does not match the runtime safe-view snapshot.",
        "observed": "Allowed" if column_hash_mismatch.get("ok") else "Denied",
        "result": column_hash_mismatch,
    })

    revoke_task(task_a["payload"]["task_id"])
    revoked = query(cred_a, task_a)
    tests.append({
        "name": "revoked_task_state",
        "expected_security_property": "Denied after task_execution_state.revoked=true.",
        "observed": "Allowed" if revoked.get("ok") else "Denied",
        "result": revoked,
    })

    # Direct same-session rebind test: bind two credential-valid tasks over one backend session.
    task_c = issue_task(f"tdsc_cred_task_c_{run_id}", cred_a["credential_id"])
    dsn = f"postgresql://{cred_a['db_user']}:{cred_a['db_password']}@{DB_HOST}:{DB_PORT}/{DB_NAME}"
    with psycopg.connect(dsn) as conn:
        conn.autocommit = True
        with conn.cursor() as cur:
            direct: dict[str, Any] = {}
            try:
                cur.execute("SELECT taskbound.bind_task(%s, %s)", (task_a["payload_text"], task_a["signature"]))
                direct["first_bind"] = cur.fetchone()[0]
                cur.execute("SELECT taskbound.bind_task(%s, %s)", (task_c["payload_text"], task_c["signature"]))
                direct["second_bind"] = cur.fetchone()[0]
                direct["ok"] = True
            except Exception as exc:
                direct["ok"] = False
                direct["error"] = str(exc).splitlines()[0]
    tests.append({
        "name": "second_active_task_binding_same_session",
        "expected_security_property": "Denied if one session cannot be rebound to another active task.",
        "observed": "Allowed" if direct.get("ok") else "Denied",
        "result": direct,
    })

    output = {"run_id": run_id, "tests": tests}
    path = OUT_DIR / f"credential_token_{run_id}.json"
    path.write_text(json.dumps(output, indent=2, default=str), encoding="utf-8")
    print(path)


if __name__ == "__main__":
    main()
