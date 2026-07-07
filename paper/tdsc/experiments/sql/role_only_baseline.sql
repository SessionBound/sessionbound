DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'tdsc_role_only') THEN
    CREATE ROLE tdsc_role_only LOGIN PASSWORD 'tdsc_role_only_pass';
  END IF;
END $$;

GRANT USAGE ON SCHEMA app_data TO tdsc_role_only;
GRANT SELECT ON ALL TABLES IN SCHEMA app_data TO tdsc_role_only;

-- Ensure this baseline is read-only over application data.
REVOKE INSERT, UPDATE, DELETE, TRUNCATE, REFERENCES, TRIGGER
ON ALL TABLES IN SCHEMA app_data FROM tdsc_role_only;

