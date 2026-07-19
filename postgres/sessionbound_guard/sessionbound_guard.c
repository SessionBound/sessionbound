#include "postgres.h"

#include "access/htup_details.h"
#include "access/xact.h"
#include "catalog/namespace.h"
#include "catalog/pg_class_d.h"
#include "catalog/pg_namespace.h"
#include "catalog/pg_proc.h"
#include "catalog/pg_type_d.h"
#include "commands/defrem.h"
#include "executor/executor.h"
#include "executor/spi.h"
#include "executor/tuptable.h"
#include "fmgr.h"
#include "miscadmin.h"
#include "nodes/nodeFuncs.h"
#include "nodes/nodes.h"
#include "nodes/parsenodes.h"
#include "nodes/plannodes.h"
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
} GuardContext;

typedef struct RelationScanContext
{
	bool saw_relation;
	bool saw_guarded_relation;
	bool saw_private_taskbound_function;
} RelationScanContext;

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
	uint64 rows_returned;
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
static bool guard_expr_walker(Node *node, void *context);
static bool relation_scan_walker(Node *node, void *context);
static bool planned_runtime_expr_walker(Node *node, void *context);

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
text_arg_to_top_cstring(PG_FUNCTION_ARGS, int argno)
{
	text *value;
	char *cstring;
	MemoryContext old_context;
	char *result;

	if (PG_ARGISNULL(argno))
		return "";

	value = PG_GETARG_TEXT_PP(argno);
	cstring = text_to_cstring(value);
	old_context = MemoryContextSwitchTo(TopMemoryContext);
	result = pstrdup(cstring);
	MemoryContextSwitchTo(old_context);
	return result;
}

static void
assign_top_string(char **target, const char *value)
{
	MemoryContext old_context;

	old_context = MemoryContextSwitchTo(TopMemoryContext);
	*target = pstrdup(value != NULL ? value : "");
	MemoryContextSwitchTo(old_context);
}

