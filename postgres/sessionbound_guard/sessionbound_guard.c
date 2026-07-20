#include "postgres.h"

#include "access/attnum.h"
#include "access/htup_details.h"
#include "access/sysattr.h"
#include "access/xact.h"
#include "catalog/namespace.h"
#include "catalog/pg_class_d.h"
#include "catalog/pg_namespace.h"
#include "catalog/pg_proc.h"
#include "catalog/pg_type_d.h"
#include "commands/prepare.h"
#include "commands/defrem.h"
#include "executor/executor.h"
#include "executor/spi.h"
#include "executor/tuptable.h"
#include "fmgr.h"
#include "miscadmin.h"
#include "nodes/bitmapset.h"
#include "nodes/nodeFuncs.h"
#include "nodes/nodes.h"
#include "nodes/parsenodes.h"
#include "nodes/plannodes.h"
#include "nodes/primnodes.h"
#include "parser/analyze.h"
#include "parser/parsetree.h"
#include "tcop/dest.h"
#include "tcop/utility.h"
#include "utils/array.h"
#include "utils/acl.h"
#include "utils/builtins.h"
#include "utils/guc.h"
#include "utils/lsyscache.h"
#include "utils/memutils.h"
#include "utils/snapmgr.h"
#include "utils/syscache.h"
#include "utils/tuplestore.h"

#include <limits.h>
#include <stdlib.h>
#include <string.h>

PG_MODULE_MAGIC;

void _PG_init(void);
void _PG_fini(void);

PG_FUNCTION_INFO_V1(sessionbound_guard_status);
PG_FUNCTION_INFO_V1(sessionbound_guard_check);
PG_FUNCTION_INFO_V1(sessionbound_guard_touched_view_oids);
PG_FUNCTION_INFO_V1(sessionbound_guard_install_binding);
PG_FUNCTION_INFO_V1(sessionbound_guard_clear_binding);

typedef struct GuardContext
{
	List *allowed_view_oids;
	int allowed_relation_refs;
	bool saw_relation;
	bool saw_public_runtime_entrypoint;
	bool saw_non_public_runtime_function;
	bool defer_safe_view_requirement;
	bool explicit_check;
	int public_runtime_entrypoint_calls;
	Query *current_query;
} GuardContext;

typedef struct RelationScanContext
{
	bool saw_relation;
	bool saw_guarded_relation;
	bool saw_private_taskbound_function;
} RelationScanContext;

typedef struct TouchedViewContext
{
	List *allowed_view_oids;
} TouchedViewContext;

typedef struct PlannedRuntimeContext
{
	bool saw_public_runtime_entrypoint;
	bool saw_non_public_runtime_function;
} PlannedRuntimeContext;

typedef struct NativeQueryState NativeQueryState;

typedef struct GuardDestReceiver
{
	DestReceiver pub;
	DestReceiver *original;
	NativeQueryState *state;
} GuardDestReceiver;

struct NativeQueryState
{
	QueryDesc *query_desc;
	MemoryContext context;
	GuardDestReceiver receiver;
	uint64 rows_authorized;
	uint64 unique_seen_count;
	bool budget_accounting_enabled;
	bool receipts_enabled;
	bool aborted;
	bool denied_recorded;
	bool finished;
	bool reserved;
	/*
	 * Native SELECT results are staged here.  This is deliberately a
	 * release barrier: no tuple reaches the client until the whole result is
	 * known to fit the task's result-tuple budget and durable accounting has
	 * succeeded.  It makes the native path match taskbound.run's atomic
	 * wrapper semantics and prevents a caller from harvesting a prefix of an
	 * over-budget result through an error.
	 */
	Tuplestorestate *result_store;
	TupleDesc result_desc;
	int result_operation;
	bool original_started;
	int max_unique_expense_rows;
	char *task_id;
	char *budget_account;
	char *binding_id;
	int64 fence_token;
	int64 advisory_lock_key;
	char *source_text;
	NativeQueryState *next;
};

static post_parse_analyze_hook_type prev_post_parse_analyze_hook = NULL;
static ProcessUtility_hook_type prev_ProcessUtility_hook = NULL;
static ExecutorStart_hook_type prev_ExecutorStart_hook = NULL;
static ExecutorRun_hook_type prev_ExecutorRun_hook = NULL;
static ExecutorFinish_hook_type prev_ExecutorFinish_hook = NULL;
static ExecutorEnd_hook_type prev_ExecutorEnd_hook = NULL;

static bool guard_enabled = false;
static bool guard_task_bound = false;
static bool guard_receipts_enabled = true;
static bool guard_budget_accounting_enabled = true;
static int guard_max_queries = 0;
static int guard_max_unique_expense_rows = 0;
static int guard_min_group_size = 5;
static char *guard_task_id = NULL;
static char *guard_budget_account = NULL;
static char *guard_allowed_view_oids = NULL;
static char *guard_denied_columns = NULL;
static char *guard_binding_id = NULL;
static char *guard_token_digest = NULL;
static char *guard_credential_id = NULL;
static int64 guard_fence_token = 0;
static int64 guard_advisory_lock_key = 0;
static Oid guard_bound_session_user_oid = InvalidOid;
static bool guard_in_internal_spi = false;
/* taskbound.run() records the caught error once; avoid one receipt per nested
 * SPI parse node while its explicit guard check is running. */
static bool guard_suppress_receipts = false;
static bool guard_collect_touched_oids = false;
static bool guard_error_receipt_recorded = false;
static List *guard_touched_view_oids = NIL;
static const char *guard_current_sql = NULL;
static NativeQueryState *native_states = NULL;

/* Trusted binding state is installed by taskbound.bind_task without transactional SET. */
static void sessionbound_guard_post_parse_analyze(ParseState *pstate, Query *query,
												 JumbleState *jstate);
static void sessionbound_guard_ProcessUtility(PlannedStmt *pstmt,
											  const char *queryString,
											  bool readOnlyTree,
											  ProcessUtilityContext context,
											  ParamListInfo params,
											  QueryEnvironment *queryEnv,
											  DestReceiver *dest,
											  QueryCompletion *qc);
static void sessionbound_guard_ExecutorStart(QueryDesc *queryDesc, int eflags);
static void sessionbound_guard_ExecutorRun(QueryDesc *queryDesc,
										   ScanDirection direction,
										   uint64 count,
										   bool execute_once);
static void sessionbound_guard_ExecutorFinish(QueryDesc *queryDesc);
static void sessionbound_guard_ExecutorEnd(QueryDesc *queryDesc);
static void guard_check_query(Query *query, GuardContext *ctx);
static void guard_check_group_policy(Query *query);
static void guard_deny(const char *detail);
static void record_denied_receipt(const char *sql_text, const char *reason);
static bool guard_expr_walker(Node *node, void *context);
static void guard_check_pg_catalog_procedure(Oid funcid, const char *kind);
static bool query_is_standalone_runtime_entrypoint(Query *query);
static void guard_check_prepared_statement(ExecuteStmt *stmt, GuardContext *ctx);
static bool relation_oid_is_taskbound_view(Oid relid);
static bool relation_scan_walker(Node *node, void *context);
static void collect_touched_views_from_query(Query *query, TouchedViewContext *ctx);
static bool touched_view_walker(Node *node, void *context);
static ArrayType *oid_list_to_array(List *oids);
static bool planned_runtime_expr_walker(Node *node, void *context);
static void native_restore_dest(NativeQueryState *state, QueryDesc *query_desc);

static bool
valid_guard_task_id(void)
{
	return guard_task_bound &&
		   guard_task_id != NULL &&
		   guard_task_id[0] != '\0';
}

static bool
valid_guard_binding_identity(void)
{
	return valid_guard_task_id() &&
		   guard_binding_id != NULL &&
		   guard_binding_id[0] != '\0' &&
		   guard_fence_token > 0 &&
		   guard_advisory_lock_key != 0;
}

static char *
text_arg_to_cstring(PG_FUNCTION_ARGS, int argno)
{
	text *value;

	if (PG_ARGISNULL(argno))
		return "";

	value = PG_GETARG_TEXT_PP(argno);
	return text_to_cstring(value);
}

static void
assign_top_string(char **target, const char *value)
{
	MemoryContext old_context;
	char *old_value = *target;
	char *new_value;

	old_context = MemoryContextSwitchTo(TopMemoryContext);
	new_value = pstrdup(value != NULL ? value : "");
	MemoryContextSwitchTo(old_context);
	*target = new_value;
	if (old_value != NULL)
		pfree(old_value);
}

static void
clear_trusted_binding_state(void)
{
	DropAllPreparedStatements();

	guard_task_bound = false;
	guard_enabled = false;
	guard_receipts_enabled = false;
	guard_budget_accounting_enabled = false;
	guard_max_queries = 0;
	guard_max_unique_expense_rows = 0;
	guard_min_group_size = 5;
	assign_top_string(&guard_task_id, "");
	assign_top_string(&guard_budget_account, "");
	assign_top_string(&guard_allowed_view_oids, "");
	assign_top_string(&guard_denied_columns, "");
	assign_top_string(&guard_binding_id, "");
	assign_top_string(&guard_token_digest, "");
	assign_top_string(&guard_credential_id, "");
	guard_fence_token = 0;
	guard_advisory_lock_key = 0;
	guard_bound_session_user_oid = InvalidOid;
}

static bool
guard_bound_identity_is_stable(void)
{
	Oid session_user_oid = GetSessionUserId();
	Oid bound_session_user_oid = OidIsValid(guard_bound_session_user_oid)
									? guard_bound_session_user_oid
									: session_user_oid;

	return session_user_oid == bound_session_user_oid &&
		   GetOuterUserId() == bound_session_user_oid;
}

static void
guard_require_bound_identity(const char *sql_text)
{
	if (!guard_task_bound)
		return;

	if (!guard_bound_identity_is_stable())
	{
		guard_current_sql = sql_text;
		guard_deny("bound task session identity cannot change with SET ROLE or SET SESSION AUTHORIZATION");
	}
}

static bool
guard_session_has_runtime_role(void)
{
	Oid runtime_role = get_role_oid("agent_runtime", true);

	if (!OidIsValid(runtime_role))
		return false;

	return is_member_of_role_nosuper(GetSessionUserId(), runtime_role);
}

static void
spi_call_void(const char *command, int nargs, Oid *argtypes, Datum *values, char *nulls)
{
	bool old_internal = guard_in_internal_spi;
	bool connected = false;
	bool pushed_snapshot = false;

	PG_TRY();
	{
		int rc;

		guard_in_internal_spi = true;
		if (!ActiveSnapshotSet())
		{
			PushActiveSnapshot(GetTransactionSnapshot());
			pushed_snapshot = true;
		}
		rc = SPI_connect();
		if (rc != SPI_OK_CONNECT)
			elog(ERROR, "SPI_connect failed: %d", rc);
		connected = true;

		rc = SPI_execute_with_args(command, nargs, argtypes, values, nulls, false, 0);
		if (rc < 0)
			elog(ERROR, "SPI_execute_with_args failed: %d", rc);

		rc = SPI_finish();
		if (rc != SPI_OK_FINISH)
			elog(ERROR, "SPI_finish failed: %d", rc);
		connected = false;
		if (pushed_snapshot)
			PopActiveSnapshot();
		pushed_snapshot = false;
		guard_in_internal_spi = old_internal;
	}
	PG_CATCH();
	{
		guard_in_internal_spi = old_internal;
		if (connected)
			SPI_finish();
		if (pushed_snapshot)
			PopActiveSnapshot();
		PG_RE_THROW();
	}
	PG_END_TRY();
}

