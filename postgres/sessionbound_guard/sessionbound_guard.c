#include "postgres.h"

#include "access/htup_details.h"
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
#include "parser/analyze.h"
#include "parser/parsetree.h"
#include "tcop/dest.h"
#include "tcop/utility.h"
#include "utils/array.h"
#include "utils/acl.h"
#include "utils/builtins.h"
#include "utils/guc.h"
#include "utils/hsearch.h"
#include "utils/lsyscache.h"
#include "utils/memutils.h"
#include "utils/snapmgr.h"
#include "utils/syscache.h"

#include <limits.h>
#include <stdlib.h>
#include <string.h>

PG_MODULE_MAGIC;

void _PG_init(void);
void _PG_fini(void);

PG_FUNCTION_INFO_V1(sessionbound_guard_status);
PG_FUNCTION_INFO_V1(sessionbound_guard_check);

#define ROW_ID_KEY_SIZE 256

typedef struct GuardContext
{
	List *allowed_view_oids;
	int allowed_relation_refs;
	bool saw_relation;
	bool explicit_check;
} GuardContext;

typedef struct RelationScanContext
{
	bool saw_relation;
	bool saw_guarded_relation;
	bool saw_private_taskbound_function;
} RelationScanContext;

typedef struct RowIdEntry
{
	char row_id[ROW_ID_KEY_SIZE];
} RowIdEntry;

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
	uint64 unique_new_count;
	List *new_expense_ids;
	HTAB *seen_expense_ids;
	bool budget_accounting_enabled;
	bool receipts_enabled;
	bool aborted;
	bool denied_recorded;
	bool finished;
	bool reserved;
	int max_unique_expense_rows;
	char *task_id;
	char *budget_account;
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
static bool guard_in_internal_spi = false;
static const char *guard_current_sql = NULL;
static NativeQueryState *native_states = NULL;

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
static bool guard_expr_walker(Node *node, void *context);
static bool relation_scan_walker(Node *node, void *context);

static bool
source_contains_i(const char *source, const char *needle)
{
	size_t needle_len;
	const char *p;

	if (source == NULL || needle == NULL)
		return false;

	needle_len = strlen(needle);
	if (needle_len == 0)
		return true;

	for (p = source; *p; p++)
	{
		if (pg_strncasecmp(p, needle, needle_len) == 0)
			return true;
	}
	return false;
}

static bool
valid_guard_task_id(void)
{
	return guard_task_bound &&
		   guard_task_id != NULL &&
		   guard_task_id[0] != '\0';
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
record_denied_receipt(const char *sql_text, const char *reason)
{
	Oid argtypes[5] = {TEXTOID, TEXTOID, TEXTOID, TEXTOID, BOOLOID};
	Datum values[5];
	char nulls[5] = {' ', ' ', ' ', ' ', ' '};

	if (!valid_guard_task_id())
		return;

	values[0] = CStringGetTextDatum(guard_task_id);
	values[1] = CStringGetTextDatum(
		guard_budget_account != NULL && guard_budget_account[0] != '\0'
			? guard_budget_account
			: guard_task_id);
	values[2] = CStringGetTextDatum(sql_text != NULL ? sql_text : "");
	values[3] = CStringGetTextDatum(reason != NULL ? reason : "query denied");
	values[4] = BoolGetDatum(guard_receipts_enabled);

	spi_call_void(
		"SELECT taskbound.native_denied_receipt($1, $2, $3, $4, $5)",
		5,
		argtypes,
		values,
		nulls);
}

static void
guard_deny(const char *detail)
{
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
guard_check_function(Oid funcid)
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

	if (function_is_public_runtime_entrypoint(namespace_name, funcname))
		return;

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
		guard_check_function(((FuncExpr *) node)->funcid);
	}
	else if (IsA(node, Aggref))
	{
		guard_check_function(((Aggref *) node)->aggfnoid);
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
			return;
		}
		case T_PrepareStmt:
		{
			PrepareStmt *stmt = (PrepareStmt *) utility_stmt;
			if (stmt->query != NULL && IsA(stmt->query, Query))
				guard_check_query((Query *) stmt->query, ctx);
			return;
		}
		case T_DeclareCursorStmt:
		{
			DeclareCursorStmt *stmt = (DeclareCursorStmt *) utility_stmt;
			if (stmt->query != NULL && IsA(stmt->query, Query))
				guard_check_query((Query *) stmt->query, ctx);
			return;
		}
		case T_ExecuteStmt:
		case T_FetchStmt:
		case T_ClosePortalStmt:
		case T_DeallocateStmt:
		case T_TransactionStmt:
			return;
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

	if (!guard_enabled && GetUserId() != GetSessionUserId())
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
		ctx.explicit_check = guard_enabled;

		if ((guard_enabled || scan.saw_guarded_relation) && ctx.allowed_view_oids == NIL)
			guard_deny("no approved safe views are bound to this session");

		guard_check_query(query, &ctx);

		if ((guard_enabled || scan.saw_guarded_relation) && ctx.allowed_relation_refs == 0)
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
		default:
			if (source_contains_i(query_string, "app_data") ||
				source_contains_i(query_string, "pg_catalog") ||
				source_contains_i(query_string, "information_schema") ||
				source_contains_i(query_string, "create ") ||
				source_contains_i(query_string, "alter ") ||
				source_contains_i(query_string, "drop ") ||
				source_contains_i(query_string, "grant ") ||
				source_contains_i(query_string, "revoke "))
				guard_deny("utility statement is not allowed in task SQL");
			break;
	}
}

