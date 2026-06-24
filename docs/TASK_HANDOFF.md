# Task Handoff Queue

SessionBoundDB needs a handoff queue for workflows that cross user or role boundaries.

The key rule:

```text
The current agent may discover the next step.
The current agent may not execute a step that belongs to another role.
It should create a handoff todo, and the target role should receive a new task token.
```

## Motivation

In the travel reimbursement demo, every submitted expense follows a sequential approval chain:

```text
employee submission
  -> finance compliance review
  -> department-manager approval
  -> optional C-level approval for high-value risk
  -> finance payment
```

A large or cumulatively risky expense such as `exp_008` reaches C-level only after finance compliance and department-manager approval.

The wrong design is:

```text
Alice's department-manager agent discovers C-level approval is required.
Alice's agent executes c_level_approve.
```

The better design is:

```text
Alice's department-manager agent discovers C-level approval is required.
Alice's agent creates a C-level approval handoff item.
Carol opens her todo inbox.
Carol's agent receives a new C-level approval task token.
Carol's agent requests c_level_approve.
SessionBoundDB enforces the command.
```

The same principle applies in the opposite direction. If finance cannot approve because evidence is missing, Fiona's agent should not edit Eve's expense as Fiona. It should create an employee handoff:

```text
Fiona's agent discovers exp_008 needs more evidence.
Fiona confirms return_expense_for_more_info.
SessionBoundDB moves the expense to returned_for_more_info.
The system creates a supplement-evidence handoff for the employee.
Employee opens their todo inbox.
Employee's agent receives a new resubmission task token.
Employee's agent helps collect evidence and requests resubmit_expense.
SessionBoundDB moves the expense to resubmitted and creates or enables the next finance-review handoff.
```

## SaaS Todo vs Agent Todo

Traditional SaaS todo items are designed for humans:

```json
{
  "id": "todo_123",
  "title": "Please review exp_008",
  "status": "open"
}
```

That is not enough for an agent. A human can click through pages, infer policy, inspect history, and decide what to do. An agent needs a structured task handoff package that says:

- what business object to inspect;
- why this work was handed off;
- which role should handle it;
- which task token should be requested;
- which safe view and fields should be queried first;
- which commands may be proposed;
- which actions require user confirmation;
- how completion should be recognized.

Agent-native todo should therefore be treated as a handoff capsule:

```text
Human todo = reminder
Agent todo = executable context package
```

## Handoff Capsule

A handoff capsule has two layers:

1. a human-readable summary for the inbox;
2. a structured agent context package for task continuation.

Human summary:

```text
exp_008 needs C-level approval
Eva Li / Engineering / equipment / 12999
Reason: single expense amount or aggregate total exceeds C-level threshold
Source: Alice's department approval
```

Agent context package:

```json
{
  "handoff_id": "todo_c_level_approval_exp_008",
  "intent": "c_level_approval_required",
  "reason": "Expense exp_008 is finance-compliant and department-approved, but requires C-level approval because of high-value policy.",
  "source": {
    "source_task_id": "task_alice_department_approval_exp_008",
    "created_by": "user:alice",
    "created_by_role": "department_manager",
    "created_by_agent": "agent:travel-expense-approver"
  },
  "target": {
    "target_role": "c_level",
    "target_user": null,
    "recommended_task_type": "c_level_expense_approval"
  },
  "business_object": {
    "type": "expense",
    "id": "exp_008",
    "display": "Eva Li / Engineering / equipment / 12999"
  },
  "context": {
    "safe_view": "expenses",
    "scope": {
      "expense_id": "exp_008"
    },
    "recommended_query": "SELECT expense_id, employee_name, department_name, category, amount, status, monthly_employee_total, yearly_employee_total, requires_c_level_approval, next_required_role, next_task_type, approval_tier, approval_reason FROM expenses WHERE expense_id = 'exp_008'",
    "important_fields": [
      "amount",
      "status",
      "monthly_employee_total",
      "yearly_employee_total",
      "requires_c_level_approval",
      "next_required_role",
      "next_task_type",
      "approval_tier",
      "approval_reason"
    ]
  },
  "allowed_next": {
    "task_type": "c_level_expense_approval",
    "commands": ["c_level_approve"],
    "requires_user_confirmation": true
  },
  "completion": {
    "success_state": "c_level_approved",
    "after_completion": "create or enable a finance payment handoff"
  },
  "audit": {
    "created_at": "2026-06-24T00:00:00Z",
    "expires_at": "2026-06-25T00:00:00Z",
    "status": "open"
  }
}
```

