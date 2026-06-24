# Role-Parametric Workflow Model

The travel reimbursement demo should stay small, but the framework must not be limited to three demo roles or any fixed role set.

The principle:

```text
Demo roles are seed data for clarity.
Framework roles are registry-defined and extensible.
```

## Core Rule

Roles do not grant database access directly.

Roles determine which task tokens a user is eligible to request:

```text
user -> role membership -> task grant -> signed task token -> database-enforced capability
```

The database never receives a broad "finance user can do finance things" permission. It receives a concrete task token with scope, views, commands, budgets, and TTL.

## Demo Role Model

The first demo should instantiate four visible identities:

| Role | Example User | Purpose | Typical Commands |
|---|---|---|---|
| `employee` | `user:eve` | Submit, track, and supplement own expenses | `submit_expense`, `resubmit_expense` |
| `finance_reviewer` | `user:fiona` | Perform compliance review, request missing evidence, and payment actions | `finance_approve`, `return_expense_for_more_info`, `pay_expense` |
| `department_manager` | `user:alice` | Perform first business approval after finance compliance | `department_approve` |
| `c_level` | `user:carol` | Perform high-value approval for single-amount or aggregate reimbursement risk | `c_level_approve` |

The demo intentionally stops at these four identities. It should not add accountant, cashier, budget owner, legal, procurement, or CEO-specific variants yet, but this is a demo choice, not a framework limit.

The framework must support adding roles without changing runtime code:

```text
role registry -> task template registry -> command policy registry -> handoff policy registry
```

Adding `budget_owner`, `cashier`, `accountant`, or another approval role should mean adding registry records, task grants, safe-view hints, and controlled commands. It should not require adding role-specific branches to the SessionBoundDB runtime.

The framework should still model approval routing generically:

```text
next_required_role = finance_reviewer | department_manager | c_level | budget_owner | director | ...
approval_tier = 1 | 2 | 3 | ...
approval_reason = single_amount_over_limit | monthly_total_over_limit | yearly_total_over_limit | budget_exception | ...
```

In the first demo, the sequential chain is fixed for clarity:

```text
employee -> finance_reviewer -> department_manager -> optional c_level -> finance_reviewer
```

The framework should still express this through policy data. A different company might insert budget owner, accountant, cashier, or legal review into the same pattern without changing the runtime.

This is enough to demonstrate the high-value SaaS loop:

```text
submitted
  -> finance_review_requested  every submitted expense enters finance compliance review
  -> finance_compliant         finance approves compliance
  -> department_approval_requested
  -> department_approved       department manager approves business reasonableness
  -> c_level_approval_requested  if single-amount or aggregate risk is high
  -> c_level_approved            C-level approves high-value risk
  -> payable
  -> paid                        finance pays after all required approvals
```

If no C-level approval is required, the chain skips directly from `department_approved` to `payable`.

The demo should also include the common rejection-and-resubmission branch:

```text
finance_review_requested
  -> returned_for_more_info     finance reviewer asks employee for evidence
  -> resubmitted                employee supplies evidence in a new task round
  -> finance_review_requested   finance review is requested again
```

This matches a more realistic reimbursement split:

```text
Finance owns compliance:
  invoice validity, category, tax/accounting requirements, attachments, payment readiness

Department manager owns ordinary business reasonableness:
  team necessity, department budget, employee reimbursement pattern

C-level owns high-value business risk:
  large single expenses, monthly/yearly employee totals over high threshold, exceptional spend
```

Amount is not primarily a finance privilege boundary. It is a business-risk signal. Every expense still receives department-manager approval after finance compliance. Large single expenses or aggregate reimbursement totals additionally require C-level approval. The target roles and thresholds should be policy data, not framework code.

`resubmitted` is intentionally separate from `submitted`.

The first submission and the resubmission are different task rounds. When finance returns an expense for more information, the finance-review task has reached a terminal branch for that round. The employee's later update is a new employee task, and the following finance review is a new finance task. Collapsing the state back to `submitted` would hide that history and weaken the handoff model.

## Task Rounds

SessionBoundDB should distinguish business object state from task execution state:

```text
Business object:
  expense exp_008

Task rounds:
  employee submit
  finance review
  department manager review
  C-level review, only when required
  employee resubmit
  finance re-review
  payment
```

A task round has its own delegator, task token, credential, query budget, allowed commands, receipts, and completion state. The business object may survive across many task rounds.

This matters for SaaS replacement because many high-value workflows are not single-step CRUD. They are loops:

```text
review -> return -> supplement -> review again -> approve -> pay
```

The framework should model those loops as explicit state transitions and handoffs, not as hidden application controller logic.

The demo should not expose CEO, director, budget owner, cashier, accountant, or controller as separate visible users yet. Those are important real-world extensions, but they would make the first demo harder to understand. The framework model must still support them as registry-defined roles.

## Framework Extension Model

Additional roles should be added through registries, not framework code.

### Role Registry

```json
{
  "role": "budget_owner",
  "users": ["user:brenda"],
  "description": "Can review expenses that exceed budget policy",
  "inherits": [],
  "constraints": {
    "tenant_id": "company_a",
    "business_units": ["engineering", "finance"]
  }
}
```

Another example:

```json
{
  "role": "c_level",
  "users": ["user:carol"],
  "description": "Can approve high-value expenses and strategic exceptions",
  "inherits": [],
  "constraints": {
    "tenant_id": "company_a"
  }
}
```

### Task Template Registry

```json
{
  "task_type": "budget_exception_review",
  "allowed_roles": ["budget_owner"],
  "allowed_views": ["expenses", "budget_status"],
  "allowed_commands": ["approve_budget_exception"],
  "required_scope": ["expense_id"],
  "default_budgets": {
    "max_queries": 20,
    "max_unique_expense_rows": 1
  }
}
```