static char *
spi_call_text(const char *command, int nargs, Oid *argtypes, Datum *values, char *nulls)
{
	bool old_internal = guard_in_internal_spi;
	bool connected = false;
	bool pushed_snapshot = false;
	char *result = NULL;

	PG_TRY();
	{
		int rc;

		guard_in_internal_spi = true;
		if (!ActiveSnapshotSet())
		{
			PushActiveSnapshot(GetTransactionSnapshot());
			pushed_snapshot = true;
		}
		rc = SPI_connect();
		if (rc != SPI_OK_CONNECT)
			elog(ERROR, "SPI_connect failed: %d", rc);
		connected = true;

		rc = SPI_execute_with_args(command, nargs, argtypes, values, nulls, false, 1);
		if (rc < 0)
			elog(ERROR, "SPI_execute_with_args failed: %d", rc);

		if (SPI_processed > 0 && SPI_tuptable != NULL)
		{
			char *raw = SPI_getvalue(SPI_tuptable->vals[0], SPI_tuptable->tupdesc, 1);
			if (raw != NULL)
			{
				MemoryContext old_context = MemoryContextSwitchTo(TopMemoryContext);
				result = pstrdup(raw);
				MemoryContextSwitchTo(old_context);
			}
		}

		rc = SPI_finish();
		if (rc != SPI_OK_FINISH)
			elog(ERROR, "SPI_finish failed: %d", rc);
		connected = false;
		if (pushed_snapshot)
			PopActiveSnapshot();
		pushed_snapshot = false;
		guard_in_internal_spi = old_internal;
	}
	PG_CATCH();
	{
		guard_in_internal_spi = old_internal;
		if (connected)
			SPI_finish();
		if (pushed_snapshot)
			PopActiveSnapshot();
		PG_RE_THROW();
	}
	PG_END_TRY();

	return result;
}

static void
validate_active_binding_or_error(void)
{
	Oid argtypes[4] = {TEXTOID, TEXTOID, INT8OID, INT8OID};
	Datum values[4];
	char nulls[4] = {' ', ' ', ' ', ' '};
	bool old_suppress_receipts = guard_suppress_receipts;

	if (!valid_guard_binding_identity())
		return;

	values[0] = CStringGetTextDatum(guard_task_id);
	values[1] = CStringGetTextDatum(guard_binding_id);
	values[2] = Int64GetDatum(guard_fence_token);
	values[3] = Int64GetDatum(guard_advisory_lock_key);

	PG_TRY();
	{
		spi_call_void(
			"SELECT taskbound.validate_active_binding($1, $2::uuid, $3, $4)",
			4,
			argtypes,
			values,
			nulls);
	}
	PG_CATCH();
	{
		ErrorData *edata;

		MemoryContextSwitchTo(ErrorContext);
		edata = CopyErrorData();
		FlushErrorState();
		guard_suppress_receipts = false;
		if (!guard_error_receipt_recorded)
			record_denied_receipt(guard_current_sql, "active binding validation failed");
		guard_suppress_receipts = old_suppress_receipts;
		FreeErrorData(edata);
		ereport(ERROR,
				(errcode(ERRCODE_INSUFFICIENT_PRIVILEGE),
				 errmsg("SessionBoundDB denied query: active binding validation failed")));
	}
	PG_END_TRY();
}

static void
record_denied_receipt(const char *sql_text, const char *reason)
{
	Oid argtypes[7] = {TEXTOID, TEXTOID, TEXTOID, TEXTOID, BOOLOID, TEXTOID, INT8OID};
	Datum values[7];
	char nulls[7] = {' ', ' ', ' ', ' ', ' ', ' ', ' '};

	if (!valid_guard_task_id() || !valid_guard_binding_identity())
		return;

	values[0] = CStringGetTextDatum(guard_task_id);
	values[1] = CStringGetTextDatum(
		guard_budget_account != NULL && guard_budget_account[0] != '\0'
			? guard_budget_account
			: guard_task_id);
	values[2] = CStringGetTextDatum(sql_text != NULL ? sql_text : "");
	values[3] = CStringGetTextDatum(reason != NULL ? reason : "query denied");
	values[4] = BoolGetDatum(guard_receipts_enabled);
	values[5] = CStringGetTextDatum(guard_binding_id);
	values[6] = Int64GetDatum(guard_fence_token);

	spi_call_void(
		"SELECT taskbound.native_denied_receipt($1, $2, $3, $4, $5, $6::uuid, $7)",
		7,
		argtypes,
		values,
		nulls);
	guard_error_receipt_recorded = true;
}

static void
guard_deny(const char *detail)
{
	if (!guard_suppress_receipts)
		record_denied_receipt(guard_current_sql, detail);
	ereport(ERROR,
			(errcode(ERRCODE_INSUFFICIENT_PRIVILEGE),
			 errmsg("SessionBound guard denied query: %s", detail)));
}

static bool
token_denied_bare_name(const char *name)
{
	char *copy;
	char *token;
	bool denied = false;

	if (name == NULL || guard_denied_columns == NULL || guard_denied_columns[0] == '\0')
		return false;

	copy = pstrdup(guard_denied_columns);
	token = strtok(copy, ",");
	while (token != NULL)
	{
		char *last_dot;
		char *column_name;

		while (*token == ' ' || *token == '\t' || *token == '\n')
			token++;
		last_dot = strrchr(token, '.');
		column_name = last_dot != NULL ? last_dot + 1 : token;
		if (pg_strcasecmp(column_name, name) == 0)
		{
			denied = true;
			break;
		}
		token = strtok(NULL, ",");
	}
	pfree(copy);
	return denied;
}

static bool
token_denied_column_reference(const char *namespace_name, const char *relation_name, const char *column_name)
{
	char *copy;
	char *token;
	bool denied = false;

	if (column_name == NULL || guard_denied_columns == NULL || guard_denied_columns[0] == '\0')
		return false;

	copy = pstrdup(guard_denied_columns);
	token = strtok(copy, ",");
	while (token != NULL)
	{
		char *last_dot;
		char *candidate_column;
		char *qualifier = NULL;
		char *qualifier_last_dot;
		char qualified_relation[NAMEDATALEN * 2 + 2];

		while (*token == ' ' || *token == '\t' || *token == '\n')
			token++;
		last_dot = strrchr(token, '.');
		candidate_column = last_dot != NULL ? last_dot + 1 : token;
		if (pg_strcasecmp(candidate_column, column_name) != 0)
		{
			token = strtok(NULL, ",");
			continue;
		}

		if (last_dot == NULL)
		{
			denied = true;
			break;
		}

		*last_dot = '\0';
		qualifier = token;
		qualifier_last_dot = strrchr(qualifier, '.');
		if (namespace_name != NULL && relation_name != NULL)
			snprintf(qualified_relation, sizeof(qualified_relation), "%s.%s", namespace_name, relation_name);
		else
			qualified_relation[0] = '\0';

		if ((relation_name != NULL && pg_strcasecmp(qualifier, relation_name) == 0) ||
			(qualified_relation[0] != '\0' && pg_strcasecmp(qualifier, qualified_relation) == 0) ||
			(qualifier_last_dot != NULL && relation_name != NULL &&
			 pg_strcasecmp(qualifier_last_dot + 1, relation_name) == 0))
		{
			denied = true;
			break;
		}

		token = strtok(NULL, ",");
	}
	pfree(copy);
	return denied;
}

static bool
name_is_sensitive(const char *name)
{
	if (name == NULL)
		return false;
	return pg_strcasecmp(name, "salary") == 0 ||
		   pg_strcasecmp(name, "bank_account") == 0 ||
		   pg_strcasecmp(name, "phone") == 0 ||
		   token_denied_bare_name(name);
}

static bool
name_is_direct_entity_identifier(const char *name)
{
	if (name == NULL)
		return false;
	return pg_strcasecmp(name, "employee_id") == 0 ||
		   pg_strcasecmp(name, "expense_id") == 0;
}

static bool
function_is_payload_aggregation(const char *name)
{
	if (name == NULL)
		return false;
	return pg_strcasecmp(name, "json_agg") == 0 ||
		   pg_strcasecmp(name, "jsonb_agg") == 0 ||
		   pg_strcasecmp(name, "array_agg") == 0 ||
		   pg_strcasecmp(name, "string_agg") == 0 ||
		   pg_strcasecmp(name, "xmlagg") == 0 ||
		   pg_strcasecmp(name, "row_to_json") == 0 ||
		   pg_strcasecmp(name, "json_build_object") == 0 ||
		   pg_strcasecmp(name, "jsonb_build_object") == 0;
}

static bool
function_is_allowed_task_function(const char *namespace_name, const char *funcname)
{
	if (namespace_name == NULL || funcname == NULL)
		return false;
	if (pg_strcasecmp(namespace_name, "pg_catalog") != 0)
		return false;

	return pg_strcasecmp(funcname, "abs") == 0 ||
		   pg_strcasecmp(funcname, "avg") == 0 ||
		   pg_strcasecmp(funcname, "bool") == 0 ||
		   pg_strcasecmp(funcname, "bpchar") == 0 ||
		   pg_strcasecmp(funcname, "coalesce") == 0 ||
		   pg_strcasecmp(funcname, "count") == 0 ||
		   pg_strcasecmp(funcname, "date") == 0 ||
		   pg_strcasecmp(funcname, "date_part") == 0 ||
		   pg_strcasecmp(funcname, "date_trunc") == 0 ||
		   pg_strcasecmp(funcname, "extract") == 0 ||
		   pg_strcasecmp(funcname, "float4") == 0 ||
		   pg_strcasecmp(funcname, "float8") == 0 ||
		   pg_strcasecmp(funcname, "greatest") == 0 ||
		   pg_strcasecmp(funcname, "int2") == 0 ||
		   pg_strcasecmp(funcname, "int4") == 0 ||
		   pg_strcasecmp(funcname, "int8") == 0 ||
		   pg_strcasecmp(funcname, "least") == 0 ||
		   pg_strcasecmp(funcname, "lower") == 0 ||
		   pg_strcasecmp(funcname, "max") == 0 ||
		   pg_strcasecmp(funcname, "min") == 0 ||
		   pg_strcasecmp(funcname, "now") == 0 ||
		   pg_strcasecmp(funcname, "nullif") == 0 ||
		   pg_strcasecmp(funcname, "numeric") == 0 ||
		   pg_strcasecmp(funcname, "round") == 0 ||
		   pg_strcasecmp(funcname, "sum") == 0 ||
		   pg_strcasecmp(funcname, "text") == 0 ||
		   pg_strcasecmp(funcname, "timestamp") == 0 ||
		   pg_strcasecmp(funcname, "timestamptz") == 0 ||
		   pg_strcasecmp(funcname, "upper") == 0;
}

static bool
function_is_public_runtime_entrypoint(const char *namespace_name, const char *funcname)
{
	if (namespace_name == NULL || funcname == NULL)
		return false;

	if (pg_strcasecmp(namespace_name, "public") == 0)
		return pg_strcasecmp(funcname, "sessionbound_guard_check") == 0 ||
			   pg_strcasecmp(funcname, "sessionbound_guard_status") == 0;

	if (pg_strcasecmp(namespace_name, "taskbound") != 0)
		return false;

	return pg_strcasecmp(funcname, "bind_task") == 0 ||
		   pg_strcasecmp(funcname, "unbind_task") == 0 ||
		   pg_strcasecmp(funcname, "run") == 0 ||
		   pg_strcasecmp(funcname, "command") == 0 ||
		   pg_strcasecmp(funcname, "inspect_task_state") == 0 ||
		   pg_strcasecmp(funcname, "receipts") == 0;
}

static bool
function_is_taskbound_private(const char *namespace_name, const char *funcname)
{
	if (namespace_name == NULL || funcname == NULL)
		return false;

	if (pg_strcasecmp(namespace_name, "taskbound") != 0)
		return false;

	return !function_is_public_runtime_entrypoint(namespace_name, funcname);
}

static bool
funcid_is_public_runtime_entrypoint(Oid funcid)
{
	char *funcname;
	char *namespace_name;

	if (!OidIsValid(funcid))
		return false;

	funcname = get_func_name(funcid);
	namespace_name = get_namespace_name(get_func_namespace(funcid));
	return function_is_public_runtime_entrypoint(namespace_name, funcname);
}

static bool
expr_is_runtime_entrypoint_call(Node *node)
{
	if (node == NULL)
		return false;

	if (IsA(node, FuncExpr))
		return funcid_is_public_runtime_entrypoint(((FuncExpr *) node)->funcid);

	if (IsA(node, RelabelType))
		return expr_is_runtime_entrypoint_call((Node *) ((RelabelType *) node)->arg);

	return false;
}