static void
native_reserve_utility_query(const char *query_string)
{
	Oid argtypes[6] = {TEXTOID, TEXTOID, TEXTOID, INT4OID, BOOLOID, BOOLOID};
	Datum values[6];
	char nulls[6] = {' ', ' ', ' ', ' ', ' ', ' '};

	if (!valid_guard_task_id())
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

	spi_call_void(
		"SELECT taskbound.native_reserve_query($1, $2, $3, $4, $5, $6)",
		6,
		argtypes,
		values,
		nulls);
}

static void
native_finish_utility_query(const char *query_string)
{
	Oid argtypes[8] = {TEXTOID, TEXTOID, TEXTOID, INT8OID, TEXTARRAYOID, INT4OID, BOOLOID, BOOLOID};
	Datum values[8];
	char nulls[8] = {' ', ' ', ' ', ' ', ' ', ' ', ' ', ' '};
	Datum empty_array;

	if (!valid_guard_task_id())
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

	spi_call_void(
		"SELECT taskbound.native_finish_query($1, $2, $3, $4, $5, $6, $7, $8)",
		8,
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

	if (!guard_in_internal_spi && guard_task_bound)
	{
		guard_utility_precheck(utility_stmt, queryString);
		account_explain = utility_stmt != NULL && IsA(utility_stmt, ExplainStmt);
		if (account_explain)
			native_reserve_utility_query(queryString);
	}

	PG_TRY();
	{
		if (prev_ProcessUtility_hook)
			prev_ProcessUtility_hook(pstmt, queryString, readOnlyTree, context,
									 params, queryEnv, dest, qc);
		else
			standard_ProcessUtility(pstmt, queryString, readOnlyTree, context,
									params, queryEnv, dest, qc);

		if (account_explain)
			native_finish_utility_query(queryString);
	}
	PG_CATCH();
	{
		ErrorData *edata;

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

static bool
source_is_runtime_helper_call(const char *source)
{
	return source_contains_i(source, "taskbound.bind_task") ||
		   source_contains_i(source, "taskbound.unbind_task") ||
		   source_contains_i(source, "taskbound.run") ||
		   source_contains_i(source, "taskbound.command") ||
		   source_contains_i(source, "taskbound.inspect_task_state") ||
		   source_contains_i(source, "taskbound.receipts") ||
		   source_contains_i(source, "taskbound.fail_receipt") ||
		   source_contains_i(source, "taskbound.native_") ||
		   source_contains_i(source, "taskbound.audit_") ||
		   source_contains_i(source, "public.sessionbound_guard_check") ||
		   source_contains_i(source, "public.sessionbound_guard_status");
}

static bool
should_account_query(QueryDesc *query_desc)
{
	if (guard_in_internal_spi || !guard_task_bound || query_desc == NULL)
		return false;

	if (!valid_guard_task_id())
		return false;

	if (query_desc->operation != CMD_SELECT)
		return false;

	if (query_desc->plannedstmt == NULL ||
		query_desc->plannedstmt->commandType != CMD_SELECT)
		return false;

	if (GetUserId() != GetSessionUserId())
		return false;

	if (source_is_runtime_helper_call(query_desc->sourceText))
		return false;

	return true;
}

static bool
row_id_hash_contains(NativeQueryState *state, const char *row_id)
{
	char key[ROW_ID_KEY_SIZE];

	if (state->seen_expense_ids == NULL || row_id == NULL)
		return false;
	if (strlen(row_id) >= ROW_ID_KEY_SIZE)
		ereport(ERROR,
				(errcode(ERRCODE_PROGRAM_LIMIT_EXCEEDED),
				 errmsg("SessionBoundDB denied query: expense_id is too large for native accounting")));

	strlcpy(key, row_id, ROW_ID_KEY_SIZE);
	return hash_search(state->seen_expense_ids, key, HASH_FIND, NULL) != NULL;
}

static void
row_id_hash_add(NativeQueryState *state, const char *row_id)
{
	char key[ROW_ID_KEY_SIZE];

	if (state->seen_expense_ids == NULL || row_id == NULL)
		return;
	if (strlen(row_id) >= ROW_ID_KEY_SIZE)
		ereport(ERROR,
				(errcode(ERRCODE_PROGRAM_LIMIT_EXCEEDED),
				 errmsg("SessionBoundDB denied query: expense_id is too large for native accounting")));

	strlcpy(key, row_id, ROW_ID_KEY_SIZE);
	hash_search(state->seen_expense_ids, key, HASH_ENTER, NULL);
}

static void
native_load_seen_rows(NativeQueryState *state)
{
	Oid argtypes[1] = {TEXTOID};
	Datum values[1];
	char nulls[1] = {' '};
	bool old_internal = guard_in_internal_spi;
	bool connected = false;
	bool pushed_snapshot = false;

	if (!state->budget_accounting_enabled)
		return;

	values[0] = CStringGetTextDatum(state->budget_account);

	PG_TRY();
	{
		int rc;
		uint64 i;

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

		rc = SPI_execute_with_args(
			"SELECT row_id FROM taskbound.native_seen_expense_rows($1)",
			1,
			argtypes,
			values,
			nulls,
			true,
			0);
		if (rc != SPI_OK_SELECT)
			elog(ERROR, "native_seen_expense_rows failed: %d", rc);

		for (i = 0; i < SPI_processed; i++)
		{
			bool isnull = false;
			Datum row_id_datum = SPI_getbinval(SPI_tuptable->vals[i],
											   SPI_tuptable->tupdesc,
											   1,
											   &isnull);
			if (!isnull)
			{
				char *row_id = TextDatumGetCString(row_id_datum);
				if (!row_id_hash_contains(state, row_id))
				{
					row_id_hash_add(state, row_id);
					state->unique_seen_count++;
				}
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
}

static void
native_reserve_query(NativeQueryState *state)
{
	Oid argtypes[6] = {TEXTOID, TEXTOID, TEXTOID, INT4OID, BOOLOID, BOOLOID};
	Datum values[6];
	char nulls[6] = {' ', ' ', ' ', ' ', ' ', ' '};

	values[0] = CStringGetTextDatum(state->task_id);
	values[1] = CStringGetTextDatum(state->budget_account);
	values[2] = CStringGetTextDatum(state->source_text != NULL ? state->source_text : "");
	values[3] = Int32GetDatum(guard_max_queries);
	values[4] = BoolGetDatum(state->budget_accounting_enabled);
	values[5] = BoolGetDatum(state->receipts_enabled);

	spi_call_void(
		"SELECT taskbound.native_reserve_query($1, $2, $3, $4, $5, $6)",
		6,
		argtypes,
		values,
		nulls);
}

static void
native_finish_query(NativeQueryState *state)
{
	Oid argtypes[8] = {TEXTOID, TEXTOID, TEXTOID, INT8OID, TEXTARRAYOID, INT4OID, BOOLOID, BOOLOID};
	Datum values[8];
	char nulls[8] = {' ', ' ', ' ', ' ', ' ', ' ', ' ', ' '};
	int nids;
	Datum *elems;
	ArrayType *array;
	ListCell *lc;
	int i = 0;

	nids = list_length(state->new_expense_ids);
	elems = nids > 0 ? palloc(sizeof(Datum) * nids) : NULL;
	foreach(lc, state->new_expense_ids)
	{
		elems[i++] = CStringGetTextDatum((char *) lfirst(lc));
	}
	array = nids > 0
				? construct_array(elems, nids, TEXTOID, -1, false, TYPALIGN_INT)
				: construct_empty_array(TEXTOID);

	values[0] = CStringGetTextDatum(state->task_id);
	values[1] = CStringGetTextDatum(state->budget_account);
	values[2] = CStringGetTextDatum(state->source_text != NULL ? state->source_text : "");
	values[3] = Int64GetDatum((int64) state->rows_returned);
	values[4] = PointerGetDatum(array);
	values[5] = Int32GetDatum(state->max_unique_expense_rows);
	values[6] = BoolGetDatum(state->budget_accounting_enabled);
	values[7] = BoolGetDatum(state->receipts_enabled);

	spi_call_void(
		"SELECT taskbound.native_finish_query($1, $2, $3, $4, $5, $6, $7, $8)",
		8,
		argtypes,
		values,
		nulls);
	state->finished = true;
}

static void
native_record_state_denial(NativeQueryState *state, const char *reason)
{
	if (state == NULL || state->denied_recorded)
		return;

	record_denied_receipt(state->source_text, reason);
	state->denied_recorded = true;
}

static void
native_observe_slot(NativeQueryState *state, TupleTableSlot *slot)
{
	TupleDesc desc;
	int attnum;
	bool isnull = false;
	Datum value;
	char *row_id;

	if (state == NULL || slot == NULL)
		return;

	state->rows_returned++;

	if (!state->budget_accounting_enabled)
		return;

	desc = slot->tts_tupleDescriptor;
	if (desc == NULL)
		return;

	attnum = SPI_fnumber(desc, "expense_id");
	if (attnum <= 0)
		return;

	value = slot_getattr(slot, attnum, &isnull);
	if (isnull)
		return;

	row_id = TextDatumGetCString(value);
	if (row_id_hash_contains(state, row_id))
		return;

	if (state->unique_seen_count + 1 > (uint64) state->max_unique_expense_rows)
	{
		native_record_state_denial(state, "unique expense row budget exceeded");
		ereport(ERROR,
				(errcode(ERRCODE_INSUFFICIENT_PRIVILEGE),
				 errmsg("SessionBoundDB denied query: unique expense row budget exceeded")));
	}

	row_id_hash_add(state, row_id);
	state->unique_seen_count++;
	state->unique_new_count++;
	state->new_expense_ids = lappend(state->new_expense_ids, MemoryContextStrdup(state->context, row_id));
}

static void
guard_dest_startup(DestReceiver *self, int operation, TupleDesc typeinfo)
{
	GuardDestReceiver *receiver = (GuardDestReceiver *) self;
	if (receiver->original != NULL && receiver->original->rStartup != NULL)
		receiver->original->rStartup(receiver->original, operation, typeinfo);
}

static bool
guard_dest_receive(TupleTableSlot *slot, DestReceiver *self)
{
	GuardDestReceiver *receiver = (GuardDestReceiver *) self;

	native_observe_slot(receiver->state, slot);

	if (receiver->original != NULL && receiver->original->receiveSlot != NULL)
		return receiver->original->receiveSlot(slot, receiver->original);
	return true;
}

static void
guard_dest_shutdown(DestReceiver *self)
{
	GuardDestReceiver *receiver = (GuardDestReceiver *) self;
	if (receiver->original != NULL && receiver->original->rShutdown != NULL)
		receiver->original->rShutdown(receiver->original);
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

static NativeQueryState *
native_create_state(QueryDesc *query_desc)
{
	NativeQueryState *state;
	MemoryContext context;
	MemoryContext old_context;
	HASHCTL ctl;

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
	state->source_text = pstrdup(query_desc->sourceText != NULL ? query_desc->sourceText : "");

	memset(&ctl, 0, sizeof(ctl));
	ctl.keysize = ROW_ID_KEY_SIZE;
	ctl.entrysize = sizeof(RowIdEntry);
	ctl.hcxt = context;
	state->seen_expense_ids = hash_create("SessionBound seen expense ids",
										  1024,
										  &ctl,
										  HASH_ELEM | HASH_STRINGS | HASH_CONTEXT);

	state->next = native_states;
	native_states = state;

	MemoryContextSwitchTo(old_context);
	return state;
}

static void
sessionbound_guard_ExecutorStart(QueryDesc *queryDesc, int eflags)
{
	NativeQueryState *state = NULL;

	if (should_account_query(queryDesc))
	{
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
			native_load_seen_rows(state);
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

		if (!state->aborted && !state->finished)
			native_finish_query(state);
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
		"Maximum unique expense rows allowed for the bound task.",
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
sessionbound_guard_check(PG_FUNCTION_ARGS)
{
	text *sql_text = PG_GETARG_TEXT_PP(0);
	char *sql = text_to_cstring(sql_text);
	bool old_guard_enabled = guard_enabled;
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
		plan = SPI_prepare(sql, 0, NULL);
		if (plan == NULL)
			elog(ERROR, "SPI_prepare failed for SessionBound guard check: %d", SPI_result);
		SPI_freeplan(plan);
		guard_enabled = old_guard_enabled;

		rc = SPI_finish();
		if (rc != SPI_OK_FINISH)
			elog(ERROR, "SPI_finish failed: %d", rc);
		spi_connected = false;
	}
	PG_CATCH();
	{
		guard_enabled = old_guard_enabled;
		if (spi_connected)
			SPI_finish();
		PG_RE_THROW();
	}
	PG_END_TRY();

	PG_RETURN_VOID();
}
