\echo Use "CREATE EXTENSION sessionbound_guard" to load this file. \quit

CREATE FUNCTION sessionbound_guard_status()
RETURNS text
AS 'MODULE_PATHNAME', 'sessionbound_guard_status'
LANGUAGE C STRICT;

CREATE FUNCTION sessionbound_guard_check(sql_text text)
RETURNS void
AS 'MODULE_PATHNAME', 'sessionbound_guard_check'
LANGUAGE C STRICT;