static bool
rte_is_runtime_entrypoint_function(RangeTblEntry *rte)
{
	ListCell *lc;
	int runtime_functions = 0;

	if (rte == NULL || rte->rtekind != RTE_FUNCTION)
		return false;

	foreach(lc, rte->functions)
	{
		RangeTblFunction *rtfunc = (RangeTblFunction *) lfirst(lc);
		if (rtfunc == NULL || !expr_is_runtime_entrypoint_call(rtfunc->funcexpr))
			return false;
		runtime_functions++;
	}

	return runtime_functions == 1 && !rte->funcordinality;
}

static bool
query_is_standalone_runtime_entrypoint(Query *query)
{
	ListCell *lc;
	int function_rtes = 0;
	int target_count = 0;

	if (query == NULL || query->commandType != CMD_SELECT)
		return false;

	if (query->cteList != NIL ||
		query->setOperations != NULL ||
		query->hasAggs ||
		query->hasWindowFuncs ||
		query->groupClause != NIL ||
		query->groupingSets != NIL ||
		query->havingQual != NULL ||
		query->windowClause != NIL ||
		query->distinctClause != NIL ||
		query->sortClause != NIL ||
		query->limitOffset != NULL ||
		query->limitCount != NULL ||
		query->rowMarks != NIL ||
		(query->jointree != NULL && query->jointree->quals != NULL))
		return false;

	foreach(lc, query->rtable)
	{
		RangeTblEntry *rte = (RangeTblEntry *) lfirst(lc);

		if (rte == NULL)
			continue;
		if (rte->rtekind == RTE_RESULT)
			continue;
		if (rte->rtekind != RTE_FUNCTION || !rte_is_runtime_entrypoint_function(rte))
			return false;
		function_rtes++;
	}

	foreach(lc, query->targetList)
	{
		TargetEntry *tle = (TargetEntry *) lfirst(lc);
		if (tle == NULL || tle->resjunk)
			continue;
		target_count++;
		if (function_rtes > 0)
		{
			if (!IsA(tle->expr, Var))
				return false;
		}
	}

	if (function_rtes == 1)
		return target_count > 0;

	if (function_rtes != 0 || target_count != 1)
		return false;

	foreach(lc, query->targetList)
	{
		TargetEntry *tle = (TargetEntry *) lfirst(lc);
		if (tle != NULL && !tle->resjunk)
			return expr_is_runtime_entrypoint_call((Node *) tle->expr);
	}

	return false;
}

static List *
parse_allowed_oids(const char *raw)
{
	List *oids = NIL;
	char *copy;
	char *token;

	if (raw == NULL || raw[0] == '\0')
		return NIL;

	copy = pstrdup(raw);
	token = strtok(copy, ",");
	while (token != NULL)
	{
		char *endptr = NULL;
		unsigned long value = strtoul(token, &endptr, 10);

		if (endptr != token && value > 0 && value <= PG_UINT32_MAX)
			oids = lappend_oid(oids, (Oid) value);

		token = strtok(NULL, ",");
	}

	return oids;
}

static TargetEntry *
find_target_entry_by_sortgroupref(List *target_list, Index ressortgroupref)
{
	ListCell *lc;

	foreach(lc, target_list)
	{
		TargetEntry *tle = (TargetEntry *) lfirst(lc);
		if (tle != NULL && tle->ressortgroupref == ressortgroupref)
			return tle;
	}
	return NULL;
}

static bool
target_entry_is_direct_entity_group(Query *query, TargetEntry *tle)
{
	Var *var;
	RangeTblEntry *rte;
	char *attname = NULL;

	if (tle == NULL)
		return false;

	if (name_is_direct_entity_identifier(tle->resname))
		return true;

	if (tle->expr == NULL || !IsA(tle->expr, Var))
		return false;

	var = (Var *) tle->expr;
	if (var->varattno <= 0 || var->varno <= 0)
		return false;
	if (query == NULL || var->varno > list_length(query->rtable))
		return false;

	rte = rt_fetch(var->varno, query->rtable);
	if (rte == NULL || rte->eref == NULL || var->varattno > list_length(rte->eref->colnames))
		return false;

	attname = strVal(list_nth(rte->eref->colnames, var->varattno - 1));
	return name_is_direct_entity_identifier(attname);
}

static void
guard_check_group_policy(Query *query)
{
	ListCell *lc;

	if (query == NULL)
		return;

	if (query->hasWindowFuncs)
		guard_deny("window functions require an approved aggregate template");

	if (query->havingQual != NULL)
		guard_deny("minimum group-size policy denies HAVING predicates");

	foreach(lc, query->groupClause)
	{
		SortGroupClause *sgc = (SortGroupClause *) lfirst(lc);
		TargetEntry *tle;

		if (sgc == NULL)
			continue;
		tle = find_target_entry_by_sortgroupref(query->targetList, sgc->tleSortGroupRef);
		if (target_entry_is_direct_entity_group(query, tle))
			guard_deny("minimum group-size policy denies grouping by sensitive entity identifiers");
	}

	if (query->groupClause != NIL)
		guard_deny("GROUP BY aggregate release requires an approved aggregate template");

	if (query->hasAggs && query->jointree != NULL && query->jointree->quals != NULL)
		guard_deny("filtered aggregate release requires an approved aggregate template");

	if (query->hasAggs)
		guard_deny("aggregate release requires an approved aggregate template");
}

static void
guard_check_function(Oid funcid, GuardContext *ctx)
{
	char *funcname;
	Oid namespace_oid;
	char *namespace_name;

	if (!OidIsValid(funcid))
		return;

	funcname = get_func_name(funcid);
	namespace_oid = get_func_namespace(funcid);
	namespace_name = get_namespace_name(namespace_oid);

	if (function_is_payload_aggregation(funcname))
		guard_deny("payload aggregation function is not allowed");

	if (funcname != NULL &&
		(pg_strcasecmp(funcname, "pg_advisory_unlock") == 0 ||
		 pg_strcasecmp(funcname, "pg_advisory_unlock_shared") == 0 ||
		 pg_strcasecmp(funcname, "pg_advisory_unlock_all") == 0 ||
		 pg_strcasecmp(funcname, "set_config") == 0))
		guard_deny("session lock or trusted runtime state cannot be modified by task SQL");

	if (function_is_public_runtime_entrypoint(namespace_name, funcname))
	{
		if (ctx != NULL)
		{
			ctx->saw_public_runtime_entrypoint = true;
			ctx->public_runtime_entrypoint_calls++;
		}
		return;
	}

	if (function_is_taskbound_private(namespace_name, funcname))
	{
		if (ctx != NULL)
			ctx->saw_non_public_runtime_function = true;
		guard_deny("direct access to taskbound runtime helper functions is not allowed");
	}

	if (!function_is_allowed_task_function(namespace_name, funcname))
	{
		char detail[256];
		snprintf(detail, sizeof(detail),
				 "function %s.%s is not allowed in task SQL",
				 namespace_name ? namespace_name : "<unknown>",
				 funcname ? funcname : "<unknown>");
		guard_deny(detail);
	}
}

static void
guard_check_pg_catalog_procedure(Oid funcid, const char *kind)
{
	char *funcname;
	char *namespace_name;
	char detail[256];

	if (!OidIsValid(funcid))
		return;

	funcname = get_func_name(funcid);
	namespace_name = get_namespace_name(get_func_namespace(funcid));

	if (namespace_name == NULL ||
		pg_strcasecmp(namespace_name, "pg_catalog") != 0)
	{
		snprintf(detail, sizeof(detail),
				 "%s procedure %s.%s is not allowed in task SQL",
				 kind != NULL ? kind : "expression",
				 namespace_name ? namespace_name : "<unknown>",
				 funcname ? funcname : "<unknown>");
		guard_deny(detail);
	}

	if (func_volatile(funcid) == PROVOLATILE_VOLATILE)
	{
		snprintf(detail, sizeof(detail),
				 "%s procedure pg_catalog.%s is volatile and not allowed in task SQL",
				 kind != NULL ? kind : "expression",
				 funcname ? funcname : "<unknown>");
		guard_deny(detail);
	}
}

static bool
relation_is_guarded(Oid relid)
{
	Oid namespace_oid;
	char *namespace_name;

	if (!OidIsValid(relid))
		return false;

	namespace_oid = get_rel_namespace(relid);
	namespace_name = get_namespace_name(namespace_oid);

	if (namespace_name == NULL)
		return true;

	return pg_strcasecmp(namespace_name, "taskbound") == 0 ||
		   pg_strcasecmp(namespace_name, "pg_catalog") == 0 ||
		   pg_strcasecmp(namespace_name, "information_schema") == 0;
}

static void
guard_check_relation(Oid relid, GuardContext *ctx)
{
	Oid namespace_oid;
	char *namespace_name;
	char relkind;

	if (!OidIsValid(relid))
		return;

	ctx->saw_relation = true;
	namespace_oid = get_rel_namespace(relid);
	namespace_name = get_namespace_name(namespace_oid);
	relkind = get_rel_relkind(relid);

	if (namespace_name == NULL)
		guard_deny("relation namespace could not be resolved");

	if (pg_strcasecmp(namespace_name, "pg_catalog") == 0 ||
		pg_strcasecmp(namespace_name, "information_schema") == 0)
		guard_deny("catalog access is not allowed");

	if (pg_strcasecmp(namespace_name, "app_data") == 0)
		guard_deny("raw application schema access is not allowed");

	if (!list_member_oid(ctx->allowed_view_oids, relid))
		guard_deny("relation is outside the approved safe-view registry");

	if (relkind != RELKIND_VIEW && relkind != RELKIND_MATVIEW)
		guard_deny("approved task SQL may only reference safe views");

	ctx->allowed_relation_refs++;
}

static void
guard_check_rte(RangeTblEntry *rte, GuardContext *ctx)
{
	ListCell *lc;

	if (rte == NULL)
		return;

	switch (rte->rtekind)
	{
		case RTE_RELATION:
			if (rte->tablesample != NULL)
				guard_deny("TABLESAMPLE is not allowed in task SQL");
			guard_check_relation(rte->relid, ctx);
			break;
		case RTE_SUBQUERY:
			guard_check_query(rte->subquery, ctx);
			break;
		case RTE_CTE:
			break;
		case RTE_FUNCTION:
			foreach(lc, rte->functions)
			{
				RangeTblFunction *rtfunc = (RangeTblFunction *) lfirst(lc);
				if (rtfunc != NULL)
					guard_expr_walker(rtfunc->funcexpr, ctx);
			}
			break;
		case RTE_TABLEFUNC:
			guard_deny("table functions are not allowed in task SQL");
			break;
		case RTE_VALUES:
		case RTE_RESULT:
		case RTE_NAMEDTUPLESTORE:
		case RTE_JOIN:
			break;
		default:
			guard_deny("unsupported range table entry in task SQL");
	}
}

static void
guard_check_query_permissions(Query *query)
{
	ListCell *lc;

	if (query == NULL)
		return;

	foreach(lc, query->rtable)
	{
		RangeTblEntry *rte = (RangeTblEntry *) lfirst(lc);
		RTEPermissionInfo *perminfo;
		Oid relid;
		char *relname;
		char *namespace_name;
		int bit = -1;

		if (rte == NULL || rte->perminfoindex <= 0)
			continue;
		if (rte->perminfoindex > list_length(query->rteperminfos))
			guard_deny("relation permission metadata is missing from task SQL");

		perminfo = (RTEPermissionInfo *) list_nth(query->rteperminfos, rte->perminfoindex - 1);
		if (perminfo == NULL || !OidIsValid(perminfo->relid))
			continue;

		relid = perminfo->relid;
		relname = get_rel_name(relid);
		namespace_name = get_namespace_name(get_rel_namespace(relid));

		while ((bit = bms_next_member(perminfo->selectedCols, bit)) >= 0)
		{
			AttrNumber attno = (AttrNumber) (bit + FirstLowInvalidHeapAttributeNumber);
			char *attname;

			if (attno <= 0)
				continue;

			attname = get_attname(relid, attno, false);
			if (name_is_sensitive(attname) ||
				token_denied_column_reference(namespace_name, relname, attname))
				guard_deny("denied column is outside this task capability");
		}
	}
}

