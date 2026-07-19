CREATE EXTENSION IF NOT EXISTS pgcrypto;
CREATE EXTENSION IF NOT EXISTS dblink;
CREATE EXTENSION IF NOT EXISTS sessionbound_guard;

CREATE ROLE agent_runtime NOLOGIN;
CREATE ROLE agent_app LOGIN PASSWORD 'agentpass';
GRANT agent_runtime TO agent_app WITH INHERIT TRUE, SET FALSE;

CREATE SCHEMA app_data;
CREATE SCHEMA taskbound;

CREATE TABLE app_data.departments (
  department_id text PRIMARY KEY,
  tenant_id text NOT NULL,
  department_name text NOT NULL,
  manager_user_id text NOT NULL
);

CREATE TABLE app_data.employees (
  employee_id text PRIMARY KEY,
  tenant_id text NOT NULL,
  department_id text NOT NULL REFERENCES app_data.departments(department_id),
  employee_name text NOT NULL,
  employee_level text NOT NULL,
  phone text NOT NULL,
  bank_account text NOT NULL,
  salary numeric(12,2) NOT NULL
);

CREATE TABLE app_data.expenses (
  expense_id text PRIMARY KEY,
  tenant_id text NOT NULL,
  employee_id text NOT NULL REFERENCES app_data.employees(employee_id),
  department_id text NOT NULL REFERENCES app_data.departments(department_id),
  expense_month text NOT NULL,
  category text NOT NULL,
  merchant text NOT NULL,
  city text NOT NULL,
  amount numeric(12,2) NOT NULL,
  submitted_at timestamptz NOT NULL,
  status text NOT NULL
);

