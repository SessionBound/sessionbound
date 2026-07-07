#include "postgres.h"

#include "access/htup_details.h"
#include "catalog/namespace.h"
#include "catalog/pg_class_d.h"
#include "catalog/pg_namespace.h"
#include "catalog/pg_proc.h"
#include "fmgr.h"
#include "nodes/nodeFuncs.h"
#include "nodes/nodes.h"
#include "nodes/parsenodes.h"
#include "parser/analyze.h"
#include "executor/spi.h"
#include "utils/builtins.h"
#include "utils/guc.h"
#include "utils/lsyscache.h"
#include "utils/syscache.h"

#include <stdlib.h>

PG_MODULE_MAGIC;

void _PG_init(void);
void _PG_fini(void);

PG_FUNCTION_INFO_V1(sessionbound_guard_status);
PG_FUNCTION_INFO_V1(sessionbound_guard_check);

static post_parse_analyze_hook_type prev_post_parse_analyze_hook = NULL;

static bool guard_enabled = false;
static bool guard_task_bound = false;
static char *guard_task_id = NULL;
static char *guard_allowed_view_oids = NULL;

typedef struct GuardContext
{
	List *allowed_view_oids;
	int allowed_relation_refs;
} GuardContext;

static void sessionbound_guard_post_parse_analyze(ParseState *pstate, Query *query,
												 JumbleState *jstate);
static void guard_check_query(Query *query, GuardContext *ctx);
static bool guard_expr_walker(Node *node, void *context);

static void
guard_deny(const char *detail)
{
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
function_is_sessionbound_runtime_claim(const char *namespace_name, const char *funcname)
{
	if (namespace_name == NULL || funcname == NULL)
		return false;

	return pg_strcasecmp(namespace_name, "taskbound") == 0 &&
		   (pg_strcasecmp(funcname, "claim") == 0 ||
			pg_strcasecmp(funcname, "current_payload") == 0 ||
			pg_strcasecmp(funcname, "require_payload") == 0);
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

	if (function_is_sessionbound_runtime_claim(namespace_name, funcname))
		return;

	if (namespace_name == NULL || pg_strcasecmp(namespace_name, "pg_catalog") != 0)
		ereport(ERROR,
				(errcode(ERRCODE_INSUFFICIENT_PRIVILEGE),
				 errmsg("SessionBound guard denied query: non-catalog function %s.%s is not allowed in task SQL",
						namespace_name ? namespace_name : "<unknown>",
						funcname ? funcname : "<unknown>")));
}

static void
guard_check_relation(Oid relid, GuardContext *ctx)
{
	Oid namespace_oid;
	char *namespace_name;
	char relkind;

	if (!OidIsValid(relid))
		return;

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
guard_check_query(Query *query, GuardContext *ctx)
{
	ListCell *lc;

	if (query == NULL)
		return;

	if (query->commandType != CMD_SELECT)
		guard_deny("only SELECT statements are allowed");

	if (query->setOperations != NULL)
		guard_deny("UNION, INTERSECT, and EXCEPT are not allowed");

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

static void
sessionbound_guard_post_parse_analyze(ParseState *pstate, Query *query,
									  JumbleState *jstate)
{
	GuardContext ctx;

	if (prev_post_parse_analyze_hook)
		prev_post_parse_analyze_hook(pstate, query, jstate);

	if (!guard_enabled)
		return;

	if (!guard_task_bound)
		guard_deny("no trusted task binding is active");

	ctx.allowed_view_oids = parse_allowed_oids(guard_allowed_view_oids);
	ctx.allowed_relation_refs = 0;
	if (ctx.allowed_view_oids == NIL)
		guard_deny("no approved safe views are bound to this session");

	guard_check_query(query, &ctx);

	if (ctx.allowed_relation_refs == 0)
		guard_deny("task SQL must reference at least one approved safe view");
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

	prev_post_parse_analyze_hook = post_parse_analyze_hook;
	post_parse_analyze_hook = sessionbound_guard_post_parse_analyze;
}

void
_PG_fini(void)
{
	post_parse_analyze_hook = prev_post_parse_analyze_hook;
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