static void
guard_check_cached_query_permissions(Query *query)
{
	ListCell *lc;

	if (query == NULL)
		return;

	if (query->commandType != CMD_SELECT)
		guard_deny("prepared statement must be a SELECT statement");

	/*
	 * PREPARE is structurally checked before a statement becomes reusable.
	 * EXECUTE can see rewritten view subqueries; replaying the full user-query
	 * policy there would classify safe-view implementation SQL as agent SQL.
	 * Recheck the permission metadata instead, so current denied-column claims
	 * still apply to cached statements.
	 */
	guard_check_query_permissions(query);

	foreach(lc, query->cteList)
	{
		CommonTableExpr *cte = (CommonTableExpr *) lfirst(lc);
		if (cte != NULL && cte->ctequery != NULL && IsA(cte->ctequery, Query))
			guard_check_cached_query_permissions((Query *) cte->ctequery);
	}

	foreach(lc, query->rtable)
	{
		RangeTblEntry *rte = (RangeTblEntry *) lfirst(lc);
		if (rte != NULL && rte->rtekind == RTE_SUBQUERY)
			guard_check_cached_query_permissions(rte->subquery);
	}
}

static void
guard_check_cached_plan_source(CachedPlanSource *plansource, GuardContext *ctx)
{
	ListCell *lc;
	bool saw_allowed_view = false;

	if (plansource == NULL)
		guard_deny("prepared statement is not available under the current task binding");

	if (!CachedPlanIsValid(plansource))
		guard_deny("prepared statement must be recreated under the current task binding");

	if (plansource->query_list == NIL)
		guard_deny("prepared statement query tree is not available for task revalidation");

	if (ctx == NULL || ctx->allowed_view_oids == NIL)
		guard_deny("no approved safe views are bound to this session");

	foreach(lc, plansource->query_list)
	{
		Node *query_node = (Node *) lfirst(lc);

		if (query_node == NULL)
			continue;
		if (!IsA(query_node, Query))
			guard_deny("prepared statement query tree is not available for task revalidation");

		guard_check_cached_query_permissions((Query *) query_node);
	}

	foreach(lc, plansource->relationOids)
	{
		Oid relid = lfirst_oid(lc);
		char relkind;

		if (!OidIsValid(relid))
			continue;

		relkind = get_rel_relkind(relid);
		if (relkind != RELKIND_VIEW && relkind != RELKIND_MATVIEW)
			continue;
		if (list_member_oid(ctx->allowed_view_oids, relid))
			saw_allowed_view = true;
		else if (relation_oid_is_taskbound_view(relid))
			guard_deny("prepared statement references a safe view outside the current task capability");
	}

	if (!saw_allowed_view)
		guard_deny("prepared statement must reference at least one approved safe view");
}

static void
guard_check_prepared_statement(ExecuteStmt *stmt, GuardContext *ctx)
{
	PreparedStatement *prepared;

	if (stmt == NULL || stmt->name == NULL)
		guard_deny("prepared statement name is missing");

	prepared = FetchPreparedStatement(stmt->name, false);
	if (prepared == NULL)
		guard_deny("prepared statement is not available under the current task binding");

	guard_check_cached_plan_source(prepared->plansource, ctx);
}

static bool
guard_expr_walker(Node *node, void *context)
{
	GuardContext *ctx = (GuardContext *) context;

	if (node == NULL)
		return false;

	if (IsA(node, Query))
	{
		guard_check_query((Query *) node, ctx);
		return false;
	}

	if (IsA(node, TargetEntry))
	{
		TargetEntry *tle = (TargetEntry *) node;
		if (!tle->resjunk && name_is_sensitive(tle->resname))
			guard_deny("sensitive output alias is outside this task capability");
	}
	else if (IsA(node, SubLink))
	{
		guard_deny("subquery expressions are not allowed in task SQL");
	}
	else if (IsA(node, ArrayExpr))
	{
		guard_deny("array constructors are not allowed in task SQL");
	}
	else if (IsA(node, RowExpr))
	{
		guard_deny("row constructors are not allowed in task SQL");
	}
	else if (IsA(node, XmlExpr))
	{
		guard_deny("XML constructors are not allowed in task SQL");
	}
	else if (IsA(node, JsonConstructorExpr) ||
			 IsA(node, JsonIsPredicate))
	{
		guard_deny("SQL/JSON constructors are not allowed in task SQL");
	}
	else if (IsA(node, FuncExpr))
	{
		guard_check_function(((FuncExpr *) node)->funcid, (GuardContext *) context);
	}
	else if (IsA(node, Aggref))
	{
		guard_check_function(((Aggref *) node)->aggfnoid, ctx);
	}
	else if (IsA(node, OpExpr))
	{
		guard_check_pg_catalog_procedure(((OpExpr *) node)->opfuncid, "operator");
	}
	else if (IsA(node, DistinctExpr))
	{
		guard_check_pg_catalog_procedure(((DistinctExpr *) node)->opfuncid, "distinct operator");
	}
	else if (IsA(node, NullIfExpr))
	{
		guard_check_pg_catalog_procedure(((NullIfExpr *) node)->opfuncid, "nullif operator");
	}
	else if (IsA(node, ScalarArrayOpExpr))
	{
		ScalarArrayOpExpr *expr = (ScalarArrayOpExpr *) node;
		guard_check_pg_catalog_procedure(expr->opfuncid, "scalar-array operator");
		guard_check_pg_catalog_procedure(expr->hashfuncid, "scalar-array hash operator");
		guard_check_pg_catalog_procedure(expr->negfuncid, "scalar-array negator operator");
	}
	else if (IsA(node, RowCompareExpr))
	{
		RowCompareExpr *expr = (RowCompareExpr *) node;
		ListCell *lc;

		foreach(lc, expr->opnos)
			guard_check_pg_catalog_procedure(get_opcode(lfirst_oid(lc)), "row-comparison operator");
	}
	else if (IsA(node, ArrayCoerceExpr))
	{
		ArrayCoerceExpr *expr = (ArrayCoerceExpr *) node;

		guard_expr_walker((Node *) expr->arg, context);
		guard_expr_walker((Node *) expr->elemexpr, context);
		return false;
	}
	else if (IsA(node, CoerceViaIO))
	{
		guard_deny("I/O coercions are not allowed in task SQL");
	}
	else if (IsA(node, CoerceToDomain))
	{
		guard_deny("domain coercions are not allowed in task SQL");
	}
	else if (IsA(node, SubscriptingRef))
	{
		SubscriptingRef *expr = (SubscriptingRef *) node;

		if (expr->refassgnexpr != NULL)
			guard_deny("assignment subscripting is not allowed in task SQL");
		guard_expr_walker((Node *) expr->refupperindexpr, context);
		guard_expr_walker((Node *) expr->reflowerindexpr, context);
		guard_expr_walker((Node *) expr->refexpr, context);
		return false;
	}
	else if (IsA(node, WindowFunc))
	{
		guard_deny("window functions require an approved aggregate template");
	}
	else if (IsA(node, Var))
	{
		Var *var = (Var *) node;
		Query *query = ctx != NULL ? ctx->current_query : NULL;
		RangeTblEntry *rte = NULL;
		char *attname = NULL;
		char *relname = NULL;
		char *namespace_name = NULL;

		if (query != NULL &&
			var->varattno > 0 &&
			var->varno > 0 &&
			var->varno <= list_length(query->rtable))
			rte = rt_fetch(var->varno, query->rtable);

		if (rte != NULL && rte->rtekind == RTE_RELATION)
		{
			attname = get_attname(rte->relid, var->varattno, false);
			relname = get_rel_name(rte->relid);
			namespace_name = get_namespace_name(get_rel_namespace(rte->relid));
		}
		else if (rte != NULL &&
				 rte->eref != NULL &&
				 var->varattno <= list_length(rte->eref->colnames))
		{
			attname = strVal(list_nth(rte->eref->colnames, var->varattno - 1));
		}

		if (name_is_sensitive(attname) ||
			token_denied_column_reference(namespace_name, relname, attname))
			guard_deny("denied column is outside this task capability");
	}
	else if (IsA(node, SQLValueFunction))
	{
		ctx->saw_non_public_runtime_function = true;
	}

	return expression_tree_walker(node, guard_expr_walker, context);
}

static void
guard_check_utility(Node *utility_stmt, GuardContext *ctx)
{
	if (utility_stmt == NULL)
		guard_deny("unsupported utility statement in task SQL");

	switch (nodeTag(utility_stmt))
	{
		case T_CopyStmt:
		{
			CopyStmt *stmt = (CopyStmt *) utility_stmt;
			if (stmt->is_from)
				guard_deny("COPY FROM is not allowed in task SQL");
			if (stmt->is_program)
				guard_deny("COPY PROGRAM is not allowed in task SQL");
			if (stmt->query == NULL)
				guard_deny("COPY must wrap an approved SELECT query");
			if (IsA(stmt->query, Query))
				guard_check_query((Query *) stmt->query, ctx);
			else
				ctx->defer_safe_view_requirement = true;
			return;
		}
		case T_ExplainStmt:
			guard_deny("EXPLAIN output is not available under task binding");
			return;
		case T_ExecuteStmt:
		{
			guard_check_prepared_statement((ExecuteStmt *) utility_stmt, ctx);
			ctx->defer_safe_view_requirement = true;
			return;
		}
		case T_PrepareStmt:
		{
			PrepareStmt *stmt = (PrepareStmt *) utility_stmt;
			if (stmt->query != NULL && IsA(stmt->query, Query))
				guard_check_query((Query *) stmt->query, ctx);
			else
				ctx->defer_safe_view_requirement = true;
			return;
		}
		case T_DeclareCursorStmt:
			guard_deny("cursors are not available under task binding");
			return;
		case T_FetchStmt:
			guard_deny("cursor fetch is not available under task binding");
			return;
		case T_ClosePortalStmt:
		case T_DeallocateStmt:
		case T_TransactionStmt:
			ctx->defer_safe_view_requirement = true;
			return;
		case T_VariableSetStmt:
		case T_VariableShowStmt:
		case T_DiscardStmt:
			guard_deny("session state utility command is not allowed in task SQL");
			break;
		default:
			guard_deny("utility statement is not allowed in task SQL");
	}
}

static void
guard_check_query(Query *query, GuardContext *ctx)
{
	ListCell *lc;
	Query *old_current_query;

	if (query == NULL)
		return;

	if (query->commandType == CMD_UTILITY)
	{
		guard_check_utility(query->utilityStmt, ctx);
		return;
	}

	if (query->commandType != CMD_SELECT)
		guard_deny("only SELECT statements are allowed");

	if (query->setOperations != NULL)
		guard_deny("UNION, INTERSECT, and EXCEPT are not allowed");

	guard_check_group_policy(query);

	foreach(lc, query->cteList)
	{
		CommonTableExpr *cte = (CommonTableExpr *) lfirst(lc);
		if (cte->cterecursive)
			guard_deny("recursive CTEs are not allowed");
		if (cte->ctequery != NULL && IsA(cte->ctequery, Query))
			guard_check_query((Query *) cte->ctequery, ctx);
	}

	foreach(lc, query->rtable)
		guard_check_rte((RangeTblEntry *) lfirst(lc), ctx);

	old_current_query = ctx->current_query;
	ctx->current_query = query;
	query_tree_walker(query, guard_expr_walker, ctx, QTW_IGNORE_RANGE_TABLE);
	ctx->current_query = old_current_query;

	guard_check_query_permissions(query);

	if (ctx->saw_public_runtime_entrypoint &&
		!query_is_standalone_runtime_entrypoint(query))
		guard_deny("taskbound runtime entrypoints must be invoked as a standalone top-level call");
}

