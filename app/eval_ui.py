EVAL_HTML = """
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Prompt Compression Eval</title>
  <style>
    :root {
      color-scheme: light;
      --bg: #f5f7fb;
      --panel: #ffffff;
      --text: #17202a;
      --muted: #617083;
      --border: #d7dee8;
      --accent: #1769aa;
      --accent-dark: #0e4e84;
      --good: #236c43;
      --bad: #a62b2b;
      --warn: #866118;
      --soft-good: #e8f3ec;
      --soft-bad: #ffe7e4;
      --soft-warn: #fff4d8;
      --shadow: 0 10px 30px rgba(24, 39, 75, 0.08);
    }

    * {
      box-sizing: border-box;
    }

    body {
      margin: 0;
      background: var(--bg);
      color: var(--text);
      font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    }

    main {
      width: min(1280px, calc(100vw - 32px));
      margin: 0 auto;
      padding: 28px 0;
    }

    header {
      display: flex;
      align-items: end;
      justify-content: space-between;
      gap: 16px;
      margin-bottom: 18px;
    }

    h1 {
      margin: 0;
      font-size: 28px;
      line-height: 1.15;
      font-weight: 720;
    }

    .subhead {
      margin: 7px 0 0;
      color: var(--muted);
      font-size: 14px;
    }

    .nav-link {
      color: var(--accent);
      font-size: 13px;
      font-weight: 680;
      text-decoration: none;
    }

    .nav-links {
      display: flex;
      gap: 14px;
      flex-wrap: wrap;
      margin-top: 8px;
    }

    .stats {
      display: flex;
      gap: 10px;
      flex-wrap: wrap;
      justify-content: flex-end;
    }

    .stat {
      min-width: 112px;
      padding: 10px 12px;
      background: var(--panel);
      border: 1px solid var(--border);
      border-radius: 8px;
      box-shadow: var(--shadow);
    }

    .stat strong {
      display: block;
      font-size: 18px;
    }

    .stat span {
      color: var(--muted);
      font-size: 12px;
    }

    .toolbar,
    .case-list,
    .result {
      background: var(--panel);
      border: 1px solid var(--border);
      border-radius: 8px;
      box-shadow: var(--shadow);
    }

    .toolbar {
      display: flex;
      align-items: center;
      gap: 14px;
      flex-wrap: wrap;
      padding: 14px 16px;
      margin-bottom: 16px;
    }

    label {
      display: inline-flex;
      align-items: center;
      gap: 8px;
      color: var(--muted);
      font-size: 13px;
    }

    input[type="range"] {
      width: 170px;
      accent-color: var(--accent);
    }

    button {
      min-height: 38px;
      padding: 0 15px;
      border: 0;
      border-radius: 7px;
      background: var(--accent);
      color: white;
      font-weight: 680;
      cursor: pointer;
    }

    button:hover {
      background: var(--accent-dark);
    }

    button:disabled {
      cursor: wait;
      opacity: 0.65;
    }

    .secondary-button {
      border: 1px solid var(--border);
      background: #f8fafc;
      color: var(--text);
    }

    .secondary-button:hover {
      background: #eef3f8;
    }

    .case-list {
      overflow: hidden;
      margin-bottom: 16px;
    }

    .case-row {
      display: grid;
      grid-template-columns: 34px 150px minmax(180px, 1fr) 120px;
      gap: 12px;
      align-items: center;
      padding: 12px 16px;
      border-top: 1px solid var(--border);
    }

    .case-row:first-child {
      border-top: 0;
    }

    .case-title {
      font-weight: 700;
      font-size: 14px;
    }

    .case-description {
      margin-top: 3px;
      color: var(--muted);
      font-size: 12px;
      line-height: 1.35;
    }

    .tag {
      display: inline-flex;
      align-items: center;
      min-height: 22px;
      padding: 0 8px;
      border-radius: 999px;
      background: #eef2f8;
      color: #40506a;
      font-size: 12px;
      font-weight: 680;
      white-space: nowrap;
    }

    .status {
      color: var(--muted);
      font-size: 13px;
    }

    .error {
      color: var(--bad);
    }

    .results {
      display: grid;
      gap: 16px;
    }

    .result {
      overflow: hidden;
    }

    .result-head {
      display: flex;
      justify-content: space-between;
      gap: 12px;
      padding: 14px 16px;
      border-bottom: 1px solid var(--border);
    }

    .result-title {
      margin: 0;
      font-size: 16px;
      line-height: 1.25;
      font-weight: 760;
    }

    .pill {
      display: inline-flex;
      align-items: center;
      min-height: 26px;
      padding: 0 9px;
      border-radius: 999px;
      font-size: 12px;
      font-weight: 760;
      white-space: nowrap;
    }

    .pill.pass {
      background: var(--soft-good);
      color: var(--good);
    }

    .pill.fail {
      background: var(--soft-bad);
      color: var(--bad);
    }

    .metric-strip {
      display: flex;
      gap: 10px;
      flex-wrap: wrap;
      padding: 12px 16px;
      border-bottom: 1px solid var(--border);
      color: var(--muted);
      font-size: 12px;
    }

    .check-list {
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 8px;
      padding: 12px 16px;
      border-bottom: 1px solid var(--border);
    }

    .check {
      min-width: 0;
      padding: 9px 10px;
      border-radius: 7px;
      font-size: 12px;
      line-height: 1.35;
      border: 1px solid transparent;
    }

    .check.pass {
      background: var(--soft-good);
      color: var(--good);
    }

    .check.fail {
      background: var(--soft-bad);
      color: var(--bad);
    }

    .check.warn {
      background: var(--soft-warn);
      color: var(--warn);
    }

    .check strong {
      display: block;
      margin-bottom: 3px;
    }

    .comparison {
      display: grid;
      grid-template-columns: minmax(0, 1fr) minmax(0, 1fr);
    }

    .pane {
      min-width: 0;
      border-left: 1px solid var(--border);
    }

    .pane:first-child {
      border-left: 0;
    }

    .pane-title {
      padding: 10px 12px;
      border-bottom: 1px solid var(--border);
      color: var(--muted);
      font-size: 12px;
      font-weight: 760;
    }

    pre {
      margin: 0;
      min-height: 220px;
      max-height: 420px;
      overflow: auto;
      padding: 12px;
      white-space: pre-wrap;
      overflow-wrap: anywhere;
      font: 12px/1.55 ui-monospace, SFMono-Regular, Consolas, "Liberation Mono", monospace;
    }

    @media (max-width: 900px) {
      header {
        align-items: stretch;
        flex-direction: column;
      }

      .stats {
        justify-content: stretch;
      }

      .stat {
        flex: 1 1 130px;
      }

      .case-row {
        grid-template-columns: 34px minmax(0, 1fr);
      }

      .case-row .tag,
      .case-row .status {
        grid-column: 2;
      }

      .check-list,
      .comparison {
        grid-template-columns: 1fr;
      }

      .pane {
        border-left: 0;
        border-top: 1px solid var(--border);
      }

      .pane:first-child {
        border-top: 0;
      }
    }
  </style>
</head>
<body>
  <main>
    <header>
      <div>
        <h1>Prompt Compression Eval</h1>
        <p class="subhead">Quality checks for preserved data, protected structures, latency, and token savings.</p>
        <nav class="nav-links" aria-label="Primary navigation">
          <a class="nav-link" href="/">Compression UI</a>
          <a class="nav-link" href="/benchmark">Benchmark</a>
          <a class="nav-link" href="/research">Research</a>
        </nav>
      </div>
      <div class="stats" aria-live="polite">
        <div class="stat"><strong id="passRate">-</strong><span>Pass rate</span></div>
        <div class="stat"><strong id="caseCount">-</strong><span>Cases</span></div>
        <div class="stat"><strong id="warningCount">-</strong><span>Warnings</span></div>
      </div>
    </header>

    <div class="toolbar">
      <button id="runButton" type="button">Run Selected</button>
      <button class="secondary-button" id="selectAllButton" type="button">Select All</button>
      <label>
        <input id="overrideEnabled" type="checkbox">
        Override aggressiveness
      </label>
      <label>
        <input id="aggressiveness" type="range" min="0" max="1" step="0.05" value="0.25">
        <strong id="aggressivenessValue">0.25</strong>
      </label>
      <span class="status" id="status">Loading cases...</span>
    </div>

    <div class="case-list" id="caseList"></div>
    <div class="results" id="results"></div>
  </main>

  <script>
    const caseList = document.getElementById("caseList");
    const results = document.getElementById("results");
    const runButton = document.getElementById("runButton");
    const selectAllButton = document.getElementById("selectAllButton");
    const overrideEnabled = document.getElementById("overrideEnabled");
    const aggressiveness = document.getElementById("aggressiveness");
    const aggressivenessValue = document.getElementById("aggressivenessValue");
    const statusNode = document.getElementById("status");
    const passRate = document.getElementById("passRate");
    const caseCount = document.getElementById("caseCount");
    const warningCount = document.getElementById("warningCount");
    const casesById = new Map();

    function setStatus(message, isError = false) {
      statusNode.textContent = message;
      statusNode.className = isError ? "status error" : "status";
    }

    function formatPercent(value) {
      return `${Math.round(value * 100)}%`;
    }

    function createTextNode(tag, className, text) {
      const node = document.createElement(tag);
      if (className) {
        node.className = className;
      }
      node.textContent = text;
      return node;
    }

    function renderCases(cases) {
      caseList.textContent = "";
      casesById.clear();
      for (const item of cases) {
        casesById.set(item.id, item);
        const row = document.createElement("div");
        row.className = "case-row";

        const checkbox = document.createElement("input");
        checkbox.type = "checkbox";
        checkbox.value = item.id;
        checkbox.checked = true;
        row.appendChild(checkbox);

        row.appendChild(createTextNode("span", "tag", item.category));

        const details = document.createElement("div");
        details.appendChild(createTextNode("div", "case-title", item.title));
        details.appendChild(createTextNode("div", "case-description", item.description));
        row.appendChild(details);

        row.appendChild(createTextNode(
          "span",
          "status",
          `Default ${Number(item.default_aggressiveness).toFixed(2)}`,
        ));

        caseList.appendChild(row);
      }
      caseCount.textContent = String(cases.length);
      setStatus(`${cases.length} cases ready`);
    }

    function selectedCaseIds() {
      return [...caseList.querySelectorAll("input[type='checkbox']:checked")]
        .map((checkbox) => checkbox.value);
    }

    function checkClass(check) {
      if (check.passed) {
        return "check pass";
      }
      if (check.severity === "warning") {
        return "check warn";
      }
      return "check fail";
    }

    function renderResult(item) {
      const sourceCase = casesById.get(item.case_id);
      const resultNode = document.createElement("article");
      resultNode.className = "result";

      const head = document.createElement("div");
      head.className = "result-head";
      const titleWrap = document.createElement("div");
      titleWrap.appendChild(createTextNode("h2", "result-title", item.title));
      titleWrap.appendChild(createTextNode("div", "case-description", item.case_id));
      head.appendChild(titleWrap);
      head.appendChild(createTextNode("span", item.passed ? "pill pass" : "pill fail", item.passed ? "PASS" : "FAIL"));
      resultNode.appendChild(head);

      const metrics = document.createElement("div");
      metrics.className = "metric-strip";
      metrics.appendChild(createTextNode("span", "", `${item.original_tokens} -> ${item.compressed_tokens} tokens`));
      metrics.appendChild(createTextNode("span", "", `${formatPercent(item.reduction)} reduction`));
      metrics.appendChild(createTextNode("span", "", `${Math.round(item.elapsed_ms)} ms`));
      metrics.appendChild(createTextNode("span", "", `aggr ${Number(item.aggressiveness).toFixed(2)}`));
      metrics.appendChild(createTextNode("span", "", `rate ${Number(item.target_rate).toFixed(2)}`));
      resultNode.appendChild(metrics);

      const checks = document.createElement("div");
      checks.className = "check-list";
      for (const check of item.checks) {
        const checkNode = document.createElement("div");
        checkNode.className = checkClass(check);
        checkNode.appendChild(createTextNode("strong", "", check.label));
        checkNode.appendChild(createTextNode("span", "", check.detail));
        checks.appendChild(checkNode);
      }
      resultNode.appendChild(checks);

      const comparison = document.createElement("div");
      comparison.className = "comparison";

      const originalPane = document.createElement("div");
      originalPane.className = "pane";
      originalPane.appendChild(createTextNode("div", "pane-title", "Original"));
      originalPane.appendChild(createTextNode("pre", "", sourceCase ? sourceCase.text : ""));

      const compressedPane = document.createElement("div");
      compressedPane.className = "pane";
      compressedPane.appendChild(createTextNode("div", "pane-title", "Compressed"));
      compressedPane.appendChild(createTextNode("pre", "", item.compressed_text));

      comparison.appendChild(originalPane);
      comparison.appendChild(compressedPane);
      resultNode.appendChild(comparison);

      return resultNode;
    }

    function renderRun(data) {
      results.textContent = "";
      for (const item of data.results) {
        results.appendChild(renderResult(item));
      }
      const warningTotal = data.results.reduce((total, item) => {
        return total + item.checks.filter((check) => check.severity === "warning" && !check.passed).length;
      }, 0);
      passRate.textContent = data.total_cases ? formatPercent(data.passed_cases / data.total_cases) : "-";
      caseCount.textContent = `${data.passed_cases}/${data.total_cases}`;
      warningCount.textContent = String(warningTotal);
      setStatus(data.passed ? "Complete" : "Failures found", !data.passed);
    }

    aggressiveness.addEventListener("input", () => {
      aggressivenessValue.textContent = Number(aggressiveness.value).toFixed(2);
    });

    selectAllButton.addEventListener("click", () => {
      const boxes = [...caseList.querySelectorAll("input[type='checkbox']")];
      const shouldCheck = boxes.some((checkbox) => !checkbox.checked);
      for (const checkbox of boxes) {
        checkbox.checked = shouldCheck;
      }
    });

    runButton.addEventListener("click", async () => {
      const ids = selectedCaseIds();
      const body = { case_ids: ids };
      if (overrideEnabled.checked) {
        body.aggressiveness = Number(aggressiveness.value);
      }

      runButton.disabled = true;
      results.textContent = "";
      setStatus("Running...");

      try {
        const response = await fetch("/eval/run", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body),
        });
        const data = await response.json();
        if (!response.ok) {
          throw new Error(data.detail || "Eval failed");
        }
        renderRun(data);
      } catch (error) {
        setStatus(error.message, true);
      } finally {
        runButton.disabled = false;
      }
    });

    async function loadCases() {
      try {
        const response = await fetch("/eval/cases");
        const data = await response.json();
        if (!response.ok) {
          throw new Error(data.detail || "Failed to load cases");
        }
        renderCases(data);
      } catch (error) {
        setStatus(error.message, true);
      }
    }

    loadCases();
  </script>
</body>
</html>
"""
