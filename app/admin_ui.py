ADMIN_HTML = r"""
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>SessionBound Policy Console</title>
  <style>
    :root {
      --bg: #f4f6f8;
      --panel: #ffffff;
      --panel-soft: #f8fafc;
      --line: #d9dee7;
      --text: #172033;
      --muted: #667085;
      --accent: #0f766e;
      --accent-soft: #e7f5f2;
      --danger: #b42318;
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
      padding: 20px 24px 16px;
    }
    h1 { margin: 0 0 6px; font-size: 23px; letter-spacing: 0; }
    h2 { margin: 0; font-size: 16px; letter-spacing: 0; }
    h3 { margin: 0 0 6px; font-size: 14px; letter-spacing: 0; }
    p { margin: 0; color: var(--muted); line-height: 1.5; }
    a { color: var(--accent); text-decoration: none; }
    a:hover { text-decoration: underline; }
    main {
      display: grid;
      grid-template-columns: minmax(360px, 430px) minmax(0, 1fr);
      gap: 14px;
      padding: 14px;
      align-items: start;
    }
    section, .card {
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 14px;
    }
    .stack { display: grid; gap: 14px; }
    .cards {
      display: grid;
      grid-template-columns: repeat(3, minmax(0, 1fr));
      gap: 10px;
      margin-top: 14px;
    }
    .card {
      background: var(--panel-soft);
      min-height: 108px;
    }
    .card b { display: block; margin-bottom: 6px; }
    .section-title {
      display: flex;
      justify-content: space-between;
      gap: 10px;
      align-items: baseline;
      margin-bottom: 12px;
    }
    .eyebrow {
      color: var(--muted);
      font-size: 12px;
      white-space: nowrap;
    }
    .status {
      display: grid;
      gap: 8px;
      margin-top: 12px;
    }
    .status-row {
      display: flex;
      align-items: center;
      gap: 8px;
      padding: 8px 9px;
      background: var(--panel-soft);
      border: 1px solid var(--line);
      border-radius: 6px;
      color: var(--muted);
      font-size: 13px;
    }
    .dot {
      width: 9px;
      height: 9px;
      border-radius: 999px;
      background: var(--warn);
      flex: 0 0 auto;
    }
    .dot.ok { background: var(--accent); }
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
      min-height: 210px;
      resize: vertical;
      font-family: ui-monospace, SFMono-Regular, Consolas, "Liberation Mono", monospace;
      line-height: 1.45;
    }
    .row { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; }
    .row-3 { display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 8px; }
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
    button.secondary {
      background: white;
      color: var(--accent);
    }
    button.danger {
      border-color: var(--danger);
      color: var(--danger);
      background: white;
    }
    pre {
      margin: 0;
      padding: 11px;
      border-radius: 6px;
      background: var(--code);
      color: #f9fafb;
      font-family: ui-monospace, SFMono-Regular, Consolas, "Liberation Mono", monospace;
      font-size: 12px;
      line-height: 1.45;
      white-space: pre-wrap;
      word-break: break-word;
      overflow: auto;
      max-height: 520px;
    }
    .tabs {
      display: flex;
      gap: 8px;
      flex-wrap: wrap;
      margin-bottom: 12px;
    }
    .tab {
      border: 1px solid var(--line);
      background: white;
      color: var(--text);
    }
    .tab.active {
      border-color: var(--accent);
      background: var(--accent-soft);
      color: var(--accent);
    }
    .hidden { display: none; }
    .callout {
      border: 1px solid #a7d8cf;
      background: #eefbf8;
      border-radius: 8px;
      padding: 12px;
      margin-top: 12px;
      color: #134e48;
      font-size: 13px;
      line-height: 1.45;
    }
    .pill {
      display: inline-flex;
      align-items: center;
      gap: 6px;
      padding: 4px 7px;
      border-radius: 999px;
      background: var(--accent-soft);
      color: var(--accent);
      font-size: 12px;
      margin: 6px 6px 0 0;
    }
    @media (max-width: 1100px) {
      main, .cards { grid-template-columns: 1fr; }
      .row, .row-3 { grid-template-columns: 1fr; }
    }
  </style>
</head>
<body>
  <header>
    <h1>SessionBound Policy Console</h1>
    <p>Configure task templates and user grants, inspect registered safe views, and preview the signed task token a user/agent will receive. <a href="/">Open task-to-session demo</a></p>
    <div class="cards">
      <div class="card">
        <b>Define task templates</b>
        <p>Create or update demo task templates: allowed safe views, denied fields, scopes, budgets, TTL, and policy version.</p>
      </div>
      <div class="card">
        <b>Grant task access</b>
        <p>Authorize which user may request which task type, with tenant/department scope and budget caps.</p>
      </div>
      <div class="card">
        <b>Inspect safe-view policy</b>
        <p>Safe views are read-only here. Database views are created by the data platform or migrations, not by this console.</p>
      </div>
    </div>
  </header>

  <main>
    <div class="stack">
      <section>
        <div class="section-title">
          <h2>Policy Console Status</h2>
          <span class="eyebrow">current registry</span>
        </div>
        <div class="status">
          <div class="status-row"><span class="dot ok"></span><span id="templateStatus">Templates loading...</span></div>
          <div class="status-row"><span class="dot ok"></span><span id="grantStatus">Grants loading...</span></div>
          <div class="status-row"><span class="dot"></span><span id="tokenStatus">No token preview yet</span></div>
        </div>
        <div class="callout">
          Agents may propose work, but this page represents the policy approval boundary. The agent never approves its own access.
        </div>
      </section>

      <section>
        <div class="section-title">
          <h2>Policy Library</h2>
          <span class="eyebrow">read-only summary</span>
        </div>
        <div id="library"></div>
      </section>

      <section>
        <div class="section-title">
          <h2>Safe View Registry</h2>
          <span class="eyebrow">read-only, data platform owned</span>
        </div>
        <p>This console does not create database views. It lists already-registered safe views that were created and reviewed outside the demo UI. Task templates can reference these safe views; raw tables are not grantable.</p>
        <div id="safeViews" style="margin-top:10px;"></div>
      </section>
    </div>

    <div class="stack">
      <section>
        <div class="tabs">
          <button id="tabTemplate" class="tab active" onclick="showTab('template')">1. Task Template</button>
          <button id="tabGrant" class="tab" onclick="showTab('grant')">2. User Grant</button>
          <button id="tabPreview" class="tab" onclick="showTab('preview')">3. Token Preview</button>
        </div>

        <div id="panelTemplate">
          <div class="section-title">
            <h2>Define A Task Template</h2>
            <span class="eyebrow">POST /admin/task-templates</span>
          </div>
        <p>A template is the reusable security shape of a business task. It can be saved here, but it can only reference registered safe views, not raw tables or newly-created database views.</p>
          <div class="row">
            <div>
              <label for="tplType">Task type</label>
              <input id="tplType" value="monthly_travel_expense_review" />
            </div>
            <div>
              <label for="tplPurpose">Purpose</label>
              <input id="tplPurpose" value="monthly_travel_expense_anomaly_review" />
            </div>
          </div>
          <label for="tplDescription">Admin-facing description</label>
          <input id="tplDescription" value="Review monthly travel reimbursement anomalies." />
          <div class="row">
            <div>
              <label for="tplViews">Allowed views</label>
              <input id="tplViews" value="expenses, departments, employees, approval_events, ledger_entries" />
            </div>
            <div>
              <label for="tplDenied">Denied columns</label>
              <input id="tplDenied" value="employees.bank_account, employees.phone, employees.salary" />
            </div>
          </div>
          <label for="tplCommands">Allowed workflow commands</label>
          <input id="tplCommands" value="submit_expense, finance_approve, department_approve, c_level_approve, return_expense_for_more_info, resubmit_expense, pay_expense" />
          <div class="row-3">
            <div>
              <label for="tplRequired">Required scope fields</label>
              <input id="tplRequired" value="expense_month" />
            </div>
            <div>
              <label for="tplOptional">Optional scope fields</label>
              <input id="tplOptional" value="department_id" />
            </div>
            <div>
              <label for="tplMonth">Default month</label>
              <input id="tplMonth" value="2026-06" />
            </div>
          </div>
          <div class="row-3">
            <div>
              <label for="tplQueries">Default query budget</label>
              <input id="tplQueries" type="number" value="5" />
            </div>
            <div>
              <label for="tplRows">Default row budget</label>
              <input id="tplRows" type="number" value="4" />
            </div>
            <div>
              <label for="tplTtl">TTL minutes</label>
              <input id="tplTtl" type="number" value="30" />
            </div>
          </div>
          <div class="buttons">
            <button onclick="buildTemplateJson()">Build Template JSON</button>
            <button onclick="saveTemplate()">Save Template</button>
            <button class="secondary" onclick="loadAdminData()">Reload</button>
          </div>
          <label for="templateJson">Advanced template JSON</label>
          <textarea id="templateJson"></textarea>
        </div>

        <div id="panelGrant" class="hidden">
          <div class="section-title">
            <h2>Grant A Task Type To A User</h2>
            <span class="eyebrow">POST /admin/task-grants</span>
          </div>
          <p>A grant says which user can request a task type and what scope and budget they are allowed to request.</p>
          <div class="row">
            <div>
              <label for="grantId">Grant ID</label>
              <input id="grantId" value="grant_alice_travel_review" />
            </div>
            <div>
              <label for="grantUser">Delegator user</label>
              <input id="grantUser" value="user:alice" />
            </div>
          </div>
          <div class="row">
            <div>
              <label for="grantTaskType">Task type</label>
              <input id="grantTaskType" value="monthly_travel_expense_review" />
            </div>
            <div>
              <label for="grantTenant">Tenant</label>
              <input id="grantTenant" value="company_a" />
            </div>
          </div>
          <div class="row">
            <div>
              <label for="grantMonth">Allowed month</label>
              <input id="grantMonth" value="2026-06" />
            </div>
            <div>
              <label for="grantDepartments">Allowed departments</label>
              <input id="grantDepartments" value="dep_fin, dep_sales, dep_eng" />
            </div>
          </div>
          <div class="row">
            <div>
              <label for="grantQueries">Max queries</label>
              <input id="grantQueries" type="number" value="30" />
            </div>
            <div>
              <label for="grantRows">Max unique rows</label>
              <input id="grantRows" type="number" value="5000" />
            </div>
          </div>
          <div class="buttons">
            <button onclick="buildGrantJson()">Build Grant JSON</button>
            <button onclick="saveGrant()">Save Grant</button>
          </div>
          <label for="grantJson">Advanced grant JSON</label>
          <textarea id="grantJson"></textarea>
        </div>

        <div id="panelPreview" class="hidden">
          <div class="section-title">
            <h2>Preview What The User/Agent Gets</h2>
            <span class="eyebrow">POST /tasks</span>
          </div>
          <p>This simulates a user/agent receiving a signed task token after policy approval. The token still needs a short-lived runtime credential before SQL can run.</p>
          <div class="row">
            <div>
              <label for="previewUser">Delegator user</label>
              <input id="previewUser" value="user:alice" />
            </div>
            <div>
              <label for="previewTaskType">Task type</label>
              <input id="previewTaskType" value="monthly_travel_expense_review" />
            </div>
          </div>
          <div class="row">
            <div>
              <label for="previewDepartment">Requested department</label>
              <select id="previewDepartment">
                <option value="">All granted departments</option>
                <option value="dep_fin">Finance</option>
                <option value="dep_sales">Sales</option>
                <option value="dep_eng">Engineering</option>
              </select>
            </div>
            <div>
              <label for="previewRows">Requested row budget</label>
              <input id="previewRows" type="number" value="10" />
            </div>
          </div>
          <div class="buttons">
            <button onclick="previewToken()">Issue Preview Token</button>
            <button class="secondary" onclick="window.location.href='/'">Open Task-to-Session Demo</button>
          </div>
          <label>Signed task token returned to Agent</label>
          <pre id="tokenPreview">No token preview yet.</pre>
        </div>
      </section>

      <section>
        <div class="section-title">
          <h2>Raw Registry</h2>
          <span class="eyebrow">debug view</span>
        </div>
        <pre id="registry">Loading...</pre>
      </section>
    </div>
  </main>

  <script>
    let templates = {};
    let grants = [];
    let safeViews = {};

    function splitList(value) {
      return value.split(',').map(v => v.trim()).filter(Boolean);
    }
    function fmt(value) {
      return JSON.stringify(value, null, 2);
    }
    function showTab(name) {
      for (const item of ['template', 'grant', 'preview']) {
        document.getElementById('panel' + item[0].toUpperCase() + item.slice(1)).classList.toggle('hidden', item !== name);
        document.getElementById('tab' + item[0].toUpperCase() + item.slice(1)).classList.toggle('active', item === name);
      }
    }
    function buildTemplateObject() {
      return {
        task_type: document.getElementById('tplType').value,
        description: document.getElementById('tplDescription').value,
        purpose: document.getElementById('tplPurpose').value,
        actor: "agent:travel-expense-analyst",
        tenant_id: "company_a",
        operations: ["SELECT"],
        allowed_views: splitList(document.getElementById('tplViews').value),
        allowed_commands: splitList(document.getElementById('tplCommands').value),
        denied_columns: splitList(document.getElementById('tplDenied').value),
        required_scope: splitList(document.getElementById('tplRequired').value),
        optional_scope: splitList(document.getElementById('tplOptional').value),
        default_scope: { expense_month: document.getElementById('tplMonth').value },
        default_budgets: {
          max_queries: Number(document.getElementById('tplQueries').value),
          max_unique_expense_rows: Number(document.getElementById('tplRows').value)
        },
        max_budgets: {
          max_queries: 100,
          max_unique_expense_rows: 5000
        },
        ttl_minutes: Number(document.getElementById('tplTtl').value),
        policy_version: "travel-demo-v1"
      };
    }
    function buildGrantObject() {
      return {
        grant_id: document.getElementById('grantId').value,
        delegator: document.getElementById('grantUser').value,
        task_type: document.getElementById('grantTaskType').value,
        allowed_scope: {
          tenant_id: document.getElementById('grantTenant').value,
          expense_month: document.getElementById('grantMonth').value,
          department_ids: splitList(document.getElementById('grantDepartments').value)
        },
        budget_overrides: {
          max_queries: Number(document.getElementById('grantQueries').value),
          max_unique_expense_rows: Number(document.getElementById('grantRows').value)
        }
      };
    }
    function buildTemplateJson() {
      document.getElementById('templateJson').value = fmt(buildTemplateObject());
    }
    function buildGrantJson() {
      document.getElementById('grantJson').value = fmt(buildGrantObject());
    }
    function fillTemplateForm(template) {
      document.getElementById('tplType').value = template.task_type || '';
      document.getElementById('tplPurpose').value = template.purpose || '';
      document.getElementById('tplDescription').value = template.description || '';
      document.getElementById('tplViews').value = (template.allowed_views || []).join(', ');
      document.getElementById('tplCommands').value = (template.allowed_commands || []).join(', ');
      document.getElementById('tplDenied').value = (template.denied_columns || []).join(', ');
      document.getElementById('tplRequired').value = (template.required_scope || []).join(', ');
      document.getElementById('tplOptional').value = (template.optional_scope || []).join(', ');
      document.getElementById('tplMonth').value = (template.default_scope || {}).expense_month || '';
      document.getElementById('tplQueries').value = (template.default_budgets || {}).max_queries || 5;
      document.getElementById('tplRows').value = (template.default_budgets || {}).max_unique_expense_rows || 4;
      document.getElementById('tplTtl').value = template.ttl_minutes || 30;
      document.getElementById('templateJson').value = fmt(template);
    }
    function fillGrantForm(grant) {
      document.getElementById('grantId').value = grant.grant_id || '';
      document.getElementById('grantUser').value = grant.delegator || 'user:alice';
      document.getElementById('grantTaskType').value = grant.task_type || 'monthly_travel_expense_review';
      document.getElementById('grantTenant').value = (grant.allowed_scope || {}).tenant_id || 'company_a';
      document.getElementById('grantMonth').value = (grant.allowed_scope || {}).expense_month || '2026-06';
      document.getElementById('grantDepartments').value = ((grant.allowed_scope || {}).department_ids || []).join(', ');
      document.getElementById('grantQueries').value = (grant.budget_overrides || {}).max_queries || 30;
      document.getElementById('grantRows').value = (grant.budget_overrides || {}).max_unique_expense_rows || 5000;
      document.getElementById('grantJson').value = fmt(grant);
    }
    function renderLibrary() {
      const templateNames = Object.keys(templates);
      const grantNames = grants.map(g => `${g.delegator} -> ${g.task_type}`);
      const viewNames = Object.keys(safeViews);
      document.getElementById('templateStatus').textContent = `${templateNames.length} task template(s) configured`;
      document.getElementById('grantStatus').textContent = `${grants.length} user grant(s) configured`;
      document.getElementById('library').innerHTML = [
        '<h3>Task templates</h3>',
        ...templateNames.map(name => `<span class="pill">${name}</span>`),
        '<h3 style="margin-top:14px;">User grants</h3>',
        ...grantNames.map(name => `<span class="pill">${name}</span>`),
        '<h3 style="margin-top:14px;">Registered safe views</h3>',
        ...viewNames.map(name => `<span class="pill">${name}</span>`)
      ].join('');
      document.getElementById('safeViews').innerHTML = viewNames.map(name => {
        const view = safeViews[name];
        return `
          <div class="card" style="margin-bottom:10px;">
            <b>${view.view_name}</b>
            <p>${view.description}</p>
            <div><span class="pill">object: ${view.database_object}</span><span class="pill">owner: ${view.owner}</span></div>
            <h3 style="margin-top:10px;">Safe columns</h3>
            <p>${(view.safe_columns || []).join(', ') || 'none'}</p>
            <h3 style="margin-top:10px;">Not exposed</h3>
            <p>${(view.not_exposed_columns || []).join(', ') || 'none'}</p>
            <h3 style="margin-top:10px;">Enforced scope</h3>
            <p>${(view.enforced_scope_claims || []).join(', ') || 'none'}</p>
          </div>
        `;
      }).join('');
      document.getElementById('registry').textContent = fmt({templates, grants});
    }
    async function loadAdminData() {
      templates = (await fetch('/admin/task-templates').then(r => r.json())).templates;
      grants = (await fetch('/admin/task-grants').then(r => r.json())).grants;
      safeViews = (await fetch('/admin/safe-views').then(r => r.json())).safe_views;
      fillTemplateForm(templates.monthly_travel_expense_review || Object.values(templates)[0] || buildTemplateObject());
      fillGrantForm(grants[0] || buildGrantObject());
      renderLibrary();
    }
    async function saveTemplate() {
      const template = JSON.parse(document.getElementById('templateJson').value);
      const res = await fetch('/admin/task-templates', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({template})
      });
      const data = await res.json();
      if (!res.ok || !data.ok) {
        alert(fmt(data));
        return;
      }
      await loadAdminData();
    }
    async function saveGrant() {
      const grant = JSON.parse(document.getElementById('grantJson').value);
      const res = await fetch('/admin/task-grants', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({grant})
      });
      const data = await res.json();
      if (!data.ok) alert(fmt(data));
      await loadAdminData();
    }
    async function previewToken() {
      const body = {
        task_id: 'task_admin_preview_' + Date.now(),
        task_type: document.getElementById('previewTaskType').value,
        delegator: document.getElementById('previewUser').value,
        department_id: document.getElementById('previewDepartment').value || null,
        max_rows: Number(document.getElementById('previewRows').value),
        max_queries: 10
      };
      const res = await fetch('/tasks', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify(body)
      });
      const data = await res.json();
      document.getElementById('tokenPreview').textContent = fmt(data);
      document.getElementById('tokenStatus').textContent = data.payload ? `Issued ${data.payload.task_id}` : `Denied: ${data.detail || 'request failed'}`;
      document.querySelector('#tokenStatus').previousElementSibling?.classList?.add('ok');
    }
    loadAdminData();
  </script>
</body>
</html>
"""
