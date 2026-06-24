import json
import os
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx
import psycopg
from psycopg import sql as psql
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from admin_ui import ADMIN_HTML as CONTROL_PLANE_HTML
from taskbound_demo import DATABASE_URL
from task_registry import (
    COMMAND_POLICIES,
    SAFE_VIEWS,
    TASK_GRANTS,
    TASK_TEMPLATES,
    build_task_from_template,
    upsert_grant,
    upsert_template,
)
from user_ui import USER_HTML


app = FastAPI(title="SessionBoundDB Travel Demo")
ADMIN_DATABASE_URL = os.environ.get(
    "ADMIN_DATABASE_URL",
    "postgresql://postgres:postgres@localhost:15432/travel",
)
AGENT_QUERY_DB_HOST = os.environ.get("AGENT_QUERY_DB_HOST")
AGENT_QUERY_DB_PORT = os.environ.get("AGENT_QUERY_DB_PORT")
DEEPSEEK_API_KEY = os.environ.get("DEEPSEEK_API_KEY", "")
DEEPSEEK_MODEL = os.environ.get("DEEPSEEK_MODEL", "deepseek-v4-flash")
DEEPSEEK_BASE_URL = os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com")


class CreateTaskRequest(BaseModel):
    task_id: str = "task_expense_review_api"
    task_type: str = "monthly_travel_expense_review"
    delegator: str = "user:alice"
    actor: str | None = None
    department_id: str | None = None
    scope: dict[str, Any] | None = None
    budgets: dict[str, int] | None = None
    max_queries: int = Field(default=5, ge=1, le=100)
    max_rows: int = Field(default=4, ge=1, le=5000)


class QueryRequest(BaseModel):
    payload_text: str
    signature: str
    sql: str


class CredentialRequest(BaseModel):
    agent_id: str = Field(default="travel-analyst", min_length=1, max_length=64)
    ttl_minutes: int = Field(default=15, ge=1, le=60)


class AgentCredential(BaseModel):
    db_host: str = "postgres"
    db_port: int = 5432
    db_name: str = "travel"
    db_user: str
    db_password: str


class AgentQueryRequest(QueryRequest):
    credential: AgentCredential


class AgentCommandRequest(BaseModel):
    credential: AgentCredential
    payload_text: str
    signature: str
    command_name: str
    args: dict[str, Any]


class AgentChatRequest(BaseModel):
    user_request: str
    task_type: str = "monthly_travel_expense_review"
    delegator: str = "user:alice"
    department_id: str | None = None
    max_rows: int = Field(default=50, ge=1, le=5000)
    max_queries: int = Field(default=20, ge=1, le=100)


class TodoListRequest(BaseModel):
    delegator: str = "user:fiona"
    expense_month: str = "2026-06"


class TaskTemplateRequest(BaseModel):
    template: dict[str, Any]


class TaskGrantRequest(BaseModel):
    grant: dict[str, Any]


def connect():
    conn = psycopg.connect(DATABASE_URL)
    conn.autocommit = True
    return conn


def admin_connect():
    conn = psycopg.connect(ADMIN_DATABASE_URL)
    conn.autocommit = True
    return conn


def connect_with_credential(credential: AgentCredential):
    host = AGENT_QUERY_DB_HOST or credential.db_host
    port = int(AGENT_QUERY_DB_PORT or credential.db_port)
    conninfo = (
        f"postgresql://{credential.db_user}:{credential.db_password}"
        f"@{host}:{port}/{credential.db_name}"
    )
    conn = psycopg.connect(conninfo)
    conn.autocommit = True
    return conn


def rows_as_dicts(cur) -> list[dict[str, Any]]:
    names = [d.name for d in cur.description]
    return [dict(zip(names, row)) for row in cur.fetchall()]


def user_role(delegator: str) -> str:
    return {
        "user:eve": "employee",
        "user:fiona": "finance_reviewer",
        "user:alice": "department_manager",
        "user:carol": "c_level",
    }.get(delegator, "unknown")


def todo_for_row(row: dict[str, Any], role: str) -> dict[str, Any] | None:
    status = row["status"]
    expense_id = row["expense_id"]
    amount = float(row["amount"])
    base = {
        "expense_id": expense_id,
        "employee_name": row["employee_name"],
        "department_name": row["department_name"],
        "category": row["category"],
        "merchant": row["merchant"],
        "amount": amount,
        "status": status,
        "monthly_employee_total": float(row["monthly_employee_total"]),
        "yearly_employee_total": float(row["yearly_employee_total"]),
    }
    if role == "finance_reviewer" and status in {"submitted", "resubmitted", "finance_review_requested"}:
        return {
            **base,
            "todo_id": f"todo_finance_{expense_id}",
            "todo_type": "finance_compliance_review",
            "title": f"{expense_id} 等待财务初审",
            "reason": "员工提交或补充后的报销需要财务检查票据、附件、类别和合规性。",
            "source": "员工提交",
            "target_role": "finance_reviewer",
            "recommended_command": "finance_approve",
            "task_prompt": f"请为财务初审准备 {expense_id} 的工作台：展示票据、附件、类别、金额和历史累计风险，并生成“通过初审/退回补充”的确认按钮，等待财务人员最终判断。",
        }
    if role == "finance_reviewer" and status in {"payable", "c_level_approved"}:
        return {
            **base,
            "todo_id": f"todo_payment_{expense_id}",
            "todo_type": "expense_payment",
            "title": f"{expense_id} 等待财务打款",
            "reason": "所有必要审批已经完成，财务可以执行打款。",
            "source": "审批链完成",
            "target_role": "finance_reviewer",
            "recommended_command": "pay_expense",
            "task_prompt": f"请为财务打款准备 {expense_id} 的工作台：展示审批链、金额和收款信息摘要，并生成打款确认按钮，等待财务人员最终确认。",
        }
    if role == "department_manager" and status == "department_approval_requested":
        return {
            **base,
            "todo_id": f"todo_department_{expense_id}",
            "todo_type": "department_expense_approval",
            "title": f"{expense_id} 等待部门经理审批",
            "reason": "财务初审已通过，需要部门经理确认业务合理性。",
            "source": "财务初审通过",
            "target_role": "department_manager",
            "recommended_command": "department_approve",
            "task_prompt": f"请为部门经理审批准备 {expense_id} 的工作台：展示业务合理性、部门预算影响和员工报销模式，并生成审批确认按钮，等待部门经理最终判断。",
        }
    if role == "c_level" and status == "c_level_approval_requested":
        return {
            **base,
            "todo_id": f"todo_c_level_{expense_id}",
            "todo_type": "c_level_expense_approval",
            "title": f"{expense_id} 等待 C-level 审批",
            "reason": "部门经理已审批，但单笔或累计金额触发高价值审批阈值。",
            "source": "部门经理审批通过",
            "target_role": "c_level",
            "recommended_command": "c_level_approve",
            "task_prompt": f"请为 C-level 高价值审批准备 {expense_id} 的工作台：展示单笔金额、月度累计、年度累计和触发原因，并生成审批确认按钮，等待 C-level 最终判断。",
        }
    if role == "employee" and status == "returned_for_more_info":
        return {
            **base,
            "todo_id": f"todo_resubmit_{expense_id}",
            "todo_type": "expense_resubmission",
            "title": f"{expense_id} 需要补充材料",
            "reason": "财务初审退回，需要员工补充说明或附件后重新提交。",
            "source": "财务退回",
            "target_role": "employee",
            "recommended_command": "resubmit_expense",
            "task_prompt": f"请帮助我补充 {expense_id} 的材料并重新提交。",
        }
    return None


