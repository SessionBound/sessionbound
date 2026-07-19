\echo Use "CREATE EXTENSION sessionbound_guard" to load this file. \quit

CREATE FUNCTION sessionbound_guard_status()
RETURNS text
AS 'MODULE_PATHNAME', 'sessionbound_guard_status'
LANGUAGE C STRICT;

CREATE FUNCTION sessionbound_guard_check(sql_text text)
RETURNS void
AS 'MODULE_PATHNAME', 'sessionbound_guard_check'
LANGUAGE C STRICT;

CREATE FUNCTION sessionbound_guard_touched_view_oids(sql_text text)
RETURNS oid[]
AS 'MODULE_PATHNAME', 'sessionbound_guard_touched_view_oids'
LANGUAGE C STRICT;

CREATE FUNCTION sessionbound_guard_install_binding(
  task_id text,
  budget_account text,
  allowed_view_oids text,
  denied_columns text,
  max_queries int,
  max_unique_expense_rows int,
  min_group_size int,
  receipts_enabled boolean,
  budget_accounting_enabled boolean,
  binding_id text,
  fence_token bigint,
  advisory_lock_key bigint,
  token_digest text,
  credential_id text,
  token_expires_at timestamptz
)
RETURNS void
AS 'MODULE_PATHNAME', 'sessionbound_guard_install_binding'
LANGUAGE C STRICT;

CREATE FUNCTION sessionbound_guard_clear_binding()
RETURNS void
AS 'MODULE_PATHNAME', 'sessionbound_guard_clear_binding'
LANGUAGE C STRICT;