CREATE TABLE app_data.approval_events (
  event_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id text NOT NULL,
  expense_id text NOT NULL REFERENCES app_data.expenses(expense_id),
  event_type text NOT NULL,
  actor text NOT NULL,
  agent_actor text NOT NULL,
  supervisor_agent_id text,
  comment text,
  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE app_data.ledger_entries (
  ledger_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id text NOT NULL,
  expense_id text NOT NULL REFERENCES app_data.expenses(expense_id),
  debit_account text NOT NULL,
  credit_account text NOT NULL,
  amount numeric(12,2) NOT NULL,
  memo text NOT NULL,
  created_by text NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE taskbound.signing_keys (
  key_id text PRIMARY KEY,
  secret text NOT NULL
);

CREATE SEQUENCE taskbound.binding_fence_token_seq AS bigint;

CREATE TABLE taskbound.active_sessions (
  backend_pid int PRIMARY KEY,
  backend_start timestamptz NOT NULL,
  task_id text NOT NULL,
  token_digest text NOT NULL,
  token_nonce text NOT NULL DEFAULT '',
  credential_id text NOT NULL DEFAULT '',
  binding_id uuid NOT NULL DEFAULT gen_random_uuid(),
  fence_token bigint NOT NULL,
  advisory_lock_key bigint NOT NULL,
  database_oid oid NOT NULL,
  lock_backend_pid int NOT NULL,
  owner_backend_pid int NOT NULL,
  owner_backend_start timestamptz NOT NULL,
  owner_postmaster_start timestamptz NOT NULL,
  owner_session_user name NOT NULL,
  payload jsonb NOT NULL,
  bound_at timestamptz NOT NULL DEFAULT now(),
  acquired_at timestamptz NOT NULL DEFAULT now(),
  last_seen_at timestamptz NOT NULL DEFAULT now(),
  token_expires_at timestamptz NOT NULL,
  UNIQUE (task_id, token_digest, credential_id),
  UNIQUE (binding_id)
);

CREATE TABLE taskbound.binding_events (
  event_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  event_type text NOT NULL,
  task_id text,
  token_digest text,
  credential_id text,
  binding_id uuid,
  fence_token bigint,
  advisory_lock_key bigint,
  owner_backend_pid int,
  reason text,
  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE taskbound.credential_ledger (
  credential_id text PRIMARY KEY,
  db_user name UNIQUE NOT NULL,
  actor text NOT NULL,
  audience text NOT NULL DEFAULT 'sessionbounddb',
  issued_at timestamptz NOT NULL DEFAULT now(),
  expires_at timestamptz NOT NULL,
  revoked boolean NOT NULL DEFAULT false
);

CREATE TABLE taskbound.task_credential_bindings (
  task_id text PRIMARY KEY,
  credential_id text NOT NULL REFERENCES taskbound.credential_ledger(credential_id),
  token_digest text NOT NULL,
  first_bound_at timestamptz NOT NULL DEFAULT now(),
  first_backend_pid int NOT NULL,
  session_user_name name NOT NULL
);

CREATE TABLE taskbound.task_execution_state (
  task_id text PRIMARY KEY,
  tenant_id text NOT NULL,
  delegator text NOT NULL,
  actor text NOT NULL,
  purpose text NOT NULL,
  budget_account text NOT NULL,
  query_count int NOT NULL DEFAULT 0,
  returned_rows bigint NOT NULL DEFAULT 0,
  unique_expense_rows bigint NOT NULL DEFAULT 0,
  revoked boolean NOT NULL DEFAULT false,
  started_at timestamptz NOT NULL DEFAULT now(),
  expires_at timestamptz NOT NULL
);

COMMENT ON COLUMN taskbound.task_execution_state.returned_rows IS
  'Cumulative count of rows authorized by the release barrier for this task. This is counted before client forwarding and is not proof of network delivery.';

CREATE TABLE taskbound.task_rows_seen (
  budget_account text NOT NULL,
  row_kind text NOT NULL,
  row_id text NOT NULL,
  PRIMARY KEY (budget_account, row_kind, row_id)
);

CREATE TABLE taskbound.task_query_receipts (
  receipt_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  execution_id uuid NOT NULL DEFAULT gen_random_uuid(),
  receipt_sequence bigint NOT NULL DEFAULT 0,
  task_id text NOT NULL,
  budget_account text NOT NULL,
  binding_id uuid,
  fence_token bigint,
  query_digest text NOT NULL,
  decision text NOT NULL,
  rows_returned bigint NOT NULL DEFAULT 0,
  unique_rows_added bigint NOT NULL DEFAULT 0,
  remaining_unique_row_budget bigint,
  reason text,
  actor text,
  touched_views text[] NOT NULL DEFAULT ARRAY[]::text[],
  previous_receipt_hash text,
  receipt_hash text,
  created_at timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT task_query_receipts_task_execution_id_key UNIQUE (task_id, execution_id),
  CONSTRAINT task_query_receipts_task_sequence_key UNIQUE (task_id, receipt_sequence)
);

COMMENT ON COLUMN taskbound.task_query_receipts.rows_returned IS
  'Legacy receipt field for release-barrier-authorized rows. For wrapper/native reads, rows are staged and counted before client forwarding, so this is an authorization count, not proof of network delivery.';

CREATE TABLE taskbound.task_receipt_chain_heads (
  task_id text PRIMARY KEY,
  last_sequence bigint NOT NULL DEFAULT 0,
  last_receipt_hash text NOT NULL DEFAULT '',
  updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE FUNCTION taskbound.advance_receipt_chain_head()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = taskbound, pg_temp
AS $$
BEGIN
  INSERT INTO taskbound.task_receipt_chain_heads (
    task_id, last_sequence, last_receipt_hash, updated_at
  )
  VALUES (
    NEW.task_id, NEW.receipt_sequence, COALESCE(NEW.receipt_hash, ''), clock_timestamp()
  )
  ON CONFLICT (task_id) DO UPDATE
    SET last_sequence = EXCLUDED.last_sequence,
        last_receipt_hash = EXCLUDED.last_receipt_hash,
        updated_at = EXCLUDED.updated_at
    WHERE taskbound.task_receipt_chain_heads.last_sequence < EXCLUDED.last_sequence;

  RETURN NEW;
END;
$$;

CREATE TRIGGER task_query_receipts_chain_head_ai
AFTER INSERT ON taskbound.task_query_receipts
FOR EACH ROW
EXECUTE FUNCTION taskbound.advance_receipt_chain_head();

CREATE TABLE taskbound.safe_view_registry (
  view_name text PRIMARY KEY,
  database_object text NOT NULL,
  business_object text NOT NULL,
  maintainer text NOT NULL,
  allowed_tasks text[] NOT NULL,
  scope_fields text[] NOT NULL,
  workflow_fields text[] NOT NULL,
  sensitive_fields_excluded text[] NOT NULL,
  recommended_commands text[] NOT NULL,
  registry_version int NOT NULL DEFAULT 1,
  policy_version text NOT NULL DEFAULT 'travel-demo-v1',
  description text NOT NULL
);