def bind(cur, payload_text: str, signature: str) -> dict[str, Any]:
    cur.execute("SELECT taskbound.bind_task(%s, %s)", (payload_text, signature))
    return cur.fetchone()[0]


def fetch_state(cur) -> list[dict[str, Any]]:
    cur.execute("SELECT * FROM taskbound.inspect_task_state()")
    return rows_as_dicts(cur)


def fetch_receipts(cur) -> list[dict[str, Any]]:
    cur.execute(
        """
        SELECT decision, rows_returned, unique_rows_added,
               remaining_unique_row_budget, reason, created_at
        FROM taskbound.receipts()
        LIMIT 20
        """
    )
    return rows_as_dicts(cur)


def schema_for_payload(payload: dict[str, Any]) -> dict[str, Any]:
    allowed = payload.get("allowed_views", [])
    views = {
        name: SAFE_VIEWS[name]
        for name in allowed
        if name in SAFE_VIEWS
    }
    return {
        "task_id": payload.get("task_id"),
        "task_type": payload.get("task_type"),
        "purpose": payload.get("purpose"),
        "row_scope": payload.get("row_scope"),
        "budgets": payload.get("budgets"),
        "allowed_views": views,
        "allowed_commands": payload.get("allowed_commands", []),
        "command_policies": {
            name: COMMAND_POLICIES[name]
            for name in payload.get("allowed_commands", [])
            if name in COMMAND_POLICIES
        },
        "sql_rules": [
            "Use SELECT or WITH only.",
            "Read only from registered safe view names, not raw app_data tables.",
            "Do not request denied or not-exposed columns.",
            "Use task scope already enforced by database; do not try to bypass it.",
            "For writes, use controlled commands only.",
        ],
    }


def call_deepseek_agent(user_request: str, task_schema: dict[str, Any]) -> dict[str, Any]:
    if not DEEPSEEK_API_KEY:
        raise HTTPException(status_code=400, detail="DEEPSEEK_API_KEY is not configured")

    system_prompt = (
        "You are a product workspace agent, not a SQL console. Return only valid JSON. "
        "All user-facing JSON strings should be Simplified Chinese. "
        "Do not include markdown, comments, trailing commas, or text outside the JSON object. "
        "Your output must have keys: intent, user_facing_answer, workspace, data_plan. "
        "intent must be one of: capability_question, data_analysis_task, workflow_action_task, followup_question. "
        "workspace is a user-facing UI spec with keys: title, subtitle, sections, actions. "
        "Each section has type and title. Supported section types: text, cards, examples, metrics, table_placeholder, action_panel. "
        "Actions are user-facing buttons with label and kind. Use kind fill_prompt for examples, command for business actions, or none. "
        "Never put placeholder values like ?, TBD, to be queried, pending query, unknown, or 待查询 in user-facing metrics. "
        "If a metric needs query results, either omit its value or use a table_placeholder section instead. "
        "data_plan has key steps. steps is an array of at most 4 backend steps. Each step is either "
        "{\"type\":\"query\",\"sql\":\"...\"} or {\"type\":\"command\",\"command_name\":\"...\",\"args\":{...}}. "
        "Query steps may run automatically. Command steps are proposed business actions and require user confirmation; do not describe them as already completed. "
        "If the user asks what features/functions/capabilities this agent has or how to use it, "
        "set intent=capability_question, create a capability workspace, and set data_plan.steps=[]; do not query the database. "
        "Never expose safe views, SQL, task token, database credentials, internal command names, or implementation details in workspace. "
        "For data tasks, you may plan read queries over safe views and controlled workflow commands. "
        "Never use raw tables, mutation SQL, hidden columns, or unregistered views. "
        "In this demo, every row in the expenses safe view is already a travel reimbursement record. "
        "Do not filter category = 'Travel' or category = 'travel'. Category values are business subtypes like flight, hotel, taxi, meal, train, and equipment. "
        "If a command should act on an expense returned by the previous query, set args.expense_id to \"$last.expense_id\". "
        "Before proposing any command, inspect task_schema.command_policies. "
        "Each command policy declares entity_key, entity_view, required_hint, and hint_query_columns. "
        "First query the target entity from entity_view with hint_query_columns. "
        "Only propose a command when the row's required_hint is true. "
        "If required_hint is false, do not propose that command; explain the business state instead. "
        "The reimbursement workflow is sequential: finance_approve, department_approve, optional c_level_approve, then pay_expense. "
        "Finance may also return_expense_for_more_info, and employees may resubmit_expense. "
        "Use the current delegator's role and workflow hints to choose plausible commands. "
        "Do not add filters that are not present in safe view columns."
    )
    user_prompt = {
        "user_request": user_request,
        "available_business_capabilities": [
            "报销数据分析",
            "异常识别",
            "财务初审建议",
            "部门经理审批建议",
            "C-level 高价值审批建议",
            "审批完成后的打款检查",
        ],
        "task_schema": {
            "row_scope": task_schema.get("row_scope"),
            "budgets": task_schema.get("budgets"),
            "allowed_views": list((task_schema.get("allowed_views") or {}).keys()),
            "allowed_commands": task_schema.get("allowed_commands", []),
            "command_policies": task_schema.get("command_policies", {}),
            "domain_notes": {
                "expenses_view": "All rows are travel reimbursement records.",
                "expense_month_example": "2026-06",
                "category_examples": ["flight", "hotel", "taxi", "meal", "train", "equipment"],
                "submitted_status": "submitted",
            },
        },
        "output_shape": {
            "intent": "capability_question | data_analysis_task | workflow_action_task | followup_question",
            "user_facing_answer": "面向普通用户的一句话回答",
            "workspace": {
                "title": "工作台标题",
                "subtitle": "工作台说明",
                "sections": [
                    {"type": "cards", "title": "核心能力", "items": [{"title": "标题", "body": "说明"}]},
                    {"type": "examples", "title": "你可以这样说", "items": ["示例任务"]},
                ],
                "actions": [{"label": "按钮文案", "kind": "fill_prompt", "prompt": "要填入输入框的示例任务"}],
            },
            "data_plan": {"steps": []},
        },
        "recommended_plans": {
            "finance_initial_review": [
                {
                    "type": "query",
                    "sql": "SELECT department_name, COUNT(*) AS expense_count, SUM(amount) AS total_amount FROM expenses GROUP BY department_name ORDER BY total_amount DESC",
                },
                {
                    "type": "query",
                    "sql": "SELECT expense_id, employee_name, category, amount, status, can_finance_approve, next_required_role, next_task_type FROM expenses WHERE status IN ('submitted','resubmitted','finance_review_requested') ORDER BY amount DESC LIMIT 1",
                },
                {
                    "type": "command",
                    "command_name": "finance_approve",
                    "args": {
                        "expense_id": "$last.expense_id",
                        "supervisor_agent_id": "agent:deepseek-supervisor",
                        "comment": "Finance compliance approved after checking materials.",
                    },
                },
            ],
            "c_level_review": [
                {
                    "type": "query",
                    "sql": "SELECT expense_id, employee_name, department_name, category, amount, status, monthly_employee_total, yearly_employee_total, requires_c_level_approval, can_c_level_approve, approval_reason FROM expenses WHERE expense_id = 'exp_008'",
                },
                {
                    "type": "command",
                    "command_name": "c_level_approve",
                    "args": {
                        "expense_id": "$last.expense_id",
                        "supervisor_agent_id": "agent:deepseek-supervisor",
                        "comment": "C-level approval requested by high-value policy.",
                    },
                },
            ],
        },
    }
    last_error = ""
    for attempt in range(2):
        response = httpx.post(
            f"{DEEPSEEK_BASE_URL}/chat/completions",
            headers={
                "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "model": DEEPSEEK_MODEL,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": json.dumps(user_prompt, ensure_ascii=False)},
                ],
                "response_format": {"type": "json_object"},
                "temperature": 0,
                "max_tokens": 3200,
            },
            timeout=75,
        )
        if response.status_code >= 400:
            last_error = f"DeepSeek API error: {response.text}"
            continue
        payload = response.json()
        choice = (payload.get("choices") or [{}])[0]
        content = ((choice.get("message") or {}).get("content") or "").strip()
        if not content:
            last_error = f"DeepSeek returned empty content on attempt {attempt + 1}"
            continue
        try:
            plan = json.loads(content)
        except json.JSONDecodeError:
            last_error = f"DeepSeek returned invalid JSON on attempt {attempt + 1}: {content[:240]}"
            continue
        required = {"intent", "user_facing_answer", "workspace", "data_plan"}
        if not required.issubset(plan):
            last_error = f"DeepSeek JSON missed required keys on attempt {attempt + 1}"
            continue
        return plan
    raise HTTPException(status_code=502, detail=f"DeepSeek 没有返回有效工作台 JSON，请重试。{last_error}")


