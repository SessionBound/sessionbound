from __future__ import annotations

import json
from typing import Any, Callable, TypeVar

import psycopg


T = TypeVar("T")


def rows_as_dicts(cur) -> list[dict[str, Any]]:
    if cur.description is None:
        return []
    names = [d.name for d in cur.description]
    return [dict(zip(names, row)) for row in cur.fetchall()]


class TaskboundSession:
    """Native-feeling agent SDK backed by the SessionBound accounting entrypoint."""

    def __init__(self, conn=None, cur=None, *, owns_connection: bool = False):
        if conn is None and cur is None:
            raise ValueError("TaskboundSession requires a psycopg connection or cursor")
        self.conn = conn
        self.cur = cur
        self.owns_connection = owns_connection

    @classmethod
    def connect(cls, conninfo: str, *, autocommit: bool = True) -> "TaskboundSession":
        conn = psycopg.connect(conninfo)
        conn.autocommit = autocommit
        return cls(conn=conn, owns_connection=True)

    def close(self) -> None:
        try:
            self.unbind_task()
        except Exception:
            pass
        if self.owns_connection and self.conn is not None:
            self.conn.close()

    def __enter__(self) -> "TaskboundSession":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def _with_cursor(self, fn: Callable[[Any], T]) -> T:
        if self.cur is not None:
            return fn(self.cur)
        if self.conn is None:
            raise ValueError("TaskboundSession has no connection")
        with self.conn.cursor() as cur:
            return fn(cur)

    def bind_task(self, payload_text: str, signature: str) -> dict[str, Any]:
        def run(cur):
            cur.execute("SELECT taskbound.bind_task(%s, %s)", (payload_text, signature))
            return cur.fetchone()[0]

        return self._with_cursor(run)

    def unbind_task(self) -> None:
        def run(cur):
            cur.execute("SELECT taskbound.unbind_task()")
            return None

        self._with_cursor(run)

    def query(self, sql_text: str) -> list[dict[str, Any]]:
        """Run ordinary safe-view SQL natively under the SessionBound guard."""

        def run(cur):
            cur.execute(sql_text)
            return rows_as_dicts(cur)

        return self._with_cursor(run)

    def query_via_runtime(self, sql_text: str) -> list[dict[str, Any]]:
        """Compatibility path for the historical taskbound.run(sql) wrapper."""

        def run(cur):
            cur.execute("SELECT * FROM taskbound.run(%s)", (sql_text,))
            return [row[0] for row in cur.fetchall()]

        return self._with_cursor(run)

    def command(self, command_name: str, args: dict[str, Any]) -> dict[str, Any]:
        def run(cur):
            cur.execute("SELECT taskbound.command(%s, %s::jsonb)", (command_name, json.dumps(args)))
            return cur.fetchone()[0]

        return self._with_cursor(run)

    def inspect_state(self) -> list[dict[str, Any]]:
        def run(cur):
            cur.execute("SELECT * FROM taskbound.inspect_task_state()")
            return rows_as_dicts(cur)

        return self._with_cursor(run)

    def receipts(self, *, limit: int = 20) -> list[dict[str, Any]]:
        def run(cur):
            cur.execute(
                """
                SELECT decision, rows_returned, unique_rows_added,
                       remaining_unique_row_budget, reason, created_at
                FROM taskbound.receipts()
                LIMIT %s
                """,
                (limit,),
            )
            return rows_as_dicts(cur)

        return self._with_cursor(run)
