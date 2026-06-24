USER_HTML = r"""
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>SessionBound Task-to-Session Demo</title>
  <style>
    :root {
      --bg: #f5f7fa;
      --panel: #ffffff;
      --soft: #f8fafc;
      --line: #d9dee7;
      --text: #172033;
      --muted: #667085;
      --accent: #0f766e;
      --accent-soft: #e8f6f3;
      --danger: #b42318;
      --warn: #b54708;
      --warn-soft: #fff7ed;
      --ok: #067647;
      --ok-soft: #ecfdf3;
      --locked: #98a2b3;
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
      display: flex;
      align-items: flex-start;
      justify-content: space-between;
      gap: 20px;
      padding: 18px 24px;
      background: var(--panel);
      border-bottom: 1px solid var(--line);
    }
    h1, h2, h3, p { margin: 0; }
    h1 { font-size: 24px; letter-spacing: 0; }
    h2 { font-size: 17px; letter-spacing: 0; }
    h3 { font-size: 14px; letter-spacing: 0; }
    p { color: var(--muted); line-height: 1.55; }
    a { color: var(--accent); text-decoration: none; }
    a:hover { text-decoration: underline; }
    main {
      display: grid;
      gap: 16px;
      padding: 18px 22px 24px;
      max-width: 1480px;
      margin: 0 auto;
    }
    section {
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 14px;
    }
    textarea, select, input {
      width: 100%;
      border: 1px solid var(--line);
      border-radius: 6px;
      padding: 9px 10px;
      background: white;
      color: var(--text);
      font: inherit;
      font-size: 14px;
    }
    textarea {
      min-height: 104px;
      resize: vertical;
      line-height: 1.5;
    }
    button {
      border: 1px solid var(--accent);
      background: var(--accent);
      color: white;
      border-radius: 6px;
      padding: 9px 12px;
      font: inherit;
      font-size: 13px;
      cursor: pointer;
      min-height: 38px;
    }
    button:disabled { opacity: .55; cursor: not-allowed; }
    button.secondary { background: white; color: var(--accent); }
    button.danger { background: white; border-color: var(--danger); color: var(--danger); }
    .hero-sub { margin-top: 5px; max-width: 900px; }
    .top-actions, .buttons, .chips {
      display: flex;
      align-items: center;
      gap: 8px;
      flex-wrap: wrap;
    }
    .pill {
      display: inline-flex;
      align-items: center;
      min-height: 28px;
      padding: 5px 8px;
      border-radius: 999px;
      background: var(--accent-soft);
      color: var(--accent);
      font-size: 12px;
      white-space: nowrap;
    }
    .pill.ok { background: var(--ok-soft); color: var(--ok); }
    .pill.warn { background: var(--warn-soft); color: var(--warn); }
    .pill.locked { background: #f2f4f7; color: var(--locked); }
    .stepper {
      display: grid;
      grid-template-columns: repeat(5, minmax(0, 1fr));
      gap: 8px;
    }
    .step {
      border: 1px solid var(--line);
      border-radius: 8px;
      background: var(--panel);
      padding: 10px;
      min-height: 70px;
    }
    .step strong { display: block; margin-bottom: 5px; font-size: 13px; }
    .step span { color: var(--muted); font-size: 12px; }
    .step.done { border-color: #a6f4c5; background: var(--ok-soft); }
    .step.active { border-color: #5eead4; background: var(--accent-soft); }
    .step.locked { color: var(--locked); background: #f8fafc; }
    .grid-main {
      display: grid;
      grid-template-columns: minmax(0, 1.05fr) minmax(360px, .95fr);
      gap: 16px;
      align-items: start;
    }
    .grid-2 {
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 10px;
    }
    .grid-3 {
      display: grid;
      grid-template-columns: repeat(3, minmax(0, 1fr));
      gap: 10px;
    }
    .section-title {
      display: flex;
      align-items: baseline;
      justify-content: space-between;
      gap: 12px;
      margin-bottom: 12px;
    }
    .eyebrow {
      color: var(--muted);
      font-size: 12px;
      white-space: nowrap;
    }
    .field { display: grid; gap: 5px; }
    .field label { color: var(--muted); font-size: 12px; }
    .card-list { display: grid; gap: 10px; }
    .mini-card {
      border: 1px solid var(--line);
      border-radius: 8px;
      background: var(--soft);
      padding: 11px;
    }
    .mini-card strong { display: block; margin-bottom: 6px; }
    .kv {
      display: grid;
      grid-template-columns: 140px minmax(0, 1fr);
      gap: 6px 10px;
      font-size: 13px;
    }
    .kv span:nth-child(odd) { color: var(--muted); }
    .mono {
      font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, "Liberation Mono", monospace;
      font-size: 12px;
    }
    .sql-box {
      min-height: 170px;
      font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, "Liberation Mono", monospace;
      font-size: 13px;
    }
    .query-layout {
      display: grid;
      grid-template-columns: minmax(0, 1fr) 250px;
      gap: 12px;
      align-items: start;
    }
    .preset-list { display: grid; gap: 8px; }
    .preset-list button { width: 100%; text-align: left; }
    .meter {
      height: 8px;
      border-radius: 999px;
      background: #eaecf0;
      overflow: hidden;
      margin-top: 7px;
    }
    .meter > span {
      display: block;
      height: 100%;
      width: 0%;
      background: var(--accent);
    }
    .result-grid {
      display: grid;
      grid-template-columns: minmax(0, 1fr) 360px;
      gap: 16px;
      align-items: start;
    }
    table {
      width: 100%;
      border-collapse: collapse;
      font-size: 13px;
    }
    th, td {
      text-align: left;
      padding: 8px;
      border-bottom: 1px solid var(--line);
      vertical-align: top;
    }
    th {
      color: var(--muted);
      font-size: 12px;
      background: white;
    }
    pre {
      margin: 0;
      max-height: 360px;
      overflow: auto;
      white-space: pre-wrap;
      word-break: break-word;
      font-size: 12px;
      line-height: 1.45;
      color: #f9fafb;
      background: var(--code);
      border-radius: 6px;
      padding: 11px;
    }
    .status-ok { color: var(--ok); font-weight: 650; }
    .status-bad { color: var(--danger); font-weight: 650; }
    .hidden { display: none; }
    @media (max-width: 1000px) {
      header, .grid-main, .query-layout, .result-grid { grid-template-columns: 1fr; }
      header { display: grid; }
      .stepper { grid-template-columns: 1fr; }
      .grid-2, .grid-3 { grid-template-columns: 1fr; }
    }
  </style>
</head>
<body>
  <header>
    <div>
      <h1>SessionBound Enterprise Analysis Demo</h1>
      <p class="hero-sub">Approve an enterprise analysis task, issue a budgeted database session, and let the agent explore safely inside the approved boundary.</p>
    </div>
    <div class="top-actions">
      <span class="pill">SessionBound Demo</span>
      <span id="sessionStatus" class="pill locked">No approved session</span>
      <a href="/admin">Policy Console</a>
    </div>
  </header>

  <main>
    <section>
      <div class="stepper" id="stepper"></div>
    </section>

    <div class="grid-main">
      <div class="card-list">
        <section>
          <div class="section-title">
            <h2>Task Template</h2>
            <span class="eyebrow">Control Plane</span>
          </div>
          <div class="kv">
            <span>Template</span><strong>Monthly Travel Expense Analysis</strong>
            <span>Purpose</span><span>internal analytical review</span>
            <span>Safe views</span><span class="mono">expenses, departments, employees, approval_events, ledger_entries</span>
            <span>Denied fields</span><span class="mono">salary, bank_account, phone, identity_number</span>
            <span>Default scope</span><span>tenant + expense month + department</span>
            <span>Default TTL</span><span>15 minutes</span>
            <span>Default budget</span><span>20 queries / 5000 result rows / weighted disclosure budget</span>
          </div>
        </section>

        <section>
          <div class="section-title">
            <h2>Task Application</h2>
            <span class="eyebrow">Request a bounded analytical database session</span>
          </div>
          <div class="grid-3">
            <div class="field">
              <label for="requester">Requested by</label>
              <select id="requester">
                <option value="user:alice">Alice, Department Manager</option>
                <option value="user:fiona">Fiona, Finance Reviewer</option>
                <option value="user:carol">Carol, C-level Reviewer</option>
                <option value="user:eve">Eve, Employee</option>
              </select>
            </div>
            <div class="field">
              <label for="department">Department scope</label>
              <select id="department">
                <option value="dep_eng">Engineering</option>
                <option value="dep_sales">Sales</option>
                <option value="dep_fin">Finance</option>
                <option value="">All approved departments</option>
              </select>
            </div>
            <div class="field">
              <label for="expenseMonth">Expense month</label>
              <input id="expenseMonth" value="2026-06" readonly />
            </div>
          </div>
          <div class="field" style="margin-top:10px;">
            <label for="businessQuestion">Business question / reason</label>
            <textarea id="businessQuestion">Review June 2026 travel reimbursements for Engineering. Find unusual merchants, repeated claims, high-value reimbursement risk, and aggregate totals. Exclude salary, bank account, phone, identity number, private notes, and credential material.</textarea>
          </div>
          <div class="grid-3" style="margin-top:10px;">
            <div class="field">
              <label for="maxQueries">Requested query budget</label>
              <input id="maxQueries" type="number" min="1" max="100" value="20" />
            </div>
            <div class="field">
              <label for="maxRows">Requested result-row budget</label>
              <input id="maxRows" type="number" min="1" max="5000" value="5000" />
            </div>
            <div class="field">
              <label>Token and credential TTL</label>
              <input value="15 minutes credential / 30 minutes task token" readonly />
            </div>
          </div>
          <div class="buttons" style="margin-top:12px;">
            <button id="applyBtn" onclick="submitApplication()">Submit Task Application</button>
            <button class="secondary" onclick="loadExample('anomaly')">Expense Anomaly Review</button>
            <button class="secondary" onclick="loadExample('risk')">High-value Risk Analysis</button>
          </div>
        </section>
      </div>

      <div class="card-list">
        <section>
          <div class="section-title">
            <h2>Task Applications</h2>
            <span class="eyebrow">Demo control plane</span>
          </div>
          <div id="applicationCard" class="mini-card">
            <strong>No task application submitted</strong>
            <p>Submit a task application to request a short-lived, budgeted database session.</p>
          </div>
        </section>

        <section>
          <div class="section-title">
            <h2>Budgeted Database Session</h2>
            <span class="eyebrow">Runtime</span>
          </div>
          <div id="sessionCard" class="mini-card">
            <strong>No approved database session yet</strong>
            <p>Submit or approve a task application to issue a short-lived, budgeted database session. The agent can analyze data only after SessionBoundDB binds the approved task token.</p>
          </div>
        </section>
      </div>
    </div>

    <section>
      <div class="section-title">
        <h2>Agent Analysis Workspace</h2>
        <span class="eyebrow">Analysis inside approved session</span>
      </div>
      <div class="query-layout">
        <div class="field">
          <label for="sqlInput">Agent-generated SQL</label>
          <textarea id="sqlInput" class="sql-box" disabled></textarea>
          <div class="buttons" style="margin-top:10px;">
            <button id="runQueryBtn" onclick="runQuery()" disabled>Run SQL in SessionBoundDB</button>
            <button class="secondary" onclick="resetDemo()">Reset Demo State</button>
          </div>
        </div>
        <div class="preset-list">
          <button class="secondary" onclick="loadSql('variance')" disabled>Department Expense Variance</button>
          <button class="secondary" onclick="loadSql('risk')" disabled>Reimbursement Risk Review</button>
          <button class="secondary" onclick="loadSql('salary')" disabled>Denied Field Probe</button>
          <button class="secondary" onclick="loadSql('raw')" disabled>Raw Table Probe</button>
          <button class="secondary" onclick="loadSql('payload')" disabled>Payload Aggregation Probe</button>
        </div>
      </div>
    </section>

    <div class="result-grid">
      <section>
        <div class="section-title">
          <h2>Query Result</h2>
          <span id="decisionBadge" class="eyebrow">Locked until session issued</span>
        </div>
        <div id="resultPanel" class="mini-card">
          <strong>Database-enforced boundary</strong>
          <p>The agent may generate SQL freely, but SessionBoundDB decides whether the query is inside the approved task.</p>
        </div>
      </section>

      <section>
        <div class="section-title">
          <h2>Budgets and Receipts</h2>
          <span class="eyebrow">Receipt</span>
        </div>
        <div id="budgetPanel" class="card-list"></div>
        <div id="receiptPanel" style="margin-top:10px;">
          <pre>{
  "status": "no query receipt yet"
}</pre>
        </div>
      </section>
    </div>
  </main>

  <script>
    const steps = ["Apply", "Approve", "Issue Session", "Analyze", "Receipt"];
    const safeViews = ["expenses", "departments", "employees", "approval_events", "ledger_entries"];
    const deniedFields = ["salary", "bank_account", "phone", "identity_number"];
    const state = {
      phase: "apply",
      application: null,
      credential: null,
      task: null,
      issuedAt: null,
      maxQueries: 20,
      maxRows: 5000,
      queryRuns: 0,
      rowsReturned: 0,
      latestResult: null
    };
    const sqlPresets = {
      variance: `SELECT department_name,
       count(*) AS expense_count,
       sum(amount) AS total_amount,
       avg(amount) AS average_amount
FROM expenses
GROUP BY department_name
ORDER BY total_amount DESC;`,
      risk: `SELECT expense_id,
       department_name,
       employee_name,
       merchant,
       amount,
       monthly_employee_total,
       approval_reason
FROM expenses
WHERE amount >= 1000
   OR monthly_employee_total >= 2000
ORDER BY amount DESC
LIMIT 10;`,
      salary: `SELECT employee_name, salary
FROM employees;`,
      raw: `SELECT *
FROM app_data.expenses
LIMIT 5;`,
      payload: `SELECT json_agg(expense_id)
FROM expenses;`
    };

    function html(value) {
      return String(value ?? "").replace(/[&<>"']/g, ch => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[ch]));
    }
    function money(value) {
      if (value === null || value === undefined || value === "") return "";
      return Number(value).toLocaleString("en-US", {style: "currency", currency: "USD", maximumFractionDigits: 0});
    }
    function currentStepIndex() {
      return {apply: 0, approve: 1, issued: 2, analyze: 3, receipt: 4, expired: 4}[state.phase] ?? 0;
    }
    function setStatus(text, cls = "locked") {
      const el = document.getElementById("sessionStatus");
      el.textContent = text;
      el.className = `pill ${cls}`;
    }
    function renderStepper() {
      const active = currentStepIndex();
      document.getElementById("stepper").innerHTML = steps.map((step, index) => {
        const cls = index < active ? "done" : (index === active ? "active" : "locked");
        const label = index < active ? "done" : (index === active ? "active" : "pending");
        return `<div class="step ${cls}"><strong>${index + 1}. ${step}</strong><span>${label}</span></div>`;
      }).join("");
    }
    function renderBudgetPanel() {
      const queryPct = Math.min(100, (state.queryRuns / state.maxQueries) * 100);
      const rowPct = Math.min(100, (state.rowsReturned / state.maxRows) * 100);
      document.getElementById("budgetPanel").innerHTML = `
        <div class="mini-card">
          <strong>Query budget</strong>
          <span class="eyebrow">${state.queryRuns} / ${state.maxQueries} used</span>
          <div class="meter"><span style="width:${queryPct}%"></span></div>
        </div>
        <div class="mini-card">
          <strong>Result-row budget</strong>
          <span class="eyebrow">${state.rowsReturned} / ${state.maxRows} observed in this UI</span>
          <div class="meter"><span style="width:${rowPct}%"></span></div>
        </div>
        <div class="mini-card">
          <strong>Weighted disclosure budget</strong>
          <span class="eyebrow">enforced by SessionBoundDB runtime</span>
          <div class="meter"><span style="width:${state.latestResult ? 8 : 0}%"></span></div>
        </div>
      `;
    }
    function renderApplication() {
      const el = document.getElementById("applicationCard");
      if (!state.application) {
        el.innerHTML = `<strong>No task application submitted</strong><p>Submit a task application to request a short-lived, budgeted database session.</p>`;
        return;
      }
      const app = state.application;
      const approveButton = state.phase === "approve"
        ? `<button onclick="approveSession()">Approve Session</button><button class="danger" onclick="denyApplication()">Deny</button>`
        : "";
      el.innerHTML = `
        <div class="chips"><span class="pill warn">${html(app.status)}</span><span class="pill">Control Plane</span></div>
        <div class="kv" style="margin-top:10px;">
          <span>Task type</span><strong>monthly_travel_expense_review</strong>
          <span>Requester</span><span>${html(app.requester)}</span>
          <span>Scope</span><span>${html(app.scope)}</span>
          <span>Safe views</span><span class="mono">${safeViews.join(", ")}</span>
          <span>Denied fields</span><span class="mono">${deniedFields.join(", ")}</span>
          <span>Budget</span><span>${app.maxQueries} queries / ${app.maxRows} result rows</span>
          <span>TTL</span><span>15 minute credential / 30 minute task token</span>
        </div>
        <p style="margin-top:10px;">${html(app.question)}</p>
        <div class="buttons" style="margin-top:12px;">${approveButton}</div>
      `;
    }
    function renderSession() {
      const el = document.getElementById("sessionCard");
      if (!state.task || !state.credential) {
        el.innerHTML = `
          <strong>No approved database session yet</strong>
          <p>Submit or approve a task application to issue a short-lived, budgeted database session. The agent can analyze data only after SessionBoundDB binds the approved task token.</p>
        `;
        return;
      }
      const payload = state.task.payload || {};
      el.innerHTML = `
        <div class="chips"><span class="pill ok">Budgeted session active</span><span class="pill">Signed task token issued</span></div>
        <div class="kv" style="margin-top:10px;">
          <span>Session ID</span><span class="mono">${html(state.credential.db_user)}</span>
          <span>Task ID</span><span class="mono">${html(payload.task_id || "task_june_expense_analysis")}</span>
          <span>Actor</span><span>${html(payload.actor || "agent:travel-expense-analyst")}</span>
          <span>Credential TTL</span><span>${html(state.credential.expires_at || "15 minutes")}</span>
          <span>Allowed views</span><span class="mono">${html((payload.allowed_views || safeViews).join(", "))}</span>
          <span>Denied fields</span><span class="mono">${html((payload.denied_columns || deniedFields).join(", "))}</span>
          <span>Scope</span><span class="mono">${html(JSON.stringify(payload.row_scope || {}))}</span>
          <span>Query budget</span><span>${html(payload.budgets?.max_queries ?? state.maxQueries)} queries</span>
          <span>Row budget</span><span>${html(payload.budgets?.max_unique_expense_rows ?? state.maxRows)} unique expense rows</span>
          <span>Status</span><span>Task token bound to SessionBoundDB on first query</span>
        </div>
      `;
    }
    function enableAnalysis(enabled) {
      document.getElementById("sqlInput").disabled = !enabled;
      document.getElementById("runQueryBtn").disabled = !enabled;
      document.querySelectorAll(".preset-list button").forEach(btn => btn.disabled = !enabled);
    }
    function renderAll() {
      renderStepper();
      renderApplication();
      renderSession();
      renderBudgetPanel();
      enableAnalysis(Boolean(state.task && state.credential) && state.phase !== "expired");
      if (state.phase === "apply") setStatus("No approved session", "locked");
      if (state.phase === "approve") setStatus("Task application pending", "warn");
      if (state.phase === "issued" || state.phase === "analyze" || state.phase === "receipt") setStatus("Budgeted session active", "ok");
      if (state.phase === "expired") setStatus("Session expired", "warn");
    }
    function loadExample(kind) {
      document.getElementById("businessQuestion").value = kind === "risk"
        ? "Analyze June 2026 reimbursements for high-value risk signals. Focus on large single claims, monthly employee totals, repeated merchants, and unusual departments. Do not access salary, phone, bank account, or raw tables."
        : "Review June 2026 travel reimbursements for unusual merchants, repeated claims, high-value reimbursement risk, and aggregate department totals. Exclude salary, bank account, phone, identity number, private notes, and credential material.";
    }
    function submitApplication() {
      state.maxQueries = Number(document.getElementById("maxQueries").value || 20);
      state.maxRows = Number(document.getElementById("maxRows").value || 5000);
      const dept = document.getElementById("department").value;
      const deptLabel = document.getElementById("department").selectedOptions[0].textContent;
      state.application = {
        status: "Submitted",
        requester: document.getElementById("requester").value,
        departmentId: dept,
        scope: `tenant=company_a, expense_month=2026-06${dept ? `, department=${deptLabel}` : ""}`,
        question: document.getElementById("businessQuestion").value,
        maxQueries: state.maxQueries,
        maxRows: state.maxRows
      };
      state.phase = "approve";
      renderAll();
    }
    function denyApplication() {
      if (!state.application) return;
      state.application.status = "Denied";
      state.phase = "apply";
      renderAll();
    }
    async function approveSession() {
      if (!state.application) return;
      const approveButtons = document.querySelectorAll("#applicationCard button");
      approveButtons.forEach(btn => btn.disabled = true);
      state.application.status = "Approved";
      state.phase = "issued";
      renderAll();
      try {
        const credential = await fetch("/credentials", {
          method: "POST",
          headers: {"Content-Type": "application/json"},
          body: JSON.stringify({agent_id: "sessionbound-demo-agent", ttl_minutes: 15})
        }).then(r => r.json());
        const task = await fetch("/tasks", {
          method: "POST",
          headers: {"Content-Type": "application/json"},
          body: JSON.stringify({
            task_id: "task_june_expense_analysis_" + Date.now(),
            task_type: "monthly_travel_expense_review",
            delegator: state.application.requester,
            actor: "agent:travel-expense-analyst",
            department_id: state.application.departmentId || null,
            max_queries: state.maxQueries,
            max_rows: state.maxRows
          })
        }).then(r => r.json());
        if (credential.detail || task.detail) throw new Error(credential.detail || task.detail);
        state.credential = credential;
        state.task = task;
        state.issuedAt = new Date();
        state.application.status = "Session Issued";
        state.phase = "analyze";
        loadSql("variance");
      } catch (err) {
        state.phase = "approve";
        state.application.status = "Submitted";
        document.getElementById("resultPanel").innerHTML = `<strong class="status-bad">Session issuance failed</strong><p>${html(err.message)}</p>`;
      }
      renderAll();
    }
    function loadSql(kind) {
      document.getElementById("sqlInput").value = sqlPresets[kind] || sqlPresets.variance;
    }
    function rowsTable(rows) {
      if (!rows || !rows.length) return "<p>No rows returned.</p>";
      const columns = Object.keys(rows[0]);
      return `<table><thead><tr>${columns.map(col => `<th>${html(col)}</th>`).join("")}</tr></thead><tbody>${
        rows.map(row => `<tr>${columns.map(col => `<td>${html(formatCell(col, row[col]))}</td>`).join("")}</tr>`).join("")
      }</tbody></table>`;
    }
    function formatCell(key, value) {
      if (typeof value === "number" && (key.includes("amount") || key.includes("total"))) return money(value);
      if (typeof value === "object" && value !== null) return JSON.stringify(value);
      return value;
    }
    function cleanError(error) {
      if (!error) return "";
      return String(error).split("\nCONTEXT:")[0];
    }
    function latestReceipt(result) {
      const receipts = result?.receipts || [];
      return receipts[0] || null;
    }
    async function runQuery() {
      if (!state.credential || !state.task) return;
      const sql = document.getElementById("sqlInput").value.trim();
      if (!sql) return;
      document.getElementById("runQueryBtn").disabled = true;
      document.getElementById("decisionBadge").textContent = "Running in SessionBoundDB";
      try {
        const result = await fetch("/agent-query", {
          method: "POST",
          headers: {"Content-Type": "application/json"},
          body: JSON.stringify({
            credential: state.credential,
            payload_text: state.task.payload_text,
            signature: state.task.signature,
            sql
          })
        }).then(r => r.json());
        state.latestResult = result;
        state.queryRuns += 1;
        if (result.ok) state.rowsReturned += (result.rows || []).length;
        state.phase = "receipt";
        document.getElementById("decisionBadge").innerHTML = result.ok
          ? '<span class="status-ok">allowed</span>'
          : '<span class="status-bad">denied</span>';
        document.getElementById("resultPanel").innerHTML = result.ok
          ? rowsTable(result.rows || [])
          : `<strong class="status-bad">SessionBoundDB denied query</strong><p>${html(cleanError(result.error))}</p>`;
        const receipt = latestReceipt(result);
        document.getElementById("receiptPanel").innerHTML = `<pre>${html(JSON.stringify({
          task_id: state.task.payload?.task_id,
          session_id: state.credential.db_user,
          decision: result.ok ? "allowed" : "denied",
          rows_returned: result.ok ? (result.rows || []).length : 0,
          database_receipt: receipt || "No persisted receipt for denied statements that rolled back",
          remaining_budget: {
            queries_observed_by_ui: Math.max(0, state.maxQueries - state.queryRuns),
            unique_expense_rows_from_runtime: receipt?.remaining_unique_row_budget ?? "see runtime state"
          }
        }, null, 2))}</pre>`;
      } catch (err) {
        document.getElementById("decisionBadge").innerHTML = '<span class="status-bad">error</span>';
        document.getElementById("resultPanel").innerHTML = `<strong class="status-bad">Request failed</strong><p>${html(err.message)}</p>`;
      } finally {
        renderAll();
      }
    }
    function resetDemo() {
      state.phase = "apply";
      state.application = null;
      state.credential = null;
      state.task = null;
      state.issuedAt = null;
      state.queryRuns = 0;
      state.rowsReturned = 0;
      state.latestResult = null;
      document.getElementById("sqlInput").value = "";
      document.getElementById("decisionBadge").textContent = "Locked until session issued";
      document.getElementById("resultPanel").innerHTML = `<strong>Database-enforced boundary</strong><p>The agent may generate SQL freely, but SessionBoundDB decides whether the query is inside the approved task.</p>`;
      document.getElementById("receiptPanel").innerHTML = `<pre>{
  "status": "no query receipt yet"
}</pre>`;
      renderAll();
    }
    renderAll();
  </script>
</body>
</html>
"""