static void
clear_trusted_binding_state(void)
{
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

static void
validate_active_binding_or_error(void)
{
	Oid argtypes[4] = {TEXTOID, TEXTOID, INT8OID, INT8OID};
	Datum values[4];
	char nulls[4] = {' ', ' ', ' ', ' '};

	if (!valid_guard_binding_identity())
		return;

	values[0] = CStringGetTextDatum(guard_task_id);
	values[1] = CStringGetTextDatum(guard_binding_id);
	values[2] = Int64GetDatum(guard_fence_token);
	values[3] = Int64GetDatum(guard_advisory_lock_key);

	spi_call_void(
		"SELECT taskbound.validate_active_binding($1, $2::uuid, $3, $4)",
		4,
		argtypes,
		values,
		nulls);
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
name_is_sensitive(const char *name)
{
	if (name == NULL)
		return false;
	return pg_strcasecmp(name, "salary") == 0 ||
		   pg_strcasecmp(name, "bank_account") == 0 ||
		   pg_strcasecmp(name, "phone") == 0;
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
		   pg_strcasecmp(funcname, "receipts") == 0 ||
		   pg_strcasecmp(funcname, "fail_receipt") == 0;
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
			ctx->saw_public_runtime_entrypoint = true;
		return;
	}

	if (ctx != NULL)
		ctx->saw_non_public_runtime_function = true;

	if (function_is_taskbound_private(namespace_name, funcname))
		guard_deny("direct access to taskbound runtime helper functions is not allowed");

	if (namespace_name == NULL || pg_strcasecmp(namespace_name, "pg_catalog") != 0)
		ereport(ERROR,
				(errcode(ERRCODE_INSUFFICIENT_PRIVILEGE),
				 errmsg("SessionBound guard denied query: non-catalog function %s.%s is not allowed in task SQL",
						namespace_name ? namespace_name : "<unknown>",
						funcname ? funcname : "<unknown>")));
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

static bool
guard_expr_walker(Node *node, void *context)
{
	if (node == NULL)
		return false;

	if (IsA(node, Query))
	{
		guard_check_query((Query *) node, (GuardContext *) context);
		return false;
	}

	if (IsA(node, TargetEntry))
	{
		TargetEntry *tle = (TargetEntry *) node;
		if (!tle->resjunk && name_is_sensitive(tle->resname))
			guard_deny("sensitive output alias is outside this task capability");
	}
	else if (IsA(node, FuncExpr))
	{
		guard_check_function(((FuncExpr *) node)->funcid, (GuardContext *) context);
	}
	else if (IsA(node, Aggref))
	{
		guard_check_function(((Aggref *) node)->aggfnoid, (GuardContext *) context);
	}
	else if (IsA(node, SQLValueFunction))
	{
		((GuardContext *) context)->saw_non_public_runtime_function = true;
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
		{
			ExplainStmt *stmt = (ExplainStmt *) utility_stmt;
			ListCell *lc;

			foreach(lc, stmt->options)
			{
				DefElem *opt = (DefElem *) lfirst(lc);
				if (pg_strcasecmp(opt->defname, "analyze") == 0)
					guard_deny("EXPLAIN ANALYZE is not allowed for task SQL");
			}
			if (stmt->query != NULL && IsA(stmt->query, Query))
				guard_check_query((Query *) stmt->query, ctx);
			else
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
		{
			DeclareCursorStmt *stmt = (DeclareCursorStmt *) utility_stmt;
			if (stmt->query != NULL && IsA(stmt->query, Query))
				guard_check_query((Query *) stmt->query, ctx);
			else
				ctx->defer_safe_view_requirement = true;
			return;
		}
		case T_ExecuteStmt:
		case T_FetchStmt:
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

	query_tree_walker(query, guard_expr_walker, ctx, QTW_IGNORE_RANGE_TABLE);
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
sessionbound_guard_post_parse_analyze(ParseState *pstate, Query *query,
									  JumbleState *jstate)
{
	GuardContext ctx;
	RelationScanContext scan;
	const char *old_sql = guard_current_sql;
	bool should_enforce;

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

	should_enforce = guard_enabled ||
					 guard_task_bound ||
					 scan.saw_guarded_relation ||
					 scan.saw_private_taskbound_function;

	if (!should_enforce)
		return;

	guard_current_sql = pstate != NULL ? pstate->p_sourcetext : NULL;

	PG_TRY();
	{
		if (!guard_task_bound &&
			(scan.saw_guarded_relation || scan.saw_private_taskbound_function || guard_enabled))
			guard_deny("no trusted task binding is active");

		ctx.allowed_view_oids = parse_allowed_oids(guard_allowed_view_oids);
		ctx.allowed_relation_refs = 0;
		ctx.saw_relation = false;
		ctx.saw_public_runtime_entrypoint = false;
		ctx.saw_non_public_runtime_function = false;
		ctx.defer_safe_view_requirement = false;
		ctx.explicit_check = guard_enabled;

		if ((guard_enabled || scan.saw_guarded_relation) && ctx.allowed_view_oids == NIL)
			guard_deny("no approved safe views are bound to this session");

		guard_check_query(query, &ctx);

		if ((guard_enabled || guard_task_bound || scan.saw_guarded_relation) &&
			ctx.allowed_relation_refs == 0 &&
			!ctx.defer_safe_view_requirement &&
			(!ctx.saw_public_runtime_entrypoint ||
			 ctx.saw_non_public_runtime_function ||
			 ctx.saw_relation))
			guard_deny("task SQL must reference at least one approved safe view");
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
		{
			ExplainStmt *stmt = (ExplainStmt *) utility_stmt;
			ListCell *lc;
			foreach(lc, stmt->options)
			{
				DefElem *opt = (DefElem *) lfirst(lc);
				if (pg_strcasecmp(opt->defname, "analyze") == 0)
					guard_deny("EXPLAIN ANALYZE is not allowed for task SQL");
			}
			break;
		}
		case T_PrepareStmt:
		case T_DeclareCursorStmt:
		case T_ExecuteStmt:
		case T_FetchStmt:
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
native_reserve_utility_query(const char *query_string)
{
	Oid argtypes[8] = {TEXTOID, TEXTOID, TEXTOID, INT4OID, BOOLOID, BOOLOID, TEXTOID, INT8OID};
	Datum values[8];
	char nulls[8] = {' ', ' ', ' ', ' ', ' ', ' ', ' ', ' '};

	if (!valid_guard_task_id() || !valid_guard_binding_identity())
		return;

	values[0] = CStringGetTextDatum(guard_task_id);
	values[1] = CStringGetTextDatum(
		guard_budget_account != NULL && guard_budget_account[0] != '\0'
			? guard_budget_account
			: guard_task_id);
	values[2] = CStringGetTextDatum(query_string != NULL ? query_string : "");
	values[3] = Int32GetDatum(guard_max_queries);
	values[4] = BoolGetDatum(guard_budget_accounting_enabled);
	values[5] = BoolGetDatum(guard_receipts_enabled);
	values[6] = CStringGetTextDatum(guard_binding_id);
	values[7] = Int64GetDatum(guard_fence_token);

	spi_call_void(
		"SELECT taskbound.native_reserve_query($1, $2, $3, $4, $5, $6, $7::uuid, $8)",
		8,
		argtypes,
		values,
		nulls);
}

static void
native_finish_utility_query(const char *query_string)
{
	Oid argtypes[10] = {TEXTOID, TEXTOID, TEXTOID, INT8OID, TEXTARRAYOID, INT4OID, BOOLOID, BOOLOID, TEXTOID, INT8OID};
	Datum values[10];
	char nulls[10] = {' ', ' ', ' ', ' ', ' ', ' ', ' ', ' ', ' ', ' '};
	Datum empty_array;

	if (!valid_guard_task_id() || !valid_guard_binding_identity())
		return;

	empty_array = PointerGetDatum(construct_empty_array(TEXTOID));
	values[0] = CStringGetTextDatum(guard_task_id);
	values[1] = CStringGetTextDatum(
		guard_budget_account != NULL && guard_budget_account[0] != '\0'
			? guard_budget_account
			: guard_task_id);
	values[2] = CStringGetTextDatum(query_string != NULL ? query_string : "");
	values[3] = Int64GetDatum(0);
	values[4] = empty_array;
	values[5] = Int32GetDatum(guard_max_unique_expense_rows);
	values[6] = BoolGetDatum(guard_budget_accounting_enabled);
	values[7] = BoolGetDatum(guard_receipts_enabled);
	values[8] = CStringGetTextDatum(guard_binding_id);
	values[9] = Int64GetDatum(guard_fence_token);

	spi_call_void(
		"SELECT taskbound.native_finish_query($1, $2, $3, $4, $5, $6, $7, $8, $9::uuid, $10)",
		10,
		argtypes,
		values,
		nulls);
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
	bool account_explain = false;
	bool is_transaction_stmt = utility_stmt != NULL && IsA(utility_stmt, TransactionStmt);
	const char *old_sql = guard_current_sql;

	PG_TRY();
	{
		if (!guard_in_internal_spi && guard_task_bound)
		{
			guard_require_bound_identity(queryString);
			/*
			 * Transaction control can be issued while the current transaction is
			 * aborted.  Running SPI validation in that state is unsafe and is not
			 * needed for BEGIN/COMMIT/ROLLBACK themselves.
			 */
			if (!is_transaction_stmt && valid_guard_binding_identity())
				validate_active_binding_or_error();
			guard_utility_precheck(utility_stmt, queryString);
			account_explain = utility_stmt != NULL && IsA(utility_stmt, ExplainStmt);
			if (account_explain)
				native_reserve_utility_query(queryString);
		}

		if (prev_ProcessUtility_hook)
			prev_ProcessUtility_hook(pstmt, queryString, readOnlyTree, context,
									 params, queryEnv, dest, qc);
		else
			standard_ProcessUtility(pstmt, queryString, readOnlyTree, context,
									params, queryEnv, dest, qc);

		if (account_explain)
			native_finish_utility_query(queryString);

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
			record_denied_receipt(queryString, edata->message);
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
planned_stmt_references_allowed_safe_view(PlannedStmt *plannedstmt)
{
	List *allowed_view_oids;
	ListCell *lc;

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
		if (!list_member_oid(allowed_view_oids, rte->relid))
			continue;

		relkind = get_rel_relkind(rte->relid);
		if (relkind == RELKIND_VIEW || relkind == RELKIND_MATVIEW)
			return true;
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

		if (!list_member_oid(allowed_view_oids, relid))
			continue;

		relkind = get_rel_relkind(relid);
		if (relkind == RELKIND_VIEW || relkind == RELKIND_MATVIEW)
			return true;
	}

	return false;
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
		planned_stmt_references_allowed_safe_view(query_desc->plannedstmt);
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

	values[0] = CStringGetTextDatum(state->task_id);
	values[1] = CStringGetTextDatum(state->budget_account);
	values[2] = CStringGetTextDatum(state->source_text != NULL ? state->source_text : "");
	values[3] = Int32GetDatum(guard_max_queries);
	values[4] = BoolGetDatum(state->budget_accounting_enabled);
	values[5] = BoolGetDatum(state->receipts_enabled);
	values[6] = CStringGetTextDatum(state->binding_id);
	values[7] = Int64GetDatum(state->fence_token);

	spi_call_void(
		"SELECT taskbound.native_reserve_query($1, $2, $3, $4, $5, $6, $7::uuid, $8)",
		8,
		argtypes,
		values,
		nulls);
}

static void
native_finish_query(NativeQueryState *state)
{
	Oid argtypes[10] = {TEXTOID, TEXTOID, TEXTOID, INT8OID, TEXTARRAYOID, INT4OID, BOOLOID, BOOLOID, TEXTOID, INT8OID};
	Datum values[10];
	char nulls[10] = {' ', ' ', ' ', ' ', ' ', ' ', ' ', ' ', ' ', ' '};
	Datum empty_array;

	/*
	 * v_new_expense_ids remains in the SQL ABI for in-place upgrades. Native
	 * accounting intentionally never derives a charge from a caller-visible
	 * identifier: every released result tuple is one conservative unit.
	 */
	empty_array = PointerGetDatum(construct_empty_array(TEXTOID));

	values[0] = CStringGetTextDatum(state->task_id);
	values[1] = CStringGetTextDatum(state->budget_account);
	values[2] = CStringGetTextDatum(state->source_text != NULL ? state->source_text : "");
	values[3] = Int64GetDatum((int64) state->rows_returned);
	values[4] = empty_array;
	values[5] = Int32GetDatum(state->max_unique_expense_rows);
	values[6] = BoolGetDatum(state->budget_accounting_enabled);
	values[7] = BoolGetDatum(state->receipts_enabled);
	values[8] = CStringGetTextDatum(state->binding_id);
	values[9] = Int64GetDatum(state->fence_token);

	spi_call_void(
		"SELECT taskbound.native_finish_query($1, $2, $3, $4, $5, $6, $7, $8, $9::uuid, $10)",
		10,
		argtypes,
		values,
		nulls);
	state->finished = true;
}

static void
native_record_state_denial(NativeQueryState *state, const char *reason)
{
	Oid argtypes[7] = {TEXTOID, TEXTOID, TEXTOID, TEXTOID, BOOLOID, TEXTOID, INT8OID};
	Datum values[7];
	char nulls[7] = {' ', ' ', ' ', ' ', ' ', ' ', ' '};

	if (state == NULL || state->denied_recorded)
		return;

	if (state->task_id == NULL || state->task_id[0] == '\0')
		return;

	values[0] = CStringGetTextDatum(state->task_id);
	values[1] = CStringGetTextDatum(state->budget_account);
	values[2] = CStringGetTextDatum(state->source_text != NULL ? state->source_text : "");
	values[3] = CStringGetTextDatum(reason != NULL ? reason : "query denied");
	values[4] = BoolGetDatum(state->receipts_enabled);
	values[5] = CStringGetTextDatum(state->binding_id);
	values[6] = Int64GetDatum(state->fence_token);

	spi_call_void(
		"SELECT taskbound.native_denied_receipt($1, $2, $3, $4, $5, $6::uuid, $7)",
		7,
		argtypes,
		values,
		nulls);
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
	state->rows_returned++;
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
	NativeQueryState *state = NULL;

	cleanup_stale_native_states();

	if (should_account_query(queryDesc))
	{
		validate_active_binding_or_error();
		state = native_create_state(queryDesc);
		PG_TRY();
		{
			native_wrap_dest(state, queryDesc);
		}
		PG_CATCH();
		{
			state->aborted = true;
			remove_native_state(state);
			MemoryContextDelete(state->context);
			PG_RE_THROW();
		}
		PG_END_TRY();
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

	if (state != NULL)
	{
		if (!state->reserved)
		{
			native_reserve_query(state);
			state->reserved = true;
		}
		native_wrap_dest(state, queryDesc);
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

		MemoryContextSwitchTo(ErrorContext);
		edata = CopyErrorData();
		FlushErrorState();
		if (state != NULL)
		{
			state->aborted = true;
			native_record_state_denial(state, edata->message);
		}
		else if (valid_guard_task_id())
			record_denied_receipt(queryDesc != NULL ? queryDesc->sourceText : guard_current_sql,
								  edata->message);
		ReThrowError(edata);
	}
	PG_END_TRY();
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
			ErrorData *edata;

			MemoryContextSwitchTo(ErrorContext);
			edata = CopyErrorData();
			FlushErrorState();
			state->aborted = true;
			native_record_state_denial(state, edata->message);
			ReThrowError(edata);
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
		if (queryDesc != NULL && queryDesc->dest == (DestReceiver *) &state->receiver)
			queryDesc->dest = state->receiver.original;

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
				ErrorData *edata;

				MemoryContextSwitchTo(ErrorContext);
				edata = CopyErrorData();
				FlushErrorState();
				state->aborted = true;
				native_record_state_denial(state, edata->message);
				ReThrowError(edata);
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

	assign_top_string(&guard_task_id, text_arg_to_top_cstring(fcinfo, 0));
	assign_top_string(&guard_budget_account, text_arg_to_top_cstring(fcinfo, 1));
	assign_top_string(&guard_allowed_view_oids, text_arg_to_top_cstring(fcinfo, 2));
	guard_max_queries = PG_GETARG_INT32(3);
	guard_max_unique_expense_rows = PG_GETARG_INT32(4);
	guard_min_group_size = PG_GETARG_INT32(5);
	guard_receipts_enabled = PG_GETARG_BOOL(6);
	guard_budget_accounting_enabled = PG_GETARG_BOOL(7);
	assign_top_string(&guard_binding_id, text_arg_to_top_cstring(fcinfo, 8));
	guard_fence_token = PG_GETARG_INT64(9);
	guard_advisory_lock_key = PG_GETARG_INT64(10);
	assign_top_string(&guard_token_digest, text_arg_to_top_cstring(fcinfo, 11));
	assign_top_string(&guard_credential_id, text_arg_to_top_cstring(fcinfo, 12));
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