static bool
relation_scan_walker(Node *node, void *context)
{
	RelationScanContext *ctx = (RelationScanContext *) context;

	if (node == NULL)
		return false;

	if (IsA(node, Query))
	{
		Query *query = (Query *) node;
		ListCell *lc;

		foreach(lc, query->rtable)
		{
			RangeTblEntry *rte = (RangeTblEntry *) lfirst(lc);
			if (rte == NULL)
				continue;
			if (rte->rtekind == RTE_RELATION)
			{
				ctx->saw_relation = true;
				if (relation_is_guarded(rte->relid))
					ctx->saw_guarded_relation = true;
			}
		}
		return query_tree_walker(query, relation_scan_walker, context, QTW_IGNORE_RANGE_TABLE);
	}

	if (IsA(node, FuncExpr))
	{
		FuncExpr *func = (FuncExpr *) node;
		char *funcname = get_func_name(func->funcid);
		char *namespace_name = get_namespace_name(get_func_namespace(func->funcid));
		if (function_is_taskbound_private(namespace_name, funcname))
			ctx->saw_private_taskbound_function = true;
	}
	else if (IsA(node, Aggref))
	{
		Aggref *agg = (Aggref *) node;
		char *funcname = get_func_name(agg->aggfnoid);
		char *namespace_name = get_namespace_name(get_func_namespace(agg->aggfnoid));
		if (function_is_taskbound_private(namespace_name, funcname))
			ctx->saw_private_taskbound_function = true;
	}

	return expression_tree_walker(node, relation_scan_walker, context);
}

static void
collect_touched_relation_oid(Oid relid, TouchedViewContext *ctx)
{
	char relkind;

	if (ctx == NULL || !OidIsValid(relid))
		return;
	if (!list_member_oid(ctx->allowed_view_oids, relid))
		return;

	relkind = get_rel_relkind(relid);
	if (relkind != RELKIND_VIEW && relkind != RELKIND_MATVIEW)
		return;

	if (!list_member_oid(guard_touched_view_oids, relid))
		guard_touched_view_oids = lappend_oid(guard_touched_view_oids, relid);
}

static void
collect_touched_views_from_utility(Node *utility_stmt, TouchedViewContext *ctx)
{
	if (utility_stmt == NULL)
		return;

	switch (nodeTag(utility_stmt))
	{
		case T_CopyStmt:
		{
			CopyStmt *stmt = (CopyStmt *) utility_stmt;
			if (stmt->query != NULL && IsA(stmt->query, Query))
				collect_touched_views_from_query((Query *) stmt->query, ctx);
			return;
		}
		case T_ExplainStmt:
		{
			ExplainStmt *stmt = (ExplainStmt *) utility_stmt;
			if (stmt->query != NULL && IsA(stmt->query, Query))
				collect_touched_views_from_query((Query *) stmt->query, ctx);
			return;
		}
		case T_PrepareStmt:
		{
			PrepareStmt *stmt = (PrepareStmt *) utility_stmt;
			if (stmt->query != NULL && IsA(stmt->query, Query))
				collect_touched_views_from_query((Query *) stmt->query, ctx);
			return;
		}
		case T_DeclareCursorStmt:
		{
			DeclareCursorStmt *stmt = (DeclareCursorStmt *) utility_stmt;
			if (stmt->query != NULL && IsA(stmt->query, Query))
				collect_touched_views_from_query((Query *) stmt->query, ctx);
			return;
		}
		default:
			return;
	}
}

static void
collect_touched_views_from_query(Query *query, TouchedViewContext *ctx)
{
	ListCell *lc;

	if (query == NULL || ctx == NULL)
		return;

	if (query->commandType == CMD_UTILITY)
	{
		collect_touched_views_from_utility(query->utilityStmt, ctx);
		return;
	}

	foreach(lc, query->cteList)
	{
		CommonTableExpr *cte = (CommonTableExpr *) lfirst(lc);
		if (cte != NULL && cte->ctequery != NULL && IsA(cte->ctequery, Query))
			collect_touched_views_from_query((Query *) cte->ctequery, ctx);
	}

	foreach(lc, query->rtable)
	{
		RangeTblEntry *rte = (RangeTblEntry *) lfirst(lc);
		if (rte == NULL)
			continue;

		switch (rte->rtekind)
		{
			case RTE_RELATION:
				collect_touched_relation_oid(rte->relid, ctx);
				break;
			case RTE_SUBQUERY:
				collect_touched_views_from_query(rte->subquery, ctx);
				break;
			case RTE_FUNCTION:
			{
				ListCell *flc;
				foreach(flc, rte->functions)
				{
					RangeTblFunction *rtfunc = (RangeTblFunction *) lfirst(flc);
					if (rtfunc != NULL)
						touched_view_walker(rtfunc->funcexpr, ctx);
				}
				break;
			}
			default:
				break;
		}
	}

	if (query->setOperations == NULL)
	{
		touched_view_walker((Node *) query->targetList, ctx);
		if (query->jointree != NULL)
			touched_view_walker(query->jointree->quals, ctx);
		touched_view_walker(query->havingQual, ctx);
	}
}

static bool
touched_view_walker(Node *node, void *context)
{
	if (node == NULL)
		return false;

	if (IsA(node, Query))
	{
		collect_touched_views_from_query((Query *) node, (TouchedViewContext *) context);
		return false;
	}
	if (IsA(node, SubLink))
	{
		SubLink *sublink = (SubLink *) node;
		if (sublink->subselect != NULL && IsA(sublink->subselect, Query))
			collect_touched_views_from_query((Query *) sublink->subselect,
											 (TouchedViewContext *) context);
	}

	return expression_tree_walker(node, touched_view_walker, context);
}

static ArrayType *
oid_list_to_array(List *oids)
{
	int n = list_length(oids);
	Datum *values;
	ListCell *lc;
	int i = 0;
	int16 typlen;
	bool typbyval;
	char typalign;

	if (n == 0)
		return construct_empty_array(OIDOID);

	values = palloc(sizeof(Datum) * n);
	foreach(lc, oids)
		values[i++] = ObjectIdGetDatum(lfirst_oid(lc));

	get_typlenbyvalalign(OIDOID, &typlen, &typbyval, &typalign);
	return construct_array(values, n, OIDOID, typlen, typbyval, typalign);
}

static void
sessionbound_guard_post_parse_analyze(ParseState *pstate, Query *query,
									  JumbleState *jstate)
{
	GuardContext ctx;
	RelationScanContext scan;
	const char *old_sql = guard_current_sql;
	bool should_enforce;
	bool unbound_runtime_session;

	if (prev_post_parse_analyze_hook)
		prev_post_parse_analyze_hook(pstate, query, jstate);

	if (guard_in_internal_spi || query == NULL)
		return;

	guard_require_bound_identity(pstate != NULL ? pstate->p_sourcetext : NULL);

	if (!guard_enabled && !guard_task_bound && GetOuterUserId() != GetSessionUserId())
		return;

	if (!guard_enabled && GetUserId() != GetOuterUserId())
		return;

	if (!guard_enabled && !guard_task_bound && superuser())
		return;

	memset(&scan, 0, sizeof(scan));
	relation_scan_walker((Node *) query, &scan);

	unbound_runtime_session = !guard_task_bound && guard_session_has_runtime_role();
	should_enforce = guard_enabled ||
					 guard_task_bound ||
					 scan.saw_guarded_relation ||
					 scan.saw_private_taskbound_function ||
					 unbound_runtime_session;

	if (!should_enforce)
		return;

	guard_current_sql = pstate != NULL ? pstate->p_sourcetext : NULL;

	PG_TRY();
	{
			if (!guard_task_bound &&
				(scan.saw_guarded_relation || scan.saw_private_taskbound_function || guard_enabled))
			{
				guard_deny("no trusted task binding is active");
			}

			memset(&ctx, 0, sizeof(ctx));
			ctx.allowed_view_oids = parse_allowed_oids(guard_allowed_view_oids);
			ctx.allowed_relation_refs = 0;
			ctx.saw_relation = false;
			ctx.saw_public_runtime_entrypoint = false;
		ctx.saw_non_public_runtime_function = false;
		ctx.defer_safe_view_requirement = false;
		ctx.explicit_check = guard_enabled;
		ctx.current_query = NULL;

		if (guard_collect_touched_oids)
		{
			TouchedViewContext touched_ctx;

			touched_ctx.allowed_view_oids = ctx.allowed_view_oids;
			collect_touched_views_from_query(query, &touched_ctx);
		}
		else
		{
			if ((guard_enabled || scan.saw_guarded_relation) && ctx.allowed_view_oids == NIL)
				guard_deny("no approved safe views are bound to this session");

			guard_check_query(query, &ctx);

			if (unbound_runtime_session &&
				(!ctx.saw_public_runtime_entrypoint ||
				 ctx.saw_non_public_runtime_function ||
				 ctx.saw_relation))
				guard_deny("no trusted task binding is active");

			if ((guard_enabled || guard_task_bound || scan.saw_guarded_relation) &&
				ctx.allowed_relation_refs == 0 &&
				!ctx.defer_safe_view_requirement &&
				(!ctx.saw_public_runtime_entrypoint ||
				 ctx.saw_non_public_runtime_function ||
				 ctx.saw_relation))
				guard_deny("task SQL must reference at least one approved safe view");
		}
	}
	PG_CATCH();
	{
		guard_current_sql = old_sql;
		PG_RE_THROW();
	}
	PG_END_TRY();

	guard_current_sql = old_sql;
}

static void
guard_utility_precheck(Node *utility_stmt, const char *query_string)
{
	if (!guard_task_bound)
		return;

	guard_current_sql = query_string;

	if (utility_stmt == NULL)
		guard_deny("unsupported utility statement in task SQL");

	switch (nodeTag(utility_stmt))
	{
		case T_CopyStmt:
		{
			CopyStmt *stmt = (CopyStmt *) utility_stmt;
			if (stmt->is_from || stmt->is_program || stmt->query == NULL)
				guard_deny("COPY must be COPY (approved SELECT) TO STDOUT");
			break;
		}
		case T_ExplainStmt:
			guard_deny("EXPLAIN output is not available under task binding");
			break;
		case T_PrepareStmt:
			break;
		case T_ExecuteStmt:
		{
			GuardContext ctx;

			memset(&ctx, 0, sizeof(ctx));
			ctx.allowed_view_oids = parse_allowed_oids(guard_allowed_view_oids);
			guard_check_prepared_statement((ExecuteStmt *) utility_stmt, &ctx);
			break;
		}
		case T_DeclareCursorStmt:
			guard_deny("cursors are not available under task binding");
			break;
		case T_FetchStmt:
			guard_deny("cursor fetch is not available under task binding");
			break;
		case T_ClosePortalStmt:
		case T_DeallocateStmt:
		case T_TransactionStmt:
			break;
		case T_VariableSetStmt:
		case T_VariableShowStmt:
		case T_DiscardStmt:
			guard_deny("session state utility command is not allowed in task SQL");
			break;
		default:
			guard_deny("utility statement is not allowed in task SQL");
			break;
	}
}

static void
sessionbound_guard_ProcessUtility(PlannedStmt *pstmt,
								  const char *queryString,
								  bool readOnlyTree,
								  ProcessUtilityContext context,
								  ParamListInfo params,
								  QueryEnvironment *queryEnv,
								  DestReceiver *dest,
								  QueryCompletion *qc)
{
	Node *utility_stmt = pstmt != NULL ? pstmt->utilityStmt : NULL;
	bool is_transaction_stmt = utility_stmt != NULL && IsA(utility_stmt, TransactionStmt);
	const char *old_sql = guard_current_sql;

	if (!guard_in_internal_spi)
		guard_error_receipt_recorded = false;

	PG_TRY();
	{
		if (!guard_in_internal_spi && !guard_task_bound && guard_session_has_runtime_role())
		{
			guard_current_sql = queryString;
			guard_deny("no trusted task binding is active");
		}

		if (!guard_in_internal_spi && guard_task_bound)
		{
			guard_require_bound_identity(queryString);
			/*
			 * Transaction control can be issued while the current transaction is
			 * aborted. Running SPI validation in that state is unsafe and is not
			 * needed for BEGIN/COMMIT/ROLLBACK themselves.
			 */
			if (!is_transaction_stmt && valid_guard_binding_identity())
				validate_active_binding_or_error();
			guard_utility_precheck(utility_stmt, queryString);
		}

		if (prev_ProcessUtility_hook)
			prev_ProcessUtility_hook(pstmt, queryString, readOnlyTree, context,
									 params, queryEnv, dest, qc);
		else
			standard_ProcessUtility(pstmt, queryString, readOnlyTree, context,
									params, queryEnv, dest, qc);

		guard_current_sql = old_sql;
	}
	PG_CATCH();
	{
		ErrorData *edata;

		guard_current_sql = old_sql;
		MemoryContextSwitchTo(ErrorContext);
		edata = CopyErrorData();
		FlushErrorState();
		if (valid_guard_task_id())
		{
			if (!guard_error_receipt_recorded)
				record_denied_receipt(queryString, "utility statement denied");
			FreeErrorData(edata);
			ereport(ERROR,
					(errcode(ERRCODE_INSUFFICIENT_PRIVILEGE),
					 errmsg("SessionBoundDB denied utility statement")));
		}
		ReThrowError(edata);
	}
	PG_END_TRY();
}