### Command Policy Registry

```json
{
  "approve_budget_exception": {
    "entity_view": "expenses",
    "entity_key": "expense_id",
    "required_hint": "requires_budget_review",
    "blocked_message": "Budget exception review is not required for this expense."
  }
}
```

### Handoff Policy Registry

```json
{
  "handoff_type": "request_budget_review",
  "source_roles": ["department_manager", "finance_reviewer"],
  "target_role": "budget_owner",
  "business_object_type": "expense",
  "business_object_key": "expense_id",
  "recommended_task_type": "budget_exception_review",
  "recommended_command": "approve_budget_exception",
  "required_hint": "requires_budget_review"
}
```

### Approval Chain Registry

Approval chains should be data, not code:

```json
[
  {
    "policy_id": "finance_first",
    "business_object_type": "expense",
    "from_status": "submitted",
    "next_status": "finance_review_requested",
    "next_role": "finance_reviewer",
    "reason": "finance_compliance_required"
  },
  {
    "policy_id": "department_after_finance",
    "business_object_type": "expense",
    "from_status": "finance_compliant",
    "next_status": "department_approval_requested",
    "next_role": "department_manager",
    "reason": "department_approval_required"
  },
  {
    "policy_id": "c_level_high_value",
    "business_object_type": "expense",
    "from_status": "department_approved",
    "condition": "claim_amount > 50000 OR monthly_employee_total > 100000 OR yearly_employee_total > 300000",
    "next_status": "c_level_approval_requested",
    "next_role": "c_level",
    "approval_tier": 2,
    "reason": "high_value_business_risk"
  },
  {
    "policy_id": "payable_after_required_approvals",
    "business_object_type": "expense",
    "from_status": "department_approved",
    "condition": "requires_c_level_approval = false",
    "next_status": "payable",
    "next_role": "finance_reviewer",
    "reason": "ready_for_payment"
  }
]
```

The safe view can expose the resolved routing decision:

```text
next_required_role = "c_level"
next_task_type = "c_level_expense_approval"
approval_tier = 2
approval_reason = "high_value_business_risk"
```

The handoff queue then routes to `next_required_role`. The command implementation verifies the same policy before accepting the next controlled command.

### State Transition Registry

The first prototype keeps state transitions inside controlled commands. A production framework can externalize them:

```json
{
  "business_object_type": "expense",
  "from_status": "payable",
  "command": "release_payment",
  "to_status": "payment_released",
  "allowed_roles": ["cashier"]
}
```

The reimbursement demo can be expressed with the same pattern:

```json
[
  {
    "business_object_type": "expense",
    "from_status": "submitted",
    "command": "request_finance_review",
    "to_status": "finance_review_requested",
    "allowed_roles": ["employee", "finance_reviewer"],
    "handoff_target_role": "finance_reviewer"
  },
  {
    "business_object_type": "expense",
    "from_status": "finance_review_requested",
    "command": "finance_approve",
    "to_status": "finance_compliant",
    "allowed_roles": ["finance_reviewer"],
    "handoff_target_role": "department_manager"
  },
  {
    "business_object_type": "expense",
    "from_status": "finance_review_requested",
    "command": "return_expense_for_more_info",
    "to_status": "returned_for_more_info",
    "allowed_roles": ["finance_reviewer"],
    "handoff_target_role": "employee"
  },
  {
    "business_object_type": "expense",
    "from_status": "returned_for_more_info",
    "command": "resubmit_expense",
    "to_status": "resubmitted",
    "allowed_roles": ["employee"],
    "handoff_target_role": "finance_reviewer"
  },
  {
    "business_object_type": "expense",
    "from_status": "resubmitted",
    "command": "request_finance_review",
    "to_status": "finance_review_requested",
    "allowed_roles": ["employee", "finance_reviewer"],
    "handoff_target_role": "finance_reviewer"
  },
  {
    "business_object_type": "expense",
    "from_status": "department_approval_requested",
    "command": "department_approve",
    "to_status": "department_approved",
    "allowed_roles": ["department_manager"],
    "handoff_target_role": "${next_required_role}"
  },
  {
    "business_object_type": "expense",
    "from_status": "c_level_approval_requested",
    "command": "c_level_approve",
    "to_status": "c_level_approved",
    "allowed_roles": ["c_level"],
    "handoff_target_role": "finance_reviewer"
  }
]
```

The exact roles are domain policy. The framework rule is more important:

```text
Every high-value transition should be named, scoped, auditable, and tied to a task round.
```

## Future Enterprise Roles

The framework should be able to support roles such as:

- `director`
- `ceo`
- `budget_owner`
- `accountant`
- `cashier`
- `controller`
- `internal_auditor`

They should be modeled by adding:

1. safe view hints;
2. command policies;
3. task templates;
4. grants;
5. optional handoff policies;
6. controlled command implementations.

The core SessionBoundDB runtime should not need role-specific branches.

## Product Implication

The demo UI may show three identities for clarity:

```text
Employee Eve
Manager Alice
Finance Fiona
```

This identity switch is not a data filter. It represents the current authenticated user and therefore determines which task tokens can be requested and which todo items are visible.

The fixed "department filter" should not be the primary entry point. Scope should come from:

- the user's natural-language task;
- the user's grants;
- a dynamic clarification component generated by the agent;
- the selected todo item.

## Design Principle

```text
A role grants eligibility to request a task.
A task grants bounded database capability.
A command performs one controlled state transition.
A handoff transfers work to another eligible role.
```