def call_deepseek_workspace_renderer(
    user_request: str,
    plan: dict[str, Any],
    execution: list[dict[str, Any]],
) -> dict[str, Any]:
    system_prompt = (
        "You are a product UI workspace renderer. Return only valid JSON. "
        "All user-facing strings must be Simplified Chinese. "
        "Do not include markdown or text outside JSON. "
        "You receive the user's task, the original agent plan, and actual execution results. "
        "Generate the final user-facing workspace from the actual results. "
        "Do not mention SQL, safe views, task tokens, database credentials, or internal implementation. "
        "Do not invent numbers. Use only values present in execution_results. "
        "Return keys: user_facing_answer, workspace. "
        "workspace has title, subtitle, sections, actions. "
        "Supported section types: metrics, table, cards, text, examples, action_panel. "
        "For table sections, include columns as [{key,label}] and rows as objects. "
        "For metrics, include items as [{label,value}]. "
        "If any execution result contains rows, you must create at least one table section using those rows. "
        "If rows include department_name plus total_amount or expense_count, create a metrics section and a ranked table section. "
        "For department summary metrics, compute total amount, total count, and top department from the rows. "
        "If an action is waiting for user confirmation, present it as an action_panel item, not as completed. "
        "If a command result has blocked_by_workflow_hints=true, explain it as the agent using workflow hints to avoid a wrong action, not as a database failure."
    )
    compact_execution = []
    for item in execution:
        result = item.get("result", {})
        planned = item.get("planned", {})
        compact_execution.append(
            {
                "step": item.get("step"),
                "type": planned.get("type"),
                "command_name": planned.get("command_name"),
                "args": planned.get("resolved_args") or planned.get("args"),
                "ok": result.get("ok"),
                "proposed": result.get("proposed", False),
                "blocked_by_workflow_hints": result.get("blocked_by_workflow_hints", False),
                "rows": (result.get("rows") or [])[:20],
                "command_result": result.get("command_result"),
                "error": result.get("error"),
            }
        )
    user_prompt = {
        "user_request": user_request,
        "initial_intent": plan.get("intent"),
        "initial_workspace": plan.get("workspace"),
        "execution_results": compact_execution,
        "output_shape": {
            "user_facing_answer": "一句话回答",
            "workspace": {
                "title": "最终工作台标题",
                "subtitle": "最终工作台说明",
                "sections": [
                    {"type": "metrics", "title": "总览", "items": [{"label": "指标", "value": "值"}]},
                    {
                        "type": "table",
                        "title": "表格标题",
                        "columns": [{"key": "field", "label": "列名"}],
                        "rows": [{"field": "value"}],
                    },
                    {"type": "text", "title": "结论", "body": "基于结果的业务解释"},
                ],
                "actions": [],
            },
        },
    }
    last_error = ""
    for attempt in range(2):
        response = httpx.post(
            f"{DEEPSEEK_BASE_URL}/chat/completions",
            headers={
                "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "model": DEEPSEEK_MODEL,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": json.dumps(user_prompt, ensure_ascii=False, default=str)},
                ],
                "response_format": {"type": "json_object"},
                "temperature": 0,
                "max_tokens": 3200,
            },
            timeout=75,
        )
        if response.status_code >= 400:
            last_error = f"DeepSeek render API error: {response.text}"
            continue
        content = (((response.json().get("choices") or [{}])[0].get("message") or {}).get("content") or "").strip()
        if not content:
            last_error = f"DeepSeek render returned empty content on attempt {attempt + 1}"
            continue
        try:
            rendered = json.loads(content)
        except json.JSONDecodeError:
            last_error = f"DeepSeek render returned invalid JSON on attempt {attempt + 1}: {content[:240]}"
            continue
        if "workspace" in rendered and "user_facing_answer" in rendered:
            return rendered
        last_error = f"DeepSeek render JSON missed required keys on attempt {attempt + 1}"
    raise HTTPException(status_code=502, detail=f"DeepSeek 没有返回有效最终工作台 JSON，请重试。{last_error}")


def resolve_command_args(args: dict[str, Any], last_rows: list[dict[str, Any]]) -> dict[str, Any]:
    resolved = dict(args or {})
    expense_id = resolved.get("expense_id")
    placeholder_values = {
        "$last.expense_id",
        "last.expense_id",
        "exp_placeholder",
        "<expense_id>",
        "{{expense_id}}",
    }
    if isinstance(expense_id, str) and expense_id in placeholder_values and last_rows:
        candidate = last_rows[0].get("expense_id")
        if candidate:
            resolved["expense_id"] = candidate
    return resolved


def workflow_hint_decision(command_name: str, args: dict[str, Any], last_rows: list[dict[str, Any]]) -> tuple[bool, str]:
    policy = COMMAND_POLICIES.get(command_name)
    if not policy:
        return True, ""
    entity_key = policy.get("entity_key")
    required_hint = policy.get("required_hint")
    if not entity_key or not required_hint:
        return True, ""
    entity_id = args.get(entity_key)
    if not entity_id:
        return True, ""
    row = next((item for item in last_rows if item.get(entity_key) == entity_id), None)
    if row is None:
        return True, ""
    if required_hint not in row:
        return True, ""
    if row.get(required_hint) is True:
        return True, ""
    status = row.get("status") or "unknown"
    amount = row.get("amount")
    amount_text = f", amount={amount}" if amount is not None else ""
    label = policy.get("label", command_name)
    already_done_statuses = set(policy.get("already_done_statuses", []))
    if status in already_done_statuses:
        return False, f"Agent checked workflow hints: {entity_id} status={status}{amount_text}; {label} should not be repeated."
    return False, (
        f"Agent checked workflow hints: {entity_id} status={status}{amount_text}; "
        f"{policy.get('blocked_message', 'the command is not currently available')} "
        f"(required hint {required_hint} is not true)."
    )