static NativeQueryState *
find_native_state(QueryDesc *query_desc)
{
	NativeQueryState *state;

	for (state = native_states; state != NULL; state = state->next)
	{
		if (state->query_desc == query_desc)
			return state;
	}
	return NULL;
}

static void
remove_native_state(NativeQueryState *state)
{
	NativeQueryState **link = &native_states;

	while (*link != NULL)
	{
		if (*link == state)
		{
			*link = state->next;
			return;
		}
		link = &(*link)->next;
	}
}

/*
 * ExecutorEnd is not guaranteed to run after an ERROR has escaped the portal
 * machinery (notably for an over-budget tuple error).  Keep those states out
 * of the next QueryDesc lookup.  Without this sweep PostgreSQL may recycle a
 * QueryDesc address and the next statement inherits the previous statement's
 * tuple counter, causing harmless audit/state queries to be denied.
 */
static void
cleanup_stale_native_states(void)
{
	NativeQueryState *state = native_states;

	while (state != NULL)
	{
		NativeQueryState *next = state->next;

		if (state->aborted || state->finished || state->query_desc == NULL)
		{
			remove_native_state(state);
			if (state->context != NULL)
				MemoryContextDelete(state->context);
		}
		state = next;
	}
}

static bool
relation_oid_is_taskbound_view(Oid relid)
{
	char relkind;
	char *namespace_name;

	if (!OidIsValid(relid))
		return false;

	relkind = get_rel_relkind(relid);
	if (relkind != RELKIND_VIEW && relkind != RELKIND_MATVIEW)
		return false;

	namespace_name = get_namespace_name(get_rel_namespace(relid));
	return namespace_name != NULL &&
		   pg_strcasecmp(namespace_name, "taskbound") == 0;
}

static bool
planned_stmt_safe_views_currently_allowed(PlannedStmt *plannedstmt)
{
	List *allowed_view_oids;
	ListCell *lc;
	bool saw_allowed_view = false;

	if (plannedstmt == NULL)
		return false;

	allowed_view_oids = parse_allowed_oids(guard_allowed_view_oids);
	if (allowed_view_oids == NIL)
		return false;

	foreach(lc, plannedstmt->rtable)
	{
		RangeTblEntry *rte = (RangeTblEntry *) lfirst(lc);
		char relkind;

		if (rte == NULL || rte->rtekind != RTE_RELATION)
			continue;

		relkind = get_rel_relkind(rte->relid);
		if (relkind != RELKIND_VIEW && relkind != RELKIND_MATVIEW)
			continue;
		if (list_member_oid(allowed_view_oids, rte->relid))
			saw_allowed_view = true;
		else if (relation_oid_is_taskbound_view(rte->relid))
			guard_deny("planned statement references a safe view outside the current task capability");
	}

	/*
	 * PostgreSQL rewrite can expand a view into base-relation RTEs before the
	 * final plan reaches ExecutorStart.  PlannedStmt keeps relation dependency
	 * OIDs for invalidation; use them as the same unforgeable OID signal when
	 * the view no longer appears as an RTE_RELATION.
	 */
	foreach(lc, plannedstmt->relationOids)
	{
		Oid relid = lfirst_oid(lc);
		char relkind;

		relkind = get_rel_relkind(relid);
		if (relkind != RELKIND_VIEW && relkind != RELKIND_MATVIEW)
			continue;
		if (list_member_oid(allowed_view_oids, relid))
			saw_allowed_view = true;
		else if (relation_oid_is_taskbound_view(relid))
			guard_deny("planned statement references a safe view outside the current task capability");
	}

	return saw_allowed_view;
}

static void
planned_runtime_check_function(Oid funcid, PlannedRuntimeContext *ctx)
{
	char *funcname;
	char *namespace_name;

	if (!OidIsValid(funcid) || ctx == NULL)
		return;

	funcname = get_func_name(funcid);
	namespace_name = get_namespace_name(get_func_namespace(funcid));
	if (function_is_public_runtime_entrypoint(namespace_name, funcname))
		ctx->saw_public_runtime_entrypoint = true;
	else
		ctx->saw_non_public_runtime_function = true;
}

static bool
planned_runtime_expr_walker(Node *node, void *context)
{
	PlannedRuntimeContext *ctx = (PlannedRuntimeContext *) context;

	if (node == NULL || ctx == NULL)
		return false;

	if (IsA(node, FuncExpr))
		planned_runtime_check_function(((FuncExpr *) node)->funcid, ctx);
	else if (IsA(node, Aggref))
		planned_runtime_check_function(((Aggref *) node)->aggfnoid, ctx);
	else if (IsA(node, OpExpr))
		planned_runtime_check_function(((OpExpr *) node)->opfuncid, ctx);
	else if (IsA(node, DistinctExpr))
		planned_runtime_check_function(((DistinctExpr *) node)->opfuncid, ctx);
	else if (IsA(node, NullIfExpr))
		planned_runtime_check_function(((NullIfExpr *) node)->opfuncid, ctx);
	else if (IsA(node, ScalarArrayOpExpr))
		planned_runtime_check_function(((ScalarArrayOpExpr *) node)->opfuncid, ctx);
	else if (IsA(node, SQLValueFunction))
		ctx->saw_non_public_runtime_function = true;

	return expression_tree_walker(node, planned_runtime_expr_walker, context);
}

static void
planned_runtime_scan_plan(Plan *plan, PlannedRuntimeContext *ctx)
{
	ListCell *lc;

	if (plan == NULL || ctx == NULL)
		return;

	expression_tree_walker((Node *) plan->targetlist, planned_runtime_expr_walker, ctx);
	expression_tree_walker((Node *) plan->qual, planned_runtime_expr_walker, ctx);
	expression_tree_walker((Node *) plan->initPlan, planned_runtime_expr_walker, ctx);

	if (IsA(plan, FunctionScan))
	{
		FunctionScan *scan = (FunctionScan *) plan;

		foreach(lc, scan->functions)
		{
			RangeTblFunction *rtfunc = (RangeTblFunction *) lfirst(lc);
			if (rtfunc != NULL)
				planned_runtime_expr_walker(rtfunc->funcexpr, ctx);
		}
	}

	planned_runtime_scan_plan(plan->lefttree, ctx);
	planned_runtime_scan_plan(plan->righttree, ctx);
}

static bool
planned_stmt_is_runtime_entrypoint_only(PlannedStmt *plannedstmt)
{
	PlannedRuntimeContext ctx;
	ListCell *lc;

	if (plannedstmt == NULL)
		return false;

	memset(&ctx, 0, sizeof(ctx));

	foreach(lc, plannedstmt->rtable)
	{
		RangeTblEntry *rte = (RangeTblEntry *) lfirst(lc);
		ListCell *flc;

		if (rte == NULL)
			continue;

		if (rte->rtekind == RTE_RELATION ||
			rte->rtekind == RTE_SUBQUERY ||
			rte->rtekind == RTE_TABLEFUNC ||
			rte->rtekind == RTE_NAMEDTUPLESTORE ||
			rte->rtekind == RTE_CTE)
			return false;

		if (rte->rtekind != RTE_FUNCTION)
			continue;

		foreach(flc, rte->functions)
		{
			RangeTblFunction *rtfunc = (RangeTblFunction *) lfirst(flc);
			if (rtfunc != NULL)
				planned_runtime_expr_walker(rtfunc->funcexpr, &ctx);
		}
	}

	planned_runtime_scan_plan(plannedstmt->planTree, &ctx);

	return ctx.saw_public_runtime_entrypoint &&
		   !ctx.saw_non_public_runtime_function;
}

static bool
should_account_query(QueryDesc *query_desc)
{
	bool references_allowed_safe_view;

	if (guard_in_internal_spi || !guard_task_bound || query_desc == NULL)
		return false;

	guard_require_bound_identity(query_desc->sourceText);

	if (!valid_guard_task_id())
		return false;

	if (!valid_guard_binding_identity())
		return false;

	if (query_desc->operation != CMD_SELECT)
		return false;

	if (query_desc->plannedstmt == NULL ||
		query_desc->plannedstmt->commandType != CMD_SELECT)
		return false;

	/*
	 * taskbound.run(), taskbound.command(), guard SPI, and receipt/budget
	 * maintenance execute through trusted SECURITY DEFINER or internal paths.
	 * Native caller SELECTs execute with CurrentUser == OuterUser == the bound
	 * session user and are accounted only if the already-planned statement
	 * carries a safe-view OID approved for this task.  Comments, string
	 * literals, aliases, and helper-name substrings cannot forge that OID.
	 */
	if (GetUserId() != GetOuterUserId())
		return false;

	references_allowed_safe_view =
		planned_stmt_safe_views_currently_allowed(query_desc->plannedstmt);
	if (!references_allowed_safe_view)
	{
		if (planned_stmt_is_runtime_entrypoint_only(query_desc->plannedstmt))
			return false;
		guard_current_sql = query_desc->sourceText;
		guard_deny("task SQL must reference at least one approved safe view");
	}

	return true;
}

static void
native_reserve_query(NativeQueryState *state)
{
	Oid argtypes[8] = {TEXTOID, TEXTOID, TEXTOID, INT4OID, BOOLOID, BOOLOID, TEXTOID, INT8OID};
	Datum values[8];
	char nulls[8] = {' ', ' ', ' ', ' ', ' ', ' ', ' ', ' '};
	char *status;

	values[0] = CStringGetTextDatum(state->task_id);
	values[1] = CStringGetTextDatum(state->budget_account);
	values[2] = CStringGetTextDatum(state->source_text != NULL ? state->source_text : "");
	values[3] = Int32GetDatum(guard_max_queries);
	values[4] = BoolGetDatum(state->budget_accounting_enabled);
	values[5] = BoolGetDatum(state->receipts_enabled);
	values[6] = CStringGetTextDatum(state->binding_id);
	values[7] = Int64GetDatum(state->fence_token);

	status = spi_call_text(
		"SELECT taskbound.native_reserve_query_status($1, $2, $3, $4, $5, $6, $7::uuid, $8)",
		8,
		argtypes,
		values,
		nulls);
	if (status == NULL || strcmp(status, "ok") != 0)
	{
		const char *reason = status != NULL ? status : "query denied";
		ereport(ERROR,
				(errcode(ERRCODE_INSUFFICIENT_PRIVILEGE),
				 errmsg("SessionBoundDB denied query: %s", reason)));
	}
	pfree(status);
}