The user does not need to see the full capsule. The agent should receive it when the user opens or delegates the todo.

## Return-for-More-Info Capsule

Not every handoff moves work "up" to a more privileged role. Some handoffs move work back to the originator.

Example finance-to-employee capsule:

```json
{
  "handoff_id": "todo_more_info_exp_008",
  "intent": "supplement_expense_evidence",
  "reason": "Finance review needs purchase justification and attachment evidence.",
  "source": {
    "source_task_id": "task_fiona_finance_review_exp_008",
    "created_by": "user:fiona",
    "created_by_role": "finance_reviewer",
    "created_by_agent": "agent:travel-expense-reviewer"
  },
  "target": {
    "target_role": "employee",
    "target_user": "user:eve",
    "recommended_task_type": "expense_resubmission"
  },
  "business_object": {
    "type": "expense",
    "id": "exp_008",
    "display": "Eva Li / Engineering / equipment / 12999"
  },
  "context": {
    "safe_view": "expenses",
    "scope": {
      "expense_id": "exp_008"
    },
    "requested_fields": [
      "business_reason",
      "receipt_attachment",
      "manager_comment"
    ],
    "recommended_query": "SELECT expense_id, employee_name, department_name, category, amount, status, can_resubmit FROM expenses WHERE expense_id = 'exp_008'"
  },
  "allowed_next": {
    "task_type": "expense_resubmission",
    "commands": ["resubmit_expense"],
    "requires_user_confirmation": true
  },
  "completion": {
    "success_state": "resubmitted",
    "after_completion": "create or enable a finance-review handoff for the same expense"
  },
  "audit": {
    "created_at": "2026-06-24T00:00:00Z",
    "expires_at": "2026-06-30T00:00:00Z",
    "status": "open"
  }
}
```

This is why `resubmitted` should be a first-class status. The employee is not continuing the finance user's task. The employee is starting a new task round that happens to update the same business object.

## Waking the Agent

A handoff todo should wake an agent through a structured event, not by filling a chat box with a vague prompt.

Bad:

```text
User clicks todo.
UI fills prompt with "Please handle exp_008".
Agent starts from an ambiguous natural-language request.
```

Better:

```text
User clicks "Let Agent take over".
Control plane verifies the user can claim the handoff.
Control plane issues a new task token scoped to the handoff object.
Credential Broker issues a short-lived DB credential.
Agent receives a wake event containing the handoff capsule, task token, and safe schema.
Agent runs the recommended query and generates a focused workspace.
```

Suggested wake event:

```json
{
  "wake_reason": "handoff_claimed",
  "current_user": {
    "user_id": "user:fiona",
    "roles": ["finance_reviewer"]
  },
  "handoff_capsule": {
    "handoff_id": "todo_c_level_approval_exp_008",
    "intent": "c_level_approval_required",
    "business_object": {
      "type": "expense",
      "id": "exp_008"
    },
    "allowed_next": {
      "task_type": "c_level_expense_approval",
      "commands": ["c_level_approve"],
      "requires_user_confirmation": true
    }
  },
  "task_token": "signed task token",
  "credential": "short-lived database credential"
}
```

The wake event is an additional agent entry point:

```text
Natural language prompt
Todo handoff claim
Scheduled review
Alert
Webhook
Policy event
```

This is important for enterprise products. Agent-native workflows should not assume every task begins with a blank chat prompt.

Suggested API shape:

```text
GET  /handoffs?role=finance_reviewer
POST /handoffs/{handoff_id}/claim
POST /agent-handoff
```

`POST /agent-handoff` can return the same general response shape as `/agent-chat`, so the dynamic workspace renderer can be reused:

```json
{
  "ok": true,
  "handoff": "...",
  "credential": "...",
  "task": "...",
  "execution": [],
  "workspace": {}
}
```

## Minimal Stored Item

A handoff item is durable workflow state, not permission.

Suggested minimal shape:

```json
{
  "handoff_id": "todo_c_level_approval_exp_008",
  "source_task_id": "task_alice_department_approval_exp_008",
  "tenant_id": "company_a",
  "business_object_type": "expense",
  "business_object_id": "exp_008",
  "target_role": "c_level",
  "target_user": null,
  "recommended_task_type": "c_level_expense_approval",
  "recommended_command": "c_level_approve",
  "reason": "Expense amount or aggregate reimbursement total requires C-level approval.",
  "status": "open",
  "created_by": "user:alice",
  "created_at": "2026-06-24T00:00:00Z",
  "claimed_by": null,
  "claimed_at": null,
  "completed_at": null
}
```

## Permission Model

Opening a handoff does not grant authority by itself.

The control plane must still check:

- is this user in the target role?
- is this handoff still open?
- does the recommended task type exist?
- is the requested business object inside scope?
- has the task expired or been revoked?

Only then should it issue a new task token.

This depends on the role-parametric model in [ROLE_MODEL.md](ROLE_MODEL.md): a role only makes a user eligible to request a task token. The task token still defines the actual database capability.

Example C-level approval task:

```json
{
  "task_type": "c_level_expense_approval",
  "delegator": "user:carol",
  "scope": {
    "expense_id": "exp_008"
  },
  "allowed_views": ["expenses", "approval_events"],
  "allowed_commands": ["c_level_approve"]
}
```

## Commands

The travel reimbursement workflow should separate "routing work" from "performing work":

```text
request_finance_review
  employee or system can route a submitted/resubmitted expense to finance compliance review

finance_approve
  finance reviewer can approve compliance and create the department-manager approval handoff

department_approve
  department manager can approve ordinary business reasonableness and route high-value items to C-level

c_level_approve
  C-level approver can approve high-value business risk

return_expense_for_more_info
  finance reviewer can return the item and create an employee handoff

resubmit_expense
  employee can supplement evidence and create or enable a new finance-review round
```

This avoids giving the first user's agent excessive authority.

## Product Behavior

For the initiating user:

```text
Agent: exp_008 is department-approved but requires C-level approval because of single-amount or aggregate reimbursement risk. I created a C-level approval todo.
```

For the C-level approver:

```text
Todo Inbox:
- exp_008 needs C-level approval
  Eva Li / Engineering / equipment / 12999
  Reason: amount or aggregate reimbursement total exceeds business approval threshold
  Source: Fiona's finance compliance review
```

After the C-level approver opens the todo:

```text
Agent: I received a C-level approval handoff capsule for exp_008.
Agent: I will request a c_level_expense_approval task token scoped to exp_008.
Agent: I will query amount, monthly total, yearly total, tier, and reason.
Suggested action: c_level_approve exp_008, waiting for Carol's confirmation.
```

The inbox is still for humans. The capsule is for agents.

For the employee after finance returns the item:

```text
Todo Inbox:
- exp_008 needs more information
  Apple Store / equipment / 12999
  Reason: finance needs purchase justification and receipt evidence
  Source: Fiona's finance review
```

After the employee opens the todo:

```text
Agent: I received a supplement-evidence handoff capsule for exp_008.
Agent: I will request an expense_resubmission task token scoped to exp_008.
Agent: I will generate a small workspace for justification, attachment status, and resubmission.
Suggested action: resubmit_expense exp_008, waiting for Eve's confirmation.
```

## Design Principle

```text
One confirmation, one state transition, one authorized role.
```

Agents may continue a workflow by creating handoffs, but each high-value state transition should be performed under the correct user's task token.

The business object may move through several task rounds:

```text
submitted
finance_review_requested
returned_for_more_info
resubmitted
finance_review_requested
finance_compliant
department_approval_requested
department_approved
c_level_approval_requested
c_level_approved
paid
```

The repeated `finance_review_requested` state is acceptable because it belongs to a new round. The audit trail and handoff capsules explain how the object arrived there.