def execute_agent_plan(
    credential: AgentCredential,
    payload_text: str,
    signature: str,
    plan: dict[str, Any],
) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    last_rows: list[dict[str, Any]] = []
    data_plan = plan.get("data_plan") if isinstance(plan.get("data_plan"), dict) else {}
    steps = data_plan.get("steps", plan.get("steps", []))
    if not isinstance(steps, list):
        raise HTTPException(status_code=400, detail="DeepSeek plan.steps must be an array")
    for index, step in enumerate(steps[:4], start=1):
        step_type = step.get("type")
        if step_type == "query":
            req = AgentQueryRequest(
                credential=credential,
                payload_text=payload_text,
                signature=signature,
                sql=step.get("sql", ""),
            )
            result = agent_query(req)
            if result.get("ok") and result.get("rows"):
                last_rows = result.get("rows", []) or []
        elif step_type == "command":
            resolved_args = resolve_command_args(step.get("args", {}), last_rows)
            step = {**step, "resolved_args": resolved_args}
            allowed_by_hints, hint_reason = workflow_hint_decision(step.get("command_name", ""), resolved_args, last_rows)
            if allowed_by_hints:
                result = {
                    "ok": True,
                    "proposed": True,
                    "requires_user_confirmation": True,
                    "message": "Command was proposed by DeepSeek after workflow hint review and is waiting for user confirmation.",
                }
            else:
                result = {
                    "ok": False,
                    "proposed": False,
                    "blocked_by_workflow_hints": True,
                    "error": hint_reason,
                }
        else:
            result = {"ok": False, "error": f"unknown step type: {step_type}"}
        results.append({"step": index, "planned": step, "result": result})
    return results


@app.get("/", response_class=HTMLResponse)
def index():
    return USER_HTML


@app.post("/tasks")
def create_task(req: CreateTaskRequest):
    requested_scope = dict(req.scope or {})
    if req.department_id:
        requested_scope["department_id"] = req.department_id
    requested_budgets = dict(req.budgets or {})
    requested_budgets.setdefault("max_queries", req.max_queries)
    requested_budgets.setdefault("max_unique_expense_rows", req.max_rows)
    try:
        payload, payload_text, signature = build_task_from_template(
            task_id=req.task_id,
            task_type=req.task_type,
            delegator=req.delegator,
            actor=req.actor,
            requested_scope=requested_scope,
            requested_budgets=requested_budgets,
        )
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        "payload": payload,
        "payload_text": payload_text,
        "signature": signature,
        "curl_example": {
            "url": "/query",
            "body": {
                "payload_text": payload_text,
                "signature": signature,
                "sql": "SELECT expense_id, department_name, employee_name, amount FROM expenses ORDER BY amount DESC LIMIT 3",
            },
        },
    }


@app.post("/task-schema")
def describe_task_schema(req: QueryRequest):
    try:
      payload = json.loads(req.payload_text)
    except json.JSONDecodeError as exc:
      raise HTTPException(status_code=400, detail="payload_text is not valid JSON") from exc
    return schema_for_payload(payload)


@app.get("/admin/task-templates")
def list_task_templates():
    return {"templates": TASK_TEMPLATES}


@app.get("/admin/safe-views")
def list_safe_views():
    return {"safe_views": SAFE_VIEWS}


@app.post("/admin/task-templates")
def save_task_template(req: TaskTemplateRequest):
    if "task_type" not in req.template:
        raise HTTPException(status_code=400, detail="template.task_type is required")
    if "purpose" not in req.template:
        raise HTTPException(status_code=400, detail="template.purpose is required")
    try:
        saved = upsert_template(req.template)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True, "template": saved}


@app.get("/admin/task-grants")
def list_task_grants():
    return {"grants": TASK_GRANTS}


@app.post("/admin/task-grants")
def save_task_grant(req: TaskGrantRequest):
    if "delegator" not in req.grant:
        raise HTTPException(status_code=400, detail="grant.delegator is required")
    if "task_type" not in req.grant:
        raise HTTPException(status_code=400, detail="grant.task_type is required")
    if req.grant["task_type"] not in TASK_TEMPLATES:
        raise HTTPException(status_code=400, detail="grant.task_type does not exist")
    saved = upsert_grant(req.grant)
    return {"ok": True, "grant": saved}


@app.get("/admin", response_class=HTMLResponse)
def admin_page():
    return CONTROL_PLANE_HTML