static void
native_finish_query(NativeQueryState *state)
{
	Oid argtypes[11] = {TEXTOID, TEXTOID, TEXTOID, INT8OID, TEXTARRAYOID, INT4OID, INT4OID, BOOLOID, BOOLOID, TEXTOID, INT8OID};
	Datum values[11];
	char nulls[11] = {' ', ' ', ' ', ' ', ' ', ' ', ' ', ' ', ' ', ' ', ' '};
	Datum empty_array;
	char *status;

	/*
	 * v_new_expense_ids remains in the SQL ABI for in-place upgrades. Native
	 * accounting intentionally never derives a charge from a caller-visible
	 * identifier: every release-barrier-authorized result tuple is one
	 * conservative unit. The SQL receipt column is named rows_returned for
	 * compatibility, but it records authorization before network delivery.
	 */
	empty_array = PointerGetDatum(construct_empty_array(TEXTOID));

	values[0] = CStringGetTextDatum(state->task_id);
	values[1] = CStringGetTextDatum(state->budget_account);
	values[2] = CStringGetTextDatum(state->source_text != NULL ? state->source_text : "");
	values[3] = Int64GetDatum((int64) state->rows_authorized);
	values[4] = empty_array;
	values[5] = Int32GetDatum(guard_max_queries);
	values[6] = Int32GetDatum(state->max_unique_expense_rows);
	values[7] = BoolGetDatum(state->budget_accounting_enabled);
	values[8] = BoolGetDatum(state->receipts_enabled);
	values[9] = CStringGetTextDatum(state->binding_id);
	values[10] = Int64GetDatum(state->fence_token);

	status = spi_call_text(
		"SELECT taskbound.native_finish_query_status($1, $2, $3, $4, $5, $6, $7, $8, $9, $10::uuid, $11)",
		11,
		argtypes,
		values,
		nulls);
	if (status == NULL || strcmp(status, "ok") != 0)
	{
		const char *reason = status != NULL ? status : "query denied";
		ereport(ERROR,
				(errcode(ERRCODE_INSUFFICIENT_PRIVILEGE),
				 errmsg("SessionBoundDB denied query: %s", reason)));
	}
	pfree(status);
	state->finished = true;
}

static void
native_record_state_denial(NativeQueryState *state, const char *reason)
{
	Oid argtypes[10] = {TEXTOID, TEXTOID, TEXTOID, TEXTOID, INT4OID, INT4OID, BOOLOID, BOOLOID, TEXTOID, INT8OID};
	Datum values[10];
	char nulls[10] = {' ', ' ', ' ', ' ', ' ', ' ', ' ', ' ', ' ', ' '};
	char *status;

	if (state == NULL || state->denied_recorded)
		return;

	if (state->task_id == NULL || state->task_id[0] == '\0')
		return;

	values[0] = CStringGetTextDatum(state->task_id);
	values[1] = CStringGetTextDatum(state->budget_account);
	values[2] = CStringGetTextDatum(state->source_text != NULL ? state->source_text : "");
	values[3] = CStringGetTextDatum(reason != NULL ? reason : "query denied");
	values[4] = Int32GetDatum(guard_max_queries);
	values[5] = Int32GetDatum(state->max_unique_expense_rows);
	values[6] = BoolGetDatum(state->budget_accounting_enabled);
	values[7] = BoolGetDatum(state->receipts_enabled);
	values[8] = CStringGetTextDatum(state->binding_id);
	values[9] = Int64GetDatum(state->fence_token);

	status = spi_call_text(
		"SELECT taskbound.native_record_query_denial_status($1, $2, $3, $4, $5, $6, $7, $8, $9::uuid, $10)",
		10,
		argtypes,
		values,
		nulls);
	if (status != NULL)
		pfree(status);
	state->denied_recorded = true;
}

static void
native_observe_slot(NativeQueryState *state, TupleTableSlot *slot)
{
	if (state == NULL || slot == NULL)
		return;

	/*
	 * Count every output tuple, regardless of projection, aliasing, join, or
	 * aggregation shape.  The old expense_id-only scheme was bypassable by
	 * SELECT amount, aliases, and aggregates.  A tuple quota is conservative:
	 * repeated entities consume budget rather than becoming free disclosures.
	 */
	if (state->budget_accounting_enabled &&
		state->unique_seen_count + 1 > (uint64) state->max_unique_expense_rows)
	{
		native_record_state_denial(state, "result tuple budget exceeded; atomic release withheld");
		ereport(ERROR,
				(errcode(ERRCODE_INSUFFICIENT_PRIVILEGE),
				 errmsg("SessionBoundDB denied query: result tuple budget exceeded")));
	}

	if (state->budget_accounting_enabled)
	{
		state->unique_seen_count++;
	}
	state->rows_authorized++;
}

static void
guard_dest_startup(DestReceiver *self, int operation, TupleDesc typeinfo)
{
	GuardDestReceiver *receiver = (GuardDestReceiver *) self;
	NativeQueryState *state = receiver->state;

	if (state == NULL)
		return;

	/*
	 * Do not start the client receiver yet.  The executor writes into a
	 * tuplestore; ExecutorFinish releases it only after the durable budget
	 * debit and allow receipt have succeeded.
	 */
	state->result_operation = operation;
	if (typeinfo != NULL && state->result_desc == NULL)
		state->result_desc = CreateTupleDescCopy(typeinfo);
	if (state->result_store == NULL)
		state->result_store = tuplestore_begin_heap(false, false, work_mem);
}

static bool
guard_dest_receive(TupleTableSlot *slot, DestReceiver *self)
{
	GuardDestReceiver *receiver = (GuardDestReceiver *) self;

	native_observe_slot(receiver->state, slot);

	if (receiver->state != NULL && receiver->state->result_store != NULL)
	{
		tuplestore_puttupleslot(receiver->state->result_store, slot);
		return true;
	}
	return true;
}

static void
guard_dest_shutdown(DestReceiver *self)
{
	/* The original receiver is started and shut down by native_release_buffer. */
	(void) self;
}

static void
guard_dest_destroy(DestReceiver *self)
{
	GuardDestReceiver *receiver = (GuardDestReceiver *) self;
	if (receiver->original != NULL && receiver->original->rDestroy != NULL)
		receiver->original->rDestroy(receiver->original);
}

static void
native_wrap_dest(NativeQueryState *state, QueryDesc *query_desc)
{
	if (state == NULL || query_desc == NULL)
		return;

	if (query_desc->dest == (DestReceiver *) &state->receiver)
		return;

	state->receiver.original = query_desc->dest;
	state->receiver.state = state;
	state->receiver.pub.receiveSlot = guard_dest_receive;
	state->receiver.pub.rStartup = guard_dest_startup;
	state->receiver.pub.rShutdown = guard_dest_shutdown;
	state->receiver.pub.rDestroy = guard_dest_destroy;
	state->receiver.pub.mydest = query_desc->dest != NULL ? query_desc->dest->mydest : DestNone;
	query_desc->dest = (DestReceiver *) &state->receiver;
}

static void
native_restore_dest(NativeQueryState *state, QueryDesc *query_desc)
{
	if (state == NULL || query_desc == NULL)
		return;
	if (query_desc->dest == (DestReceiver *) &state->receiver)
		query_desc->dest = state->receiver.original;
}

static void
native_release_buffer(NativeQueryState *state)
{
	TupleTableSlot *slot;
	DestReceiver *original;

	if (state == NULL || state->result_store == NULL)
		return;

	original = state->receiver.original;
	if (original == NULL || state->result_desc == NULL)
		return;

	if (original->rStartup != NULL)
	{
		original->rStartup(original, state->result_operation, state->result_desc);
		state->original_started = true;
	}

	slot = MakeSingleTupleTableSlot(state->result_desc, &TTSOpsMinimalTuple);
	tuplestore_rescan(state->result_store);
	while (tuplestore_gettupleslot(state->result_store, true, false, slot))
	{
		if (original->receiveSlot != NULL && !original->receiveSlot(slot, original))
			break;
		ExecClearTuple(slot);
	}
	ExecDropSingleTupleTableSlot(slot);

	if (state->original_started && original->rShutdown != NULL)
		original->rShutdown(original);
	state->original_started = false;
}

static NativeQueryState *
native_create_state(QueryDesc *query_desc)
{
	NativeQueryState *state;
	MemoryContext context;
	MemoryContext old_context;

	context = AllocSetContextCreate(TopMemoryContext,
									"SessionBound native query accounting",
									ALLOCSET_DEFAULT_SIZES);
	old_context = MemoryContextSwitchTo(context);

	state = palloc0(sizeof(NativeQueryState));
	state->query_desc = query_desc;
	state->context = context;
	state->budget_accounting_enabled = guard_budget_accounting_enabled;
	state->receipts_enabled = guard_receipts_enabled;
	state->max_unique_expense_rows = guard_max_unique_expense_rows;
	state->task_id = pstrdup(guard_task_id);
	state->budget_account = pstrdup(
		guard_budget_account != NULL && guard_budget_account[0] != '\0'
			? guard_budget_account
			: guard_task_id);
	state->binding_id = pstrdup(guard_binding_id);
	state->fence_token = guard_fence_token;
	state->advisory_lock_key = guard_advisory_lock_key;
	state->source_text = pstrdup(query_desc->sourceText != NULL ? query_desc->sourceText : "");

	state->next = native_states;
	native_states = state;

	MemoryContextSwitchTo(old_context);
	return state;
}

static void
sessionbound_guard_ExecutorStart(QueryDesc *queryDesc, int eflags)
{
	cleanup_stale_native_states();

	if (should_account_query(queryDesc))
	{
		guard_current_sql = queryDesc != NULL ? queryDesc->sourceText : guard_current_sql;
		validate_active_binding_or_error();
		native_create_state(queryDesc);
	}

	if (prev_ExecutorStart_hook)
		prev_ExecutorStart_hook(queryDesc, eflags);
	else
		standard_ExecutorStart(queryDesc, eflags);
}

static void
sessionbound_guard_ExecutorRun(QueryDesc *queryDesc,
							   ScanDirection direction,
							   uint64 count,
							   bool execute_once)
{
	NativeQueryState *state = find_native_state(queryDesc);
	bool runtime_entrypoint_known = queryDesc != NULL && queryDesc->plannedstmt != NULL;
	bool runtime_entrypoint_only = false;

	if (!guard_in_internal_spi)
		guard_error_receipt_recorded = false;

	if (state == NULL && valid_guard_task_id() && runtime_entrypoint_known)
		runtime_entrypoint_only = planned_stmt_is_runtime_entrypoint_only(queryDesc->plannedstmt);

	if (state != NULL)
	{
		PG_TRY();
		{
			if (!state->reserved)
			{
				native_reserve_query(state);
				state->reserved = true;
			}
			native_wrap_dest(state, queryDesc);
		}
		PG_CATCH();
		{
			state->aborted = true;
			native_restore_dest(state, queryDesc);
			PG_RE_THROW();
		}
		PG_END_TRY();
	}

	PG_TRY();
	{
		if (prev_ExecutorRun_hook)
			prev_ExecutorRun_hook(queryDesc, direction, count, execute_once);
		else
			standard_ExecutorRun(queryDesc, direction, count, execute_once);
	}
		PG_CATCH();
		{
			ErrorData *edata;
			bool sanitize_error = false;

			MemoryContextSwitchTo(ErrorContext);
			edata = CopyErrorData();
			FlushErrorState();
			if (state != NULL)
			{
				state->aborted = true;
				native_record_state_denial(state, "query execution failed before release");
				native_restore_dest(state, queryDesc);
				sanitize_error = true;
				}
				else if (valid_guard_task_id() &&
						 (!runtime_entrypoint_known || !runtime_entrypoint_only))
				{
					if (!guard_error_receipt_recorded)
						record_denied_receipt(queryDesc != NULL ? queryDesc->sourceText : guard_current_sql,
										  "query execution failed before release");
				sanitize_error = true;
			}
			if (sanitize_error)
			{
				FreeErrorData(edata);
				ereport(ERROR,
						(errcode(ERRCODE_INSUFFICIENT_PRIVILEGE),
						 errmsg("SessionBoundDB denied query: execution failed before release")));
			}
			ReThrowError(edata);
		}
	PG_END_TRY();

	/*
	 * Plain SELECT portals do not reliably pass through ExecutorFinish before
	 * the client result stream is finalized.  For the ordinary full-result
	 * execution path, release the buffered tuples here after the fenced budget
	 * and receipt transition succeeds.  Cursor-style partial fetches keep the
	 * existing ExecutorFinish/ExecutorEnd fallback semantics.
	 */
	if (state != NULL && !state->aborted && !state->finished && count == 0)
	{
		PG_TRY();
		{
			native_finish_query(state);
			native_release_buffer(state);
		}
		PG_CATCH();
		{
			state->aborted = true;
			native_restore_dest(state, queryDesc);
			PG_RE_THROW();
		}
		PG_END_TRY();
	}
}