@app.post("/todos")
def list_todos(req: TodoListRequest):
    role = user_role(req.delegator)
    with admin_connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                WITH totals AS (
                  SELECT
                    e.expense_id,
                    e.expense_month,
                    d.department_name,
                    emp.employee_name,
                    e.category,
                    e.merchant,
                    e.amount,
                    e.status,
                    sum(e.amount) OVER (
                      PARTITION BY e.tenant_id, e.employee_id, date_trunc('month', e.submitted_at)
                    ) AS monthly_employee_total,
                    sum(e.amount) OVER (
                      PARTITION BY e.tenant_id, e.employee_id, date_trunc('year', e.submitted_at)
                    ) AS yearly_employee_total
                  FROM app_data.expenses e
                  JOIN app_data.departments d ON d.department_id = e.department_id
                  JOIN app_data.employees emp ON emp.employee_id = e.employee_id
                  WHERE e.tenant_id = 'company_a'
                    AND e.expense_month = %s
                )
                SELECT *
                FROM totals
                ORDER BY
                  CASE status
                    WHEN 'submitted' THEN 1
                    WHEN 'resubmitted' THEN 2
                    WHEN 'department_approval_requested' THEN 3
                    WHEN 'c_level_approval_requested' THEN 4
                    WHEN 'payable' THEN 5
                    ELSE 9
                  END,
                  amount DESC
                """,
                (req.expense_month,),
            )
            rows = rows_as_dicts(cur)
    todos = [todo for row in rows if (todo := todo_for_row(row, role))]
    return {
        "ok": True,
        "delegator": req.delegator,
        "role": role,
        "todos": todos,
    }


@app.post("/credentials")
def issue_credential(req: CredentialRequest):
    now = datetime.now(timezone.utc)
    expires_at = now + timedelta(minutes=req.ttl_minutes)
    safe_agent = "".join(ch if ch.isalnum() else "_" for ch in req.agent_id.lower())[:40]
    db_user = f"agent_{safe_agent}_{secrets.token_hex(4)}"
    db_password = secrets.token_urlsafe(24)

    with admin_connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                psql.SQL("CREATE ROLE {} LOGIN PASSWORD {} VALID UNTIL {}")
                .format(
                    psql.Identifier(db_user),
                    psql.Literal(db_password),
                    psql.Literal(expires_at.isoformat()),
                )
            )
            cur.execute(
                psql.SQL("GRANT agent_runtime TO {}").format(psql.Identifier(db_user))
            )

    return {
        "db_host": "postgres",
        "db_port": 5432,
        "db_name": "travel",
        "db_user": db_user,
        "db_password": db_password,
        "expires_at": expires_at.isoformat(),
        "role": "agent_runtime",
        "note": "This short-lived DB credential only authenticates the agent runtime. It still needs a signed task token to access task-scoped data.",
    }


@app.post("/query")
def query(req: QueryRequest):
    with connect() as conn:
        with conn.cursor() as cur:
            try:
                bound = bind(cur, req.payload_text, req.signature)
                cur.execute("SELECT * FROM taskbound.run(%s)", (req.sql,))
                rows = [row[0] for row in cur.fetchall()]
                state = fetch_state(cur)
                receipts = fetch_receipts(cur)
                return {
                    "ok": True,
                    "bound": bound,
                    "rows": rows,
                    "state": state,
                    "receipts": receipts,
                }
            except Exception as exc:
                try:
                    state = fetch_state(cur)
                    receipts = fetch_receipts(cur)
                except Exception:
                    state = []
                    receipts = []
                return {
                    "ok": False,
                    "error": str(exc),
                    "state": state,
                    "receipts": receipts,
                }


@app.post("/agent-query")
def agent_query(req: AgentQueryRequest):
    try:
        conn = connect_with_credential(req.credential)
    except Exception as exc:
        raise HTTPException(status_code=401, detail=f"could not connect with dynamic credential: {exc}") from exc

    with conn:
        with conn.cursor() as cur:
            try:
                bound = bind(cur, req.payload_text, req.signature)
                cur.execute("SELECT * FROM taskbound.run(%s)", (req.sql,))
                rows = [row[0] for row in cur.fetchall()]
                state = fetch_state(cur)
                receipts = fetch_receipts(cur)
                return {
                    "ok": True,
                    "used_dynamic_credential": req.credential.db_user,
                    "bound": bound,
                    "rows": rows,
                    "state": state,
                    "receipts": receipts,
                }
            except Exception as exc:
                try:
                    state = fetch_state(cur)
                    receipts = fetch_receipts(cur)
                except Exception:
                    state = []
                    receipts = []
                return {
                    "ok": False,
                    "used_dynamic_credential": req.credential.db_user,
                    "error": str(exc),
                    "state": state,
                    "receipts": receipts,
                }


@app.post("/agent-command")
def agent_command(req: AgentCommandRequest):
    try:
        conn = connect_with_credential(req.credential)
    except Exception as exc:
        raise HTTPException(status_code=401, detail=f"could not connect with dynamic credential: {exc}") from exc

    with conn:
        with conn.cursor() as cur:
            try:
                bound = bind(cur, req.payload_text, req.signature)
                cur.execute(
                    "SELECT taskbound.command(%s, %s::jsonb)",
                    (req.command_name, json.dumps(req.args)),
                )
                result = cur.fetchone()[0]
                return {
                    "ok": True,
                    "used_dynamic_credential": req.credential.db_user,
                    "bound": bound,
                    "command_result": result,
                }
            except Exception as exc:
                return {
                    "ok": False,
                    "used_dynamic_credential": req.credential.db_user,
                    "error": str(exc),
                }


@app.post("/agent-chat")
def agent_chat(req: AgentChatRequest):
    credential_dict = issue_credential(
        CredentialRequest(agent_id="deepseek-agent", ttl_minutes=15)
    )
    task_dict = create_task(
        CreateTaskRequest(
            task_id=f"task_deepseek_{secrets.token_hex(4)}",
            task_type=req.task_type,
            delegator=req.delegator,
            actor="agent:deepseek-travel-analyst",
            department_id=req.department_id,
            max_rows=req.max_rows,
            max_queries=req.max_queries,
        )
    )
    credential = AgentCredential(
        db_host=credential_dict["db_host"],
        db_port=credential_dict["db_port"],
        db_name=credential_dict["db_name"],
        db_user=credential_dict["db_user"],
        db_password=credential_dict["db_password"],
    )
    task_schema = schema_for_payload(task_dict["payload"])
    plan = call_deepseek_agent(req.user_request, task_schema)
    execution = execute_agent_plan(
        credential,
        task_dict["payload_text"],
        task_dict["signature"],
        plan,
    )
    rendered = call_deepseek_workspace_renderer(req.user_request, plan, execution)
    return {
        "ok": True,
        "model": DEEPSEEK_MODEL,
        "intent": plan.get("intent", "data_analysis_task"),
        "user_facing_answer": rendered.get("user_facing_answer", plan.get("user_facing_answer", "")),
        "workspace": rendered.get("workspace", plan.get("workspace", {})),
        "credential": credential_dict,
        "task": {
            "payload": task_dict["payload"],
            "payload_text": task_dict["payload_text"],
            "signature": task_dict["signature"],
        },
        "task_schema": task_schema,
        "deepseek_plan": plan,
        "deepseek_render": rendered,
        "execution": execution,
    }


HTML = r"""
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>SessionBoundDB Travel Demo</title>
  <style>
    :root {
      color-scheme: light;
      --bg: #f4f6f8;
      --panel: #ffffff;
      --panel-soft: #f9fafb;
      --line: #d9dee7;
      --text: #162033;
      --muted: #667085;
      --accent: #0f766e;
      --accent-dark: #115e59;
      --danger: #b42318;
      --ok: #067647;
      --warn: #b54708;
      --code: #101828;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      background: var(--bg);
      color: var(--text);
      font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    }
    header {
      background: var(--panel);
      border-bottom: 1px solid var(--line);
      padding: 18px 22px 14px;
    }
    h1 { margin: 0 0 6px; font-size: 22px; letter-spacing: 0; }
    p { margin: 0; color: var(--muted); line-height: 1.45; }
    .topbar {
      display: grid;
      grid-template-columns: repeat(3, minmax(0, 1fr));
      gap: 10px;
      margin-top: 14px;
    }
    .chip {
      display: flex;
      align-items: center;
      gap: 8px;
      min-height: 38px;
      padding: 8px 10px;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: var(--panel-soft);
      color: var(--muted);
      font-size: 13px;
      overflow: hidden;
    }
    .chip strong { color: var(--text); font-weight: 650; }
    .dot {
      flex: 0 0 auto;
      width: 9px;
      height: 9px;
      border-radius: 999px;
      background: var(--muted);
    }
    .dot.ok { background: var(--ok); }
    .dot.bad { background: var(--danger); }
    .dot.warn { background: var(--warn); }
    main {
      display: grid;
      grid-template-columns: 340px minmax(360px, 1fr) minmax(360px, 1fr);
      gap: 14px;
      padding: 14px;
      align-items: start;
    }
    .stack { display: grid; gap: 14px; }
    section {
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 14px;
    }
    .section-title {
      display: flex;
      justify-content: space-between;
      align-items: baseline;
      gap: 10px;
      margin-bottom: 10px;
    }
    h2 { margin: 0; font-size: 15px; letter-spacing: 0; }
    .eyebrow {
      color: var(--muted);
      font-size: 12px;
      white-space: nowrap;
    }
    label {
      display: block;
      margin: 10px 0 5px;
      color: var(--muted);
      font-size: 12px;
    }
    input, textarea, select {
      width: 100%;
      border: 1px solid var(--line);
      border-radius: 6px;
      padding: 8px 9px;
      background: white;
      color: var(--text);
      font: inherit;
      font-size: 13px;
    }
    textarea {
      min-height: 148px;
      resize: vertical;
      font-family: ui-monospace, SFMono-Regular, Consolas, "Liberation Mono", monospace;
      line-height: 1.45;
    }
    .request-area { min-height: 356px; }
    .result-area { min-height: 610px; }
    .row { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; }
    .buttons { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 12px; }
    button {
      border: 1px solid var(--accent);
      background: var(--accent);
      color: white;
      border-radius: 6px;
      padding: 8px 10px;
      font: inherit;
      font-size: 13px;
      cursor: pointer;
    }
    button:hover { background: var(--accent-dark); }
    button.secondary {
      background: white;
      color: var(--accent);
    }
    button.secondary:hover { background: #ecfdf3; }
    button.danger {
      border-color: var(--danger);
      background: white;
      color: var(--danger);
    }
    button.danger:hover { background: #fff1f0; }
    pre {
      margin: 0;
      padding: 11px;
      overflow: auto;
      border-radius: 6px;
      background: var(--code);
      color: #f9fafb;
      font-size: 12px;
      line-height: 1.45;
      white-space: pre-wrap;
      word-break: break-word;
    }
    .mini {
      background: #f8fafc;
      color: #243044;
      border: 1px solid var(--line);
      max-height: 190px;
      min-height: 92px;
    }
    .flow {
      display: grid;
      grid-template-columns: 1fr;
      gap: 7px;
      margin-top: 10px;
      color: var(--muted);
      font-size: 12px;
    }
    .flow div {
      padding: 8px 9px;
      border: 1px solid var(--line);
      border-radius: 6px;
      background: var(--panel-soft);
    }
    .flow b { color: var(--text); }
    @media (max-width: 1180px) {
      main { grid-template-columns: 1fr; }
      .topbar { grid-template-columns: 1fr; }
      .request-area, .result-area { min-height: 260px; }
    }
  </style>
</head>
<body>
  <header>
    <h1>SessionBoundDB Travel Demo</h1>
    <p>Credential Broker authenticates the Agent runtime. Task token authorizes the work. Postgres enforces the boundary. <a href="/admin">Admin task setup</a></p>
    <div class="topbar">
      <div class="chip"><span id="taskDot" class="dot"></span><span><strong>Task</strong> <span id="taskStatus">not issued</span></span></div>
      <div class="chip"><span id="credDot" class="dot"></span><span><strong>DB Credential</strong> <span id="credStatus">not issued</span></span></div>
      <div class="chip"><span id="queryDot" class="dot"></span><span><strong>Last Query</strong> <span id="queryStatus">not run</span></span></div>
    </div>
  </header>
  <main>
    <div class="stack">
      <section>
        <div class="section-title">
          <h2>1. Task Authorization</h2>
          <span class="eyebrow">/tasks</span>
        </div>
        <label for="taskType">Task type</label>
        <input id="taskType" value="monthly_travel_expense_review" />
        <label for="delegator">Delegator user</label>
        <input id="delegator" value="user:alice" />
        <label for="department">Scope</label>
        <select id="department">
          <option value="">All departments</option>
          <option value="dep_fin">Finance</option>
          <option value="dep_sales">Sales</option>
          <option value="dep_eng">Engineering</option>
        </select>
        <p style="margin-top:6px;font-size:12px;">Scope is enforced inside Postgres views. Select Sales, issue a new task, then run Scope Test: non-Sales rows should disappear.</p>
        <div class="row">
          <div>
            <label for="maxRows">Unique rows</label>
            <input id="maxRows" type="number" value="4" min="1" max="1000" />
          </div>
          <div>
            <label for="maxQueries">Queries</label>
            <input id="maxQueries" type="number" value="5" min="1" max="100" />
          </div>
        </div>
        <div class="buttons">
          <button onclick="createTask()">Issue Task Token</button>
        </div>
        <label>Visible signed task token</label>
        <pre id="taskPreview" class="mini">No task token yet.</pre>
      </section>

      <section>
        <div class="section-title">
          <h2>2. Credential Broker</h2>
          <span class="eyebrow">/credentials</span>
        </div>
        <div class="row">
          <div>
            <label for="agentId">Agent ID</label>
            <input id="agentId" value="travel-analyst" />
          </div>
          <div>
            <label for="ttl">TTL minutes</label>
            <input id="ttl" type="number" value="15" min="1" max="60" />
          </div>
        </div>
        <div class="buttons">
          <button onclick="issueCredential()">Issue DB Password</button>
        </div>
        <label>Visible dynamic DB credential</label>
        <pre id="credPreview" class="mini">No database credential yet.</pre>
      </section>

      <section>
        <div class="section-title">
          <h2>Flow</h2>
          <span class="eyebrow">what the demo proves</span>
        </div>
        <div class="flow">
          <div><b>DB credential</b> proves the Agent runtime may connect.</div>
          <div><b>Task token</b> proves this exact travel-review task is authorized.</div>
          <div><b>Postgres</b> rejects SQL that crosses scope, fields, or budget.</div>
        </div>
      </section>

      <section>
        <div class="section-title">
          <h2>Real DeepSeek Agent</h2>
          <span class="eyebrow">/agent-chat</span>
        </div>
        <p>Calls DeepSeek with this task's safe schema. The model proposes SQL/commands; SessionBoundDB still enforces every step.</p>
        <label for="agentPrompt">Natural language request</label>
        <textarea id="agentPrompt" style="min-height:110px;">Analyze June travel reimbursement anomalies. Show department totals, then approve the small submitted expense if policy allows it. Do not pay anything yet.</textarea>
        <div class="buttons">
          <button onclick="runDeepSeekAgent()">Run DeepSeek Agent</button>
        </div>
      </section>
    </div>

    <section>
      <div class="section-title">
        <h2>3. Agent Request Builder</h2>
        <span class="eyebrow">/agent-query</span>
      </div>
      <label for="sql">SQL generated by Agent</label>
      <textarea id="sql">SELECT department_name, category, sum(amount) AS total_amount
FROM expenses
GROUP BY department_name, category
ORDER BY total_amount DESC</textarea>
      <div class="buttons">
        <button onclick="runAgentQuery()">Send Agent Request</button>
        <button class="secondary" onclick="setSql('aggregate')">Aggregate</button>
        <button class="secondary" onclick="setSql('join')">JOIN</button>
        <button class="secondary" onclick="setSql('cte')">CTE</button>
        <button class="secondary" onclick="setSql('window')">Window</button>
        <button class="secondary" onclick="setSql('having')">HAVING</button>
        <button class="secondary" onclick="setSql('subquery')">Subquery</button>
        <button class="secondary" onclick="setSql('detail')">Detail</button>
        <button class="secondary" onclick="setSql('scope')">Scope Test</button>
        <button class="secondary" onclick="setSql('page')">Pagination</button>
        <button class="danger" onclick="setSql('salary')">Salary Attack</button>
        <button class="danger" onclick="setSql('raw')">Raw Table Attack</button>
      </div>
      <div style="margin-top:16px;border-top:1px solid var(--line);padding-top:14px;">
        <div class="section-title">
          <h2>Controlled Write Commands</h2>
          <span class="eyebrow">/agent-command</span>
        </div>
        <p>System Agent may propose a workflow action, but Postgres enforces status, manager, finance review, and ledger rules.</p>
        <div class="row">
          <div>
            <label for="cmdName">Command</label>
            <select id="cmdName">
              <option value="approve_expense">approve_expense</option>
              <option value="finance_review">finance_review</option>
              <option value="pay_expense">pay_expense</option>
            </select>
          </div>
          <div>
            <label for="cmdExpense">Expense ID</label>
            <input id="cmdExpense" value="exp_002" />
          </div>
        </div>
        <label for="cmdComment">System Agent comment</label>
        <input id="cmdComment" value="Supervisor checked policy and recommends this action." />
        <div class="buttons">
          <button onclick="runAgentCommand()">Run Command</button>
          <button class="secondary" onclick="setCommand('approve_expense','exp_002')">Approve small</button>
          <button class="secondary" onclick="setCommand('pay_expense','exp_002')">Pay approved</button>
          <button class="danger" onclick="setCommand('approve_expense','exp_008')">Approve large</button>
          <button class="secondary" onclick="setCommand('finance_review','exp_008')">Finance review large</button>
          <button class="secondary" onclick="setCommand('pay_expense','exp_008')">Pay large</button>
        </div>
      </div>
      <label>Exact request the Agent sends</label>
      <pre id="requestPreview" class="request-area">Create a task and issue a DB credential to see the full Agent request.</pre>
    </section>

    <section>
      <div class="section-title">
        <h2>4. Database Response</h2>
        <span class="eyebrow">SessionBoundDB enforcement</span>
      </div>
      <pre id="out" class="result-area">Open http://localhost:8000, issue a task, issue a DB credential, then send an Agent request.</pre>
    </section>
  </main>
  <script>
    let task = null;
    let credential = null;
    let lastResponse = null;
    const examples = {
      aggregate: `SELECT department_name, category, sum(amount) AS total_amount
FROM expenses
GROUP BY department_name, category
ORDER BY total_amount DESC`,
      join: `SELECT e.department_name, emp.employee_level, count(*) AS trips, sum(e.amount) AS total_amount
FROM expenses e
JOIN employees emp ON emp.employee_id = e.employee_id
GROUP BY e.department_name, emp.employee_level
ORDER BY total_amount DESC`,
      cte: `WITH dept_totals AS (
  SELECT department_name, sum(amount) AS total_amount
  FROM expenses
  GROUP BY department_name
)
SELECT department_name, total_amount
FROM dept_totals
WHERE total_amount > 3000
ORDER BY total_amount DESC`,
      window: `SELECT expense_id, department_name, employee_name, category, amount,
       rank() OVER (PARTITION BY department_name ORDER BY amount DESC) AS dept_rank
FROM expenses
ORDER BY department_name, dept_rank`,
      having: `SELECT department_name, employee_name, sum(amount) AS employee_total
FROM expenses
GROUP BY department_name, employee_name
HAVING sum(amount) > 2000
ORDER BY employee_total DESC`,
      subquery: `SELECT department_name, employee_name, amount
FROM expenses
WHERE amount > (
  SELECT avg(amount)
  FROM expenses
)
ORDER BY amount DESC`,
      detail: `SELECT expense_id, department_name, employee_name, category, merchant, amount
FROM expenses
ORDER BY amount DESC
LIMIT 3`,
      scope: `SELECT expense_id, department_name, employee_name, category, amount
FROM expenses
WHERE department_name <> 'Sales'
ORDER BY expense_id`,
      page: `SELECT expense_id, department_name, employee_name, category, merchant, amount
FROM expenses
ORDER BY expense_id
LIMIT 3 OFFSET 3`,
      salary: `SELECT employee_name, salary FROM employees`,
      raw: `SELECT * FROM app_data.employees`
    };
    function compactTask() {
      if (!task) return null;
      return {
        task_id: task.payload.task_id,
        delegator: task.payload.delegator,
        actor: task.payload.actor,
        purpose: task.payload.purpose,
        tenant_id: task.payload.tenant_id,
        row_scope: task.payload.row_scope,
        denied_columns: task.payload.denied_columns,
        allowed_commands: task.payload.allowed_commands,
        budgets: task.payload.budgets,
        expires_at: task.payload.expires_at,
        signature: task.signature,
        payload_text: task.payload_text
      };
    }
    function compactCredential() {
      if (!credential) return null;
      return {
        db_host: credential.db_host,
        db_port: credential.db_port,
        db_name: credential.db_name,
        db_user: credential.db_user,
        db_password: credential.db_password,
        role: credential.role,
        expires_at: credential.expires_at
      };
    }
    function buildAgentRequest() {
      return {
        endpoint: "POST /agent-query",
        credential: compactCredential(),
        task_token: compactTask(),
        sql: document.getElementById('sql').value
      };
    }
    function buildAgentCommandRequest() {
      return {
        endpoint: "POST /agent-command",
        credential: compactCredential(),
        task_token: compactTask(),
        command_name: document.getElementById('cmdName').value,
        args: {
          expense_id: document.getElementById('cmdExpense').value,
          supervisor_agent_id: "agent:expense-supervisor",
          comment: document.getElementById('cmdComment').value,
          memo: "travel reimbursement payment"
        }
      };
    }
    function show(value) {
      document.getElementById('out').textContent = JSON.stringify(value, null, 2);
    }
    function refreshPreviews() {
      document.getElementById('taskPreview').textContent = task ? JSON.stringify(compactTask(), null, 2) : 'No task token yet.';
      document.getElementById('credPreview').textContent = credential ? JSON.stringify(compactCredential(), null, 2) : 'No database credential yet.';
      document.getElementById('requestPreview').textContent = JSON.stringify({
        sql_request: buildAgentRequest(),
        command_request: buildAgentCommandRequest()
      }, null, 2);
    }
    function setChip(id, statusId, text, state) {
      document.getElementById(id).className = 'dot ' + (state || '');
      document.getElementById(statusId).textContent = text;
    }
    function setSql(name) {
      document.getElementById('sql').value = examples[name];
      refreshPreviews();
    }
    function setCommand(commandName, expenseId) {
      document.getElementById('cmdName').value = commandName;
      document.getElementById('cmdExpense').value = expenseId;
      refreshPreviews();
    }
    async function createTask() {
      const body = {
        task_id: 'task_api_' + Date.now(),
        task_type: document.getElementById('taskType').value || 'monthly_travel_expense_review',
        delegator: document.getElementById('delegator').value || 'user:alice',
        department_id: document.getElementById('department').value || null,
        max_rows: Number(document.getElementById('maxRows').value),
        max_queries: Number(document.getElementById('maxQueries').value)
      };
      const res = await fetch('/tasks', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify(body)
      });
      task = await res.json();
      setChip('taskDot', 'taskStatus', task.payload.task_id, 'ok');
      refreshPreviews();
    }
    async function issueCredential() {
      const res = await fetch('/credentials', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({
          agent_id: document.getElementById('agentId').value || 'travel-analyst',
          ttl_minutes: Number(document.getElementById('ttl').value)
        })
      });
      credential = await res.json();
      setChip('credDot', 'credStatus', credential.db_user, 'ok');
      refreshPreviews();
    }
    async function runAgentQuery() {
      if (!task) await createTask();
      if (!credential) await issueCredential();
      const body = {
        credential,
        payload_text: task.payload_text,
        signature: task.signature,
        sql: document.getElementById('sql').value
      };
      document.getElementById('requestPreview').textContent = JSON.stringify({
        endpoint: "POST /agent-query",
        credential: compactCredential(),
        task_token: compactTask(),
        sql: body.sql
      }, null, 2);
      const res = await fetch('/agent-query', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify(body)
      });
      lastResponse = await res.json();
      setChip('queryDot', 'queryStatus', lastResponse.ok ? 'allowed' : 'denied', lastResponse.ok ? 'ok' : 'bad');
      show(lastResponse);
    }
    async function runAgentCommand() {
      if (!task) await createTask();
      if (!credential) await issueCredential();
      const commandRequest = buildAgentCommandRequest();
      document.getElementById('requestPreview').textContent = JSON.stringify(commandRequest, null, 2);
      const res = await fetch('/agent-command', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({
          credential,
          payload_text: task.payload_text,
          signature: task.signature,
          command_name: commandRequest.command_name,
          args: commandRequest.args
        })
      });
      lastResponse = await res.json();
      setChip('queryDot', 'queryStatus', lastResponse.ok ? 'command allowed' : 'command denied', lastResponse.ok ? 'ok' : 'bad');
      show(lastResponse);
    }
    async function runDeepSeekAgent() {
      const res = await fetch('/agent-chat', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({
          user_request: document.getElementById('agentPrompt').value,
          task_type: document.getElementById('taskType').value || 'monthly_travel_expense_review',
          delegator: document.getElementById('delegator').value || 'user:alice',
          department_id: document.getElementById('department').value || null,
          max_rows: Number(document.getElementById('maxRows').value) || 50,
          max_queries: Number(document.getElementById('maxQueries').value) || 20
        })
      });
      const data = await res.json();
      setChip('queryDot', 'queryStatus', data.ok ? 'DeepSeek agent completed' : 'DeepSeek agent failed', data.ok ? 'ok' : 'bad');
      show(data);
    }
    document.getElementById('sql').addEventListener('input', refreshPreviews);
    document.getElementById('cmdName').addEventListener('input', refreshPreviews);
    document.getElementById('cmdExpense').addEventListener('input', refreshPreviews);
    document.getElementById('cmdComment').addEventListener('input', refreshPreviews);
    refreshPreviews();
  </script>
</body>
</html>
"""


ADMIN_HTML = r"""
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>SessionBoundDB Admin</title>
  <style>
    :root {
      --bg: #f4f6f8;
      --panel: #ffffff;
      --line: #d9dee7;
      --text: #162033;
      --muted: #667085;
      --accent: #0f766e;
      --danger: #b42318;
      --code: #101828;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      background: var(--bg);
      color: var(--text);
      font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    }
    header {
      padding: 18px 22px 14px;
      background: var(--panel);
      border-bottom: 1px solid var(--line);
    }
    h1 { margin: 0 0 6px; font-size: 22px; }
    p { margin: 0; color: var(--muted); line-height: 1.45; }
    a { color: var(--accent); }
    main {
      display: grid;
      grid-template-columns: minmax(380px, 1fr) minmax(380px, 1fr);
      gap: 14px;
      padding: 14px;
    }
    section {
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 14px;
    }
    h2 { margin: 0 0 10px; font-size: 15px; }
    label {
      display: block;
      margin: 10px 0 5px;
      color: var(--muted);
      font-size: 12px;
    }
    textarea, pre {
      width: 100%;
      min-height: 430px;
      border: 1px solid var(--line);
      border-radius: 6px;
      padding: 11px;
      font-family: ui-monospace, SFMono-Regular, Consolas, "Liberation Mono", monospace;
      font-size: 12px;
      line-height: 1.45;
      white-space: pre-wrap;
      word-break: break-word;
    }
    textarea { resize: vertical; background: white; color: var(--text); }
    pre { margin: 0; background: var(--code); color: #f9fafb; overflow: auto; }
    .buttons { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 12px; }
    button {
      border: 1px solid var(--accent);
      background: var(--accent);
      color: white;
      border-radius: 6px;
      padding: 8px 10px;
      font: inherit;
      font-size: 13px;
      cursor: pointer;
    }
    button.secondary { background: white; color: var(--accent); }
    button.danger { border-color: var(--danger); color: var(--danger); background: white; }
    @media (max-width: 900px) {
      main { grid-template-columns: 1fr; }
    }
  </style>
</head>
<body>
  <header>
    <h1>SessionBoundDB Admin</h1>
    <p>Define task templates, grant them to users, then test user/agent execution on <a href="/">the demo console</a>.</p>
  </header>
  <main>
    <section>
      <h2>1. Task Template</h2>
      <p>Admin defines what a task type means: purpose, allowed views, denied columns, required scope, budgets, TTL.</p>
      <label for="template">Template JSON</label>
      <textarea id="template"></textarea>
      <div class="buttons">
        <button onclick="saveTemplate()">Save Template</button>
        <button class="secondary" onclick="loadAdminData()">Reload</button>
      </div>
    </section>
    <section>
      <h2>2. User Grant</h2>
      <p>Admin grants a task type to a user and constrains scope and budget.</p>
      <label for="grant">Grant JSON</label>
      <textarea id="grant"></textarea>
      <div class="buttons">
        <button onclick="saveGrant()">Save Grant</button>
      </div>
    </section>
    <section>
      <h2>Current Registry</h2>
      <pre id="registry">Loading...</pre>
    </section>
    <section>
      <h2>How To Test</h2>
      <pre>{
  "admin_flow": [
    "Save a task template",
    "Save a grant for user:alice",
    "Open /",
    "Set Task type and Delegator user",
    "Issue Task Token",
    "Issue DB Password",
    "Send Agent Request"
  ],
  "architecture": "Admin defines task. Admin grants task to user. User/Agent requests task token. Database enforces task."
}</pre>
    </section>
  </main>
  <script>
    const defaultTemplate = {
      task_type: "monthly_travel_expense_review",
      description: "Review monthly travel reimbursement anomalies.",
      purpose: "monthly_travel_expense_anomaly_review",
      actor: "agent:travel-expense-analyst",
      tenant_id: "company_a",
      operations: ["SELECT"],
      allowed_views: ["expenses", "departments", "employees"],
      denied_columns: [
        "employees.bank_account",
        "employees.phone",
        "employees.salary"
      ],
      required_scope: ["expense_month"],
      optional_scope: ["department_id"],
      default_scope: {
        expense_month: "2026-06"
      },
      default_budgets: {
        max_queries: 5,
        max_unique_expense_rows: 4
      },
      max_budgets: {
        max_queries: 100,
        max_unique_expense_rows: 5000
      },
      ttl_minutes: 30,
      policy_version: "travel-demo-v1"
    };
    const defaultGrant = {
      grant_id: "grant_alice_travel_review",
      delegator: "user:alice",
      task_type: "monthly_travel_expense_review",
      allowed_scope: {
        tenant_id: "company_a",
        expense_month: "2026-06",
        department_ids: ["dep_fin", "dep_sales", "dep_eng"]
      },
      budget_overrides: {
        max_queries: 30,
        max_unique_expense_rows: 5000
      }
    };
    function fmt(value) {
      return JSON.stringify(value, null, 2);
    }
    async function loadAdminData() {
      const templates = await fetch('/admin/task-templates').then(r => r.json());
      const grants = await fetch('/admin/task-grants').then(r => r.json());
      document.getElementById('registry').textContent = fmt({templates: templates.templates, grants: grants.grants});
      const firstTemplate = templates.templates.monthly_travel_expense_review || Object.values(templates.templates)[0] || defaultTemplate;
      const firstGrant = grants.grants[0] || defaultGrant;
      document.getElementById('template').value = fmt(firstTemplate);
      document.getElementById('grant').value = fmt(firstGrant);
    }
    async function saveTemplate() {
      const template = JSON.parse(document.getElementById('template').value);
      const res = await fetch('/admin/task-templates', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({template})
      });
      const data = await res.json();
      document.getElementById('registry').textContent = fmt(data);
      await loadAdminData();
    }
    async function saveGrant() {
      const grant = JSON.parse(document.getElementById('grant').value);
      const res = await fetch('/admin/task-grants', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({grant})
      });
      const data = await res.json();
      document.getElementById('registry').textContent = fmt(data);
      await loadAdminData();
    }
    loadAdminData();
  </script>
</body>
</html>
"""