static void
sessionbound_guard_ExecutorFinish(QueryDesc *queryDesc)
{
	NativeQueryState *state = find_native_state(queryDesc);

	/*
	 * Commit accounting before any buffered tuple is handed to the client.
	 * A cumulative-budget conflict therefore becomes an automatic denial
	 * receipt with zero released rows, rather than a late error after a visible
	 * prefix.
	 */
	if (state != NULL && !state->aborted && !state->finished)
	{
		PG_TRY();
		{
			native_finish_query(state);
			native_release_buffer(state);
		}
		PG_CATCH();
		{
			state->aborted = true;
			native_restore_dest(state, queryDesc);
			PG_RE_THROW();
		}
		PG_END_TRY();
	}

	if (prev_ExecutorFinish_hook)
		prev_ExecutorFinish_hook(queryDesc);
	else
		standard_ExecutorFinish(queryDesc);
}

static void
sessionbound_guard_ExecutorEnd(QueryDesc *queryDesc)
{
	NativeQueryState *state = find_native_state(queryDesc);
	MemoryContext state_context = NULL;

	if (state != NULL)
	{
		state_context = state->context;
		native_restore_dest(state, queryDesc);

		/* ExecutorFinish is the normal release barrier.  This fallback is for
		 * unusual executor lifecycles and preserves the same atomic ordering. */
		if (!state->aborted && !state->finished)
		{
			PG_TRY();
			{
				native_finish_query(state);
				native_release_buffer(state);
			}
			PG_CATCH();
			{
				state->aborted = true;
				native_restore_dest(state, queryDesc);
				PG_RE_THROW();
			}
			PG_END_TRY();
		}
		remove_native_state(state);
	}

	if (prev_ExecutorEnd_hook)
		prev_ExecutorEnd_hook(queryDesc);
	else
		standard_ExecutorEnd(queryDesc);

	if (state_context != NULL)
		MemoryContextDelete(state_context);
}

void
_PG_init(void)
{
	DefineCustomBoolVariable(
		"sessionbound_guard.enabled",
		"Enable SessionBound structural SQL guard for the current execution path.",
		NULL,
		&guard_enabled,
		false,
		PGC_SUSET,
		GUC_NOT_IN_SAMPLE,
		NULL,
		NULL,
		NULL);

	DefineCustomBoolVariable(
		"sessionbound_guard.task_bound",
		"Records whether a trusted SessionBound task binding is active.",
		NULL,
		&guard_task_bound,
		false,
		PGC_SUSET,
		GUC_NOT_IN_SAMPLE,
		NULL,
		NULL,
		NULL);

	DefineCustomStringVariable(
		"sessionbound_guard.task_id",
		"Trusted SessionBound task identifier for diagnostics.",
		NULL,
		&guard_task_id,
		"",
		PGC_SUSET,
		GUC_NOT_IN_SAMPLE,
		NULL,
		NULL,
		NULL);

	DefineCustomStringVariable(
		"sessionbound_guard.budget_account",
		"Trusted SessionBound budget account for native executor accounting.",
		NULL,
		&guard_budget_account,
		"",
		PGC_SUSET,
		GUC_NOT_IN_SAMPLE,
		NULL,
		NULL,
		NULL);

	DefineCustomStringVariable(
		"sessionbound_guard.allowed_view_oids",
		"Comma-separated OIDs of safe views approved for the bound task.",
		NULL,
		&guard_allowed_view_oids,
		"",
		PGC_SUSET,
		GUC_NOT_IN_SAMPLE,
		NULL,
		NULL,
		NULL);

	DefineCustomIntVariable(
		"sessionbound_guard.max_queries",
		"Maximum native SQL queries allowed for the bound task.",
		NULL,
		&guard_max_queries,
		0,
		0,
		INT_MAX,
		PGC_SUSET,
		GUC_NOT_IN_SAMPLE,
		NULL,
		NULL,
		NULL);

	DefineCustomIntVariable(
		"sessionbound_guard.max_unique_expense_rows",
		"Maximum conservative output tuples allowed (legacy setting name).",
		NULL,
		&guard_max_unique_expense_rows,
		0,
		0,
		INT_MAX,
		PGC_SUSET,
		GUC_NOT_IN_SAMPLE,
		NULL,
		NULL,
		NULL);

	DefineCustomIntVariable(
		"sessionbound_guard.min_group_size",
		"Minimum distinct entity count required for aggregate group release.",
		NULL,
		&guard_min_group_size,
		5,
		1,
		INT_MAX,
		PGC_SUSET,
		GUC_NOT_IN_SAMPLE,
		NULL,
		NULL,
		NULL);

	DefineCustomBoolVariable(
		"sessionbound_guard.receipts_enabled",
		"Enable native SessionBound query receipts for the bound task.",
		NULL,
		&guard_receipts_enabled,
		true,
		PGC_SUSET,
		GUC_NOT_IN_SAMPLE,
		NULL,
		NULL,
		NULL);

	DefineCustomBoolVariable(
		"sessionbound_guard.budget_accounting_enabled",
		"Enable native SessionBound budget accounting for the bound task.",
		NULL,
		&guard_budget_accounting_enabled,
		true,
		PGC_SUSET,
		GUC_NOT_IN_SAMPLE,
		NULL,
		NULL,
		NULL);

	prev_post_parse_analyze_hook = post_parse_analyze_hook;
	post_parse_analyze_hook = sessionbound_guard_post_parse_analyze;
	prev_ProcessUtility_hook = ProcessUtility_hook;
	ProcessUtility_hook = sessionbound_guard_ProcessUtility;
	prev_ExecutorStart_hook = ExecutorStart_hook;
	ExecutorStart_hook = sessionbound_guard_ExecutorStart;
	prev_ExecutorRun_hook = ExecutorRun_hook;
	ExecutorRun_hook = sessionbound_guard_ExecutorRun;
	prev_ExecutorFinish_hook = ExecutorFinish_hook;
	ExecutorFinish_hook = sessionbound_guard_ExecutorFinish;
	prev_ExecutorEnd_hook = ExecutorEnd_hook;
	ExecutorEnd_hook = sessionbound_guard_ExecutorEnd;
}

void
_PG_fini(void)
{
	post_parse_analyze_hook = prev_post_parse_analyze_hook;
	ProcessUtility_hook = prev_ProcessUtility_hook;
	ExecutorStart_hook = prev_ExecutorStart_hook;
	ExecutorRun_hook = prev_ExecutorRun_hook;
	ExecutorFinish_hook = prev_ExecutorFinish_hook;
	ExecutorEnd_hook = prev_ExecutorEnd_hook;
}

Datum
sessionbound_guard_status(PG_FUNCTION_ARGS)
{
	PG_RETURN_TEXT_P(cstring_to_text("sessionbound_guard loaded"));
}

Datum
sessionbound_guard_install_binding(PG_FUNCTION_ARGS)
{
	if (GetOuterUserId() != GetSessionUserId())
		ereport(ERROR,
				(errcode(ERRCODE_INSUFFICIENT_PRIVILEGE),
				 errmsg("SessionBound guard denied binding: SET ROLE or SET SESSION AUTHORIZATION is active")));

	assign_top_string(&guard_task_id, text_arg_to_cstring(fcinfo, 0));
	assign_top_string(&guard_budget_account, text_arg_to_cstring(fcinfo, 1));
	assign_top_string(&guard_allowed_view_oids, text_arg_to_cstring(fcinfo, 2));
	assign_top_string(&guard_denied_columns, text_arg_to_cstring(fcinfo, 3));
	guard_max_queries = PG_GETARG_INT32(4);
	guard_max_unique_expense_rows = PG_GETARG_INT32(5);
	guard_min_group_size = PG_GETARG_INT32(6);
	guard_receipts_enabled = PG_GETARG_BOOL(7);
	guard_budget_accounting_enabled = PG_GETARG_BOOL(8);
	assign_top_string(&guard_binding_id, text_arg_to_cstring(fcinfo, 9));
	guard_fence_token = PG_GETARG_INT64(10);
	guard_advisory_lock_key = PG_GETARG_INT64(11);
	assign_top_string(&guard_token_digest, text_arg_to_cstring(fcinfo, 12));
	assign_top_string(&guard_credential_id, text_arg_to_cstring(fcinfo, 13));
	guard_bound_session_user_oid = GetSessionUserId();
	guard_task_bound = true;
	guard_enabled = false;

	PG_RETURN_VOID();
}

Datum
sessionbound_guard_clear_binding(PG_FUNCTION_ARGS)
{
	clear_trusted_binding_state();
	PG_RETURN_VOID();
}

Datum
sessionbound_guard_check(PG_FUNCTION_ARGS)
{
	text *sql_text = PG_GETARG_TEXT_PP(0);
	char *sql = text_to_cstring(sql_text);
	bool old_guard_enabled = guard_enabled;
	bool old_suppress_receipts = guard_suppress_receipts;
	bool spi_connected = false;
	SPIPlanPtr plan = NULL;

	PG_TRY();
	{
		int rc;

		rc = SPI_connect();
		if (rc != SPI_OK_CONNECT)
			elog(ERROR, "SPI_connect failed: %d", rc);
		spi_connected = true;

		guard_enabled = true;
		guard_suppress_receipts = true;
		plan = SPI_prepare(sql, 0, NULL);
		if (plan == NULL)
			elog(ERROR, "SPI_prepare failed for SessionBound guard check: %d", SPI_result);
		SPI_freeplan(plan);
		guard_enabled = old_guard_enabled;
		guard_suppress_receipts = old_suppress_receipts;

		rc = SPI_finish();
		if (rc != SPI_OK_FINISH)
			elog(ERROR, "SPI_finish failed: %d", rc);
		spi_connected = false;
	}
	PG_CATCH();
	{
		guard_enabled = old_guard_enabled;
		guard_suppress_receipts = old_suppress_receipts;
		if (spi_connected)
			SPI_finish();
		PG_RE_THROW();
	}
	PG_END_TRY();

	PG_RETURN_VOID();
}

Datum
sessionbound_guard_touched_view_oids(PG_FUNCTION_ARGS)
{
	text *sql_text = PG_GETARG_TEXT_PP(0);
	char *sql = text_to_cstring(sql_text);
	bool old_guard_enabled = guard_enabled;
	bool old_suppress_receipts = guard_suppress_receipts;
	bool old_collect_touched = guard_collect_touched_oids;
	bool old_internal_spi = guard_in_internal_spi;
	List *old_touched_view_oids = guard_touched_view_oids;
	MemoryContext result_context = CurrentMemoryContext;
	MemoryContext old_context;
	bool spi_connected = false;
	SPIPlanPtr plan = NULL;
	ArrayType *result_array = NULL;

	PG_TRY();
	{
		int rc;

		rc = SPI_connect();
		if (rc != SPI_OK_CONNECT)
			elog(ERROR, "SPI_connect failed: %d", rc);
		spi_connected = true;

		guard_enabled = true;
		guard_suppress_receipts = true;
		guard_collect_touched_oids = true;
		guard_in_internal_spi = false;
		guard_touched_view_oids = NIL;

		plan = SPI_prepare(sql, 0, NULL);
		if (plan == NULL)
			elog(ERROR, "SPI_prepare failed for SessionBound touched-view extraction: %d", SPI_result);
		SPI_freeplan(plan);

		old_context = MemoryContextSwitchTo(result_context);
		result_array = oid_list_to_array(guard_touched_view_oids);
		MemoryContextSwitchTo(old_context);

		guard_enabled = old_guard_enabled;
		guard_suppress_receipts = old_suppress_receipts;
		guard_collect_touched_oids = old_collect_touched;
		guard_in_internal_spi = old_internal_spi;
		guard_touched_view_oids = old_touched_view_oids;

		rc = SPI_finish();
		if (rc != SPI_OK_FINISH)
			elog(ERROR, "SPI_finish failed: %d", rc);
		spi_connected = false;
	}
	PG_CATCH();
	{
		guard_enabled = old_guard_enabled;
		guard_suppress_receipts = old_suppress_receipts;
		guard_collect_touched_oids = old_collect_touched;
		guard_in_internal_spi = old_internal_spi;
		guard_touched_view_oids = old_touched_view_oids;
		if (spi_connected)
			SPI_finish();
		PG_RE_THROW();
	}
	PG_END_TRY();

	PG_RETURN_ARRAYTYPE_P(result_array);
}
