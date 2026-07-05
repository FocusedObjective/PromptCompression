BENCHMARK_HTML = """
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Prompt Compression Benchmark</title>
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
      --bad: #a62b2b;
      --good: #236c43;
      --warn: #866118;
      --soft-bad: #ffe7e4;
      --soft-good: #e8f3ec;
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
      width: min(1380px, calc(100vw - 32px));
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

    h2 {
      margin: 0;
      font-size: 15px;
      line-height: 1.2;
      font-weight: 720;
    }

    .subhead {
      margin: 7px 0 0;
      color: var(--muted);
      font-size: 14px;
    }

    .nav-links {
      display: flex;
      gap: 14px;
      flex-wrap: wrap;
      margin-top: 8px;
    }

    .nav-link {
      color: var(--accent);
      font-size: 13px;
      font-weight: 680;
      text-decoration: none;
    }

    .stats {
      display: flex;
      gap: 10px;
      flex-wrap: wrap;
      justify-content: flex-end;
    }

    .stat {
      min-width: 120px;
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
    .panel {
      background: var(--panel);
      border: 1px solid var(--border);
      border-radius: 8px;
      box-shadow: var(--shadow);
    }

    .toolbar {
      display: grid;
      grid-template-columns: repeat(6, minmax(120px, 1fr));
      gap: 12px;
      padding: 14px 16px;
      margin-bottom: 16px;
    }

    .field {
      display: grid;
      gap: 5px;
      min-width: 0;
      color: var(--muted);
      font-size: 12px;
      font-weight: 620;
    }

    .field.wide {
      grid-column: span 2;
    }

    .field input[type="text"],
    .field input[type="number"] {
      width: 100%;
      min-height: 34px;
      border: 1px solid var(--border);
      border-radius: 6px;
      outline: 0;
      padding: 7px 9px;
      background: #ffffff;
      color: var(--text);
      font: 13px/1.4 ui-monospace, SFMono-Regular, Consolas, "Liberation Mono", monospace;
    }

    .field input[type="range"] {
      width: 100%;
      accent-color: var(--accent);
    }

    .inline {
      display: flex;
      align-items: center;
      gap: 8px;
      min-height: 34px;
      color: var(--muted);
      font-size: 13px;
      font-weight: 620;
    }

    .actions {
      grid-column: 1 / -1;
      display: flex;
      align-items: center;
      gap: 10px;
      flex-wrap: wrap;
      padding-top: 2px;
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

    .danger-button {
      background: var(--bad);
    }

    .danger-button:hover {
      background: #7f2020;
    }

    .status {
      color: var(--muted);
      font-size: 13px;
    }

    .status.error {
      color: var(--bad);
    }

    .status.ok {
      color: var(--good);
    }

    .grid {
      display: grid;
      grid-template-columns: minmax(0, 1fr);
      gap: 16px;
    }

    .panel-head {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 12px;
      padding: 14px 16px;
      border-bottom: 1px solid var(--border);
    }

    .table-wrap {
      overflow: auto;
    }

    table {
      width: 100%;
      border-collapse: collapse;
      font-size: 12px;
    }

    th,
    td {
      padding: 9px 10px;
      border-bottom: 1px solid var(--border);
      text-align: right;
      white-space: nowrap;
    }

    th:first-child,
    td:first-child {
      text-align: left;
    }

    th {
      color: var(--muted);
      font-weight: 760;
      background: #fbfcfe;
    }

    tr.error-row td {
      background: var(--soft-bad);
      color: var(--bad);
    }

    tr.ok-row td:first-child::before {
      content: "";
      display: inline-block;
      width: 7px;
      height: 7px;
      margin-right: 7px;
      border-radius: 50%;
      background: var(--good);
    }

    .log {
      min-height: 180px;
      max-height: 280px;
      overflow: auto;
      margin: 0;
      padding: 12px 16px;
      color: var(--muted);
      background: #fbfcfe;
      font: 12px/1.5 ui-monospace, SFMono-Regular, Consolas, "Liberation Mono", monospace;
      white-space: pre-wrap;
    }

    @media (max-width: 1100px) {
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

      .toolbar {
        grid-template-columns: repeat(2, minmax(0, 1fr));
      }

      .field.wide {
        grid-column: span 2;
      }
    }

    @media (max-width: 680px) {
      .toolbar {
        grid-template-columns: 1fr;
      }

      .field.wide {
        grid-column: auto;
      }
    }
  </style>
</head>
<body>
  <main>
    <header>
      <div>
        <h1>Performance Benchmark</h1>
        <p class="subhead">Measure size, JSON share, latency phases, and token reduction on this deployment.</p>
        <nav class="nav-links" aria-label="Primary navigation">
          <a class="nav-link" href="/">Compression UI</a>
          <a class="nav-link" href="/eval">Eval Suite</a>
          <a class="nav-link" href="/research">Research</a>
          <a class="nav-link" href="/docs">API Docs</a>
        </nav>
      </div>
      <div class="stats" aria-live="polite">
        <div class="stat"><strong id="progressStat">-</strong><span>Progress</span></div>
        <div class="stat"><strong id="clientP50Stat">-</strong><span>Client p50</span></div>
        <div class="stat"><strong id="llmlinguaP50Stat">-</strong><span>LLMLingua p50</span></div>
        <div class="stat"><strong id="modelCallStat">-</strong><span>Model calls</span></div>
        <div class="stat"><strong id="errorStat">-</strong><span>Errors</span></div>
      </div>
    </header>

    <section class="toolbar" aria-label="Benchmark controls">
      <label class="field wide">
        Target tokens
        <input id="sizesInput" type="text" spellcheck="false" value="256,512,1000,1500,2000,2500,3000,6000,12000,24000,50000,100000,200000">
      </label>
      <label class="field wide">
        JSON ratios
        <input id="jsonRatiosInput" type="text" spellcheck="false" value="0,0.1,0.25,0.5,0.75">
      </label>
      <label class="field">
        Repeats
        <input id="repeatsInput" type="number" min="1" max="20" step="1" value="1">
      </label>
      <label class="field">
        Warmup
        <input id="warmupInput" type="number" min="0" max="20" step="1" value="1">
      </label>
      <label class="field">
        Concurrency
        <input id="concurrencyInput" type="number" min="1" max="8" step="1" value="1">
      </label>
      <label class="field">
        Aggressiveness <strong id="aggressivenessValue">0.25</strong>
        <input id="aggressivenessInput" type="range" min="0" max="1" step="0.05" value="0.25">
      </label>
      <label class="inline">
        <input id="includeSectionsInput" type="checkbox">
        Include sections
      </label>
      <div class="actions">
        <button id="runButton" type="button">Run</button>
        <button class="danger-button" id="stopButton" type="button" disabled>Stop</button>
        <button class="secondary-button" id="downloadRawButton" type="button" disabled>Download Raw JSONL</button>
        <button class="secondary-button" id="downloadSummaryButton" type="button" disabled>Download Summary CSV</button>
        <span class="status" id="statusNode">Ready</span>
      </div>
    </section>

    <div class="grid">
      <section class="panel">
        <div class="panel-head">
          <h2>Summary</h2>
          <span class="status" id="summaryStatus">No results</span>
        </div>
        <div class="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Case</th>
                <th>Runs</th>
                <th>Client p50</th>
                <th>Server p50</th>
                <th>Preprocess p50</th>
                <th>Selection p50</th>
                <th>Model calls</th>
                <th>LLMLingua p50</th>
                <th>Token est. p50</th>
                <th>Reduction avg</th>
              </tr>
            </thead>
            <tbody id="summaryBody"></tbody>
          </table>
        </div>
      </section>

      <section class="panel">
        <div class="panel-head">
          <h2>Runs</h2>
          <span class="status" id="runStatus">0 rows</span>
        </div>
        <div class="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Case</th>
                <th>Repeat</th>
                <th>Status</th>
                <th>Client</th>
                <th>Server</th>
                <th>Preprocess</th>
                <th>LLMLingua</th>
                <th>In tokens</th>
                <th>Out tokens</th>
                <th>Error</th>
              </tr>
            </thead>
            <tbody id="rawBody"></tbody>
          </table>
        </div>
      </section>

      <section class="panel">
        <div class="panel-head">
          <h2>Log</h2>
          <span class="status" id="logStatus">Idle</span>
        </div>
        <pre class="log" id="logNode"></pre>
      </section>
    </div>
  </main>

  <script>
    const TIMING_KEYS = [
      "total_ms",
      "target_rate_ms",
      "preprocessing_ms",
      "force_drop_ms",
      "segment_selection_ms",
      "model_load_ms",
      "model_input_ms",
      "force_tokens_ms",
      "llmlingua_ms",
      "placeholder_validation_ms",
      "model_expand_ms",
      "uncompressed_expand_ms",
      "token_estimate_ms",
      "other_ms",
    ];
    const letterOrNumberPattern = /[\\p{L}\\p{N}]/u;
    const rows = [];
    const activeControllers = new Set();
    let cancelled = false;
    let plannedRuns = 0;
    let completedRuns = 0;

    const sizesInput = document.getElementById("sizesInput");
    const jsonRatiosInput = document.getElementById("jsonRatiosInput");
    const repeatsInput = document.getElementById("repeatsInput");
    const warmupInput = document.getElementById("warmupInput");
    const concurrencyInput = document.getElementById("concurrencyInput");
    const aggressivenessInput = document.getElementById("aggressivenessInput");
    const aggressivenessValue = document.getElementById("aggressivenessValue");
    const includeSectionsInput = document.getElementById("includeSectionsInput");
    const runButton = document.getElementById("runButton");
    const stopButton = document.getElementById("stopButton");
    const downloadRawButton = document.getElementById("downloadRawButton");
    const downloadSummaryButton = document.getElementById("downloadSummaryButton");
    const statusNode = document.getElementById("statusNode");
    const progressStat = document.getElementById("progressStat");
    const clientP50Stat = document.getElementById("clientP50Stat");
    const llmlinguaP50Stat = document.getElementById("llmlinguaP50Stat");
    const modelCallStat = document.getElementById("modelCallStat");
    const errorStat = document.getElementById("errorStat");
    const summaryStatus = document.getElementById("summaryStatus");
    const runStatus = document.getElementById("runStatus");
    const logStatus = document.getElementById("logStatus");
    const summaryBody = document.getElementById("summaryBody");
    const rawBody = document.getElementById("rawBody");
    const logNode = document.getElementById("logNode");

    function setStatus(message, state = "") {
      statusNode.textContent = message;
      statusNode.className = state ? `status ${state}` : "status";
    }

    function log(message) {
      const stamp = new Date().toISOString().slice(11, 19);
      logNode.textContent += `[${stamp}] ${message}\\n`;
      logNode.scrollTop = logNode.scrollHeight;
    }

    function parseNumberList(value, parser) {
      return value
        .split(",")
        .map((part) => parser(part.trim().replaceAll("_", "")))
        .filter((item) => Number.isFinite(item));
    }

    function estimateTokenCount(text) {
      let count = 0;
      let inWord = false;
      for (const char of text) {
        if (/\\s/u.test(char)) {
          inWord = false;
          continue;
        }
        if (letterOrNumberPattern.test(char)) {
          if (!inWord) {
            count += 1;
            inWord = true;
          }
          continue;
        }
        count += 1;
        inWord = false;
      }
      return count;
    }

    function pad(value, width) {
      return String(value).padStart(width, "0");
    }

    function buildProse(tokenBudget) {
      const sample = proseUnit(1);
      const unitTokens = Math.max(1, estimateTokenCount(sample));
      const count = Math.max(1, Math.ceil(tokenBudget / unitTokens));
      const parts = [];
      for (let index = 1; index <= count; index += 1) {
        parts.push(proseUnit(index));
      }
      return parts.join("");
    }

    function proseUnit(index) {
      const key = pad(index, 6);
      return (
        `Incident INC-${key} shows queue latency, retry pressure, ` +
        "payment authorization drift, account owner follow-up, contract deadline " +
        `2026-07-15, dashboard https://example.com/run/${key}, and support ` +
        "notes requiring concise executive summary with exact identifiers. "
      );
    }

    function jsonPayload(recordCount) {
      const records = [];
      const regions = ["us-central1", "us-east1", "us-west1"];
      const severities = ["low", "medium", "high", "critical"];
      const statuses = ["open", "monitoring", "resolved"];
      for (let index = 0; index < recordCount; index += 1) {
        records.push({
          account_id: `acct_${pad(index, 8)}`,
          incident_id: `INC-${pad(index, 6)}`,
          region: regions[index % regions.length],
          severity: severities[index % severities.length],
          status: statuses[index % statuses.length],
          retry_limit: 3 + (index % 2),
          p95_latency_ms: 250 + (index % 700),
          dashboard_url: `https://example.com/dashboards/${pad(index, 6)}`,
          note: (
            "Synthetic benchmark record used to stress JSON preprocessing " +
            "and TOON conversion paths."
          ),
        });
      }
      return JSON.stringify({
        generated_for: "prompt-compression-performance",
        records,
        schema_version: "benchmark.v1",
      }, null, 2);
    }

    function buildJsonBlock(tokenBudget) {
      if (tokenBudget <= 0) {
        return "";
      }
      const baseTokens = estimateTokenCount(jsonPayload(0));
      const sampleCount = 10;
      const sampleTokens = estimateTokenCount(jsonPayload(sampleCount));
      const recordTokens = Math.max(1, (sampleTokens - baseTokens) / sampleCount);
      const recordCount = Math.max(1, Math.ceil(Math.max(1, tokenBudget - baseTokens) / recordTokens));
      return "Customer telemetry JSON:\\n" + jsonPayload(recordCount);
    }

    function formatRatio(value) {
      return String(value).replace(".", "p");
    }

    function buildCase(targetTokens, jsonRatio) {
      const jsonTokenBudget = Math.floor(targetTokens * jsonRatio);
      const proseTokenBudget = Math.max(1, targetTokens - jsonTokenBudget);
      const prose = buildProse(proseTokenBudget);
      const jsonBlock = buildJsonBlock(jsonTokenBudget);
      const parts = [
        "You are a support operations analyst preparing an escalation brief.",
        "Preserve customer IDs, URLs, dates, retry limits, and hard constraints.",
        "Summarize risk, identify likely blockers, and propose next actions.",
        prose,
        jsonBlock,
        "Output: executive summary, blockers and owner, next three actions.",
      ].filter((part) => part);
      const text = parts.join("\\n\\n");
      const syntheticInputTokens = estimateTokenCount(text);
      const syntheticJsonTokens = jsonBlock ? estimateTokenCount(jsonBlock) : 0;
      return {
        case_id: `tok${targetTokens}_json${formatRatio(jsonRatio)}`,
        target_tokens: targetTokens,
        json_ratio_target: jsonRatio,
        synthetic_input_tokens: syntheticInputTokens,
        synthetic_json_tokens: syntheticJsonTokens,
        synthetic_json_ratio: syntheticInputTokens ? syntheticJsonTokens / syntheticInputTokens : 0,
        input_chars: text.length,
        json_chars: jsonBlock.length,
        text,
      };
    }

    function buildTasks(sizes, jsonRatios, repeats) {
      const tasks = [];
      for (const targetTokens of sizes) {
        for (const jsonRatio of jsonRatios) {
          for (let repeat = 1; repeat <= repeats; repeat += 1) {
            tasks.push({ targetTokens, jsonRatio, repeat, measured: true });
          }
        }
      }
      return tasks;
    }

    function baseRow(testCase, repeat, measured) {
      return {
        status: "started",
        error: "",
        measured,
        case_id: testCase.case_id,
        repeat,
        target_tokens: testCase.target_tokens,
        json_ratio_target: testCase.json_ratio_target,
        synthetic_input_tokens: testCase.synthetic_input_tokens,
        synthetic_json_tokens: testCase.synthetic_json_tokens,
        synthetic_json_ratio: testCase.synthetic_json_ratio,
        input_chars: testCase.input_chars,
        json_chars: testCase.json_chars,
      };
    }

    async function runOne(task) {
      const testCase = buildCase(task.targetTokens, task.jsonRatio);
      const row = baseRow(testCase, task.repeat, task.measured);
      const controller = new AbortController();
      activeControllers.add(controller);
      const started = performance.now();
      try {
        const response = await fetch("/compress", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          signal: controller.signal,
          body: JSON.stringify({
            text: testCase.text,
            aggressiveness: Number(aggressivenessInput.value),
            include_sections: includeSectionsInput.checked,
            include_diagnostics: true,
          }),
        });
        row.http_status = response.status;
        row.client_wall_ms = performance.now() - started;
        let data = {};
        try {
          data = await response.json();
        } catch (error) {
          data = { detail: "Response was not JSON" };
        }
        if (!response.ok) {
          row.status = "error";
          row.error = data.detail || response.statusText;
          return row;
        }
        return addResponseFields(row, data);
      } catch (error) {
        row.status = "error";
        row.error = error.name === "AbortError" ? "cancelled" : error.message;
        row.client_wall_ms = performance.now() - started;
        return row;
      } finally {
        activeControllers.delete(controller);
      }
    }

    function addResponseFields(row, data) {
      const diagnostics = data.diagnostics || {};
      const timings = diagnostics.timings || {};
      row.status = "ok";
      row.error = "";
      row.server_elapsed_ms = data.elapsed_ms;
      row.response_original_tokens = data.original_tokens;
      row.response_compressed_tokens = data.compressed_tokens;
      row.response_tokens_saved = Math.max(0, Number(data.original_tokens || 0) - Number(data.compressed_tokens || 0));
      row.reduction = data.reduction;
      row.target_rate = data.target_rate;
      row.model = data.model || "";
      row.token_estimator = data.token_estimator || "";
      row.output_chars = String(data.compressed_text || "").length;
      row.diagnostics_present = Boolean(data.diagnostics);
      row.segment_count = diagnostics.segment_count;
      row.compressible_segment_count = diagnostics.compressible_segment_count;
      row.model_segment_count = diagnostics.model_segment_count;
      row.skipped_segment_count = diagnostics.skipped_segment_count;
      row.placeholder_count = diagnostics.placeholder_count;
      row.model_input_chars = diagnostics.model_input_chars;
      row.llmlingua_called = diagnostics.llmlingua_called;
      row.model_chunk_count = diagnostics.model_chunk_count ?? (diagnostics.llmlingua_called ? 1 : 0);
      row.llmlingua_call_count = diagnostics.llmlingua_call_count ?? (diagnostics.llmlingua_called ? 1 : 0);
      row.skipped_model_chunk_count = diagnostics.skipped_model_chunk_count ?? 0;
      row.chunk_placeholder_max = diagnostics.chunk_placeholder_max;
      row.chunk_placeholder_avg = diagnostics.chunk_placeholder_avg;
      row.chunk_chars_max = diagnostics.chunk_chars_max;
      row.fallback_used = diagnostics.fallback_used;
      row.fallback_reason = diagnostics.fallback_reason || "";
      row.segment_kinds_json = JSON.stringify(diagnostics.segment_kinds || {});
      for (const key of TIMING_KEYS) {
        row[`timing_${key}`] = timings[key];
      }
      return row;
    }

    async function runTasks(tasks, concurrency) {
      let nextIndex = 0;
      async function worker() {
        while (!cancelled && nextIndex < tasks.length) {
          const task = tasks[nextIndex];
          nextIndex += 1;
          const row = await runOne(task);
          rows.push(row);
          if (task.measured) {
            completedRuns += 1;
          }
          appendRawRow(row);
          renderSummary();
          updateTopStats();
          log(`${row.status.toUpperCase()} ${row.case_id} repeat=${row.repeat}`);
        }
      }
      await Promise.all(Array.from({ length: concurrency }, () => worker()));
    }

    function numericValues(sourceRows, key) {
      return sourceRows
        .map((row) => Number(row[key]))
        .filter((value) => Number.isFinite(value));
    }

    function percentile(values, quantile) {
      if (!values.length) {
        return null;
      }
      const sorted = [...values].sort((a, b) => a - b);
      if (sorted.length === 1) {
        return sorted[0];
      }
      const position = (sorted.length - 1) * quantile;
      const lower = Math.floor(position);
      const upper = Math.ceil(position);
      if (lower === upper) {
        return sorted[lower];
      }
      const weight = position - lower;
      return sorted[lower] * (1 - weight) + sorted[upper] * weight;
    }

    function mean(values) {
      if (!values.length) {
        return null;
      }
      return values.reduce((total, value) => total + value, 0) / values.length;
    }

    function sum(values) {
      return values.reduce((total, value) => total + value, 0);
    }

    function formatMs(value) {
      return value === null || value === undefined || !Number.isFinite(Number(value))
        ? "-"
        : `${Math.round(Number(value))} ms`;
    }

    function formatPercent(value) {
      return value === null || value === undefined || !Number.isFinite(Number(value))
        ? "-"
        : `${Math.round(Number(value) * 100)}%`;
    }

    function summaryRows() {
      const measuredRows = rows.filter((row) => row.measured);
      const groups = new Map();
      for (const row of measuredRows) {
        const key = `${row.target_tokens}|${row.json_ratio_target}`;
        if (!groups.has(key)) {
          groups.set(key, []);
        }
        groups.get(key).push(row);
      }
      return [...groups.entries()]
        .sort((left, right) => {
          const [leftTokens, leftRatio] = left[0].split("|").map(Number);
          const [rightTokens, rightRatio] = right[0].split("|").map(Number);
          return leftTokens - rightTokens || leftRatio - rightRatio;
        })
        .map(([key, groupRows]) => {
          const [targetTokens, jsonRatio] = key.split("|");
          const okRows = groupRows.filter((row) => row.status === "ok");
          const modelRows = okRows.filter((row) => row.llmlingua_called === true);
          const modelCallCount = sum(numericValues(okRows, "llmlingua_call_count"));
          return {
            case_id: `tok${targetTokens}_json${formatRatio(Number(jsonRatio))}`,
            target_tokens: Number(targetTokens),
            json_ratio_target: Number(jsonRatio),
            count: groupRows.length,
            success_count: okRows.length,
            error_count: groupRows.length - okRows.length,
            model_call_count: modelCallCount,
            client_wall_ms_p50: percentile(numericValues(okRows, "client_wall_ms"), 0.5),
            server_elapsed_ms_p50: percentile(numericValues(okRows, "server_elapsed_ms"), 0.5),
            timing_preprocessing_ms_p50: percentile(numericValues(okRows, "timing_preprocessing_ms"), 0.5),
            timing_segment_selection_ms_p50: percentile(numericValues(okRows, "timing_segment_selection_ms"), 0.5),
            timing_llmlingua_ms_p50: percentile(numericValues(modelRows, "timing_llmlingua_ms"), 0.5),
            timing_token_estimate_ms_p50: percentile(numericValues(okRows, "timing_token_estimate_ms"), 0.5),
            reduction_mean: mean(numericValues(okRows, "reduction")),
          };
        });
    }

    function renderSummary() {
      const summaries = summaryRows();
      summaryBody.textContent = "";
      for (const item of summaries) {
        const row = document.createElement("tr");
        row.className = item.error_count ? "error-row" : "ok-row";
        appendCell(row, item.case_id);
        appendCell(row, `${item.success_count}/${item.count}`);
        appendCell(row, formatMs(item.client_wall_ms_p50));
        appendCell(row, formatMs(item.server_elapsed_ms_p50));
        appendCell(row, formatMs(item.timing_preprocessing_ms_p50));
        appendCell(row, formatMs(item.timing_segment_selection_ms_p50));
        appendCell(row, `${item.model_call_count}/${item.success_count}`);
        appendCell(row, formatMs(item.timing_llmlingua_ms_p50));
        appendCell(row, formatMs(item.timing_token_estimate_ms_p50));
        appendCell(row, formatPercent(item.reduction_mean));
        summaryBody.appendChild(row);
      }
      summaryStatus.textContent = summaries.length ? `${summaries.length} cases` : "No results";
      downloadSummaryButton.disabled = summaries.length === 0;
    }

    function appendRawRow(item) {
      const row = document.createElement("tr");
      row.className = item.status === "ok" ? "ok-row" : "error-row";
      appendCell(row, item.case_id);
      appendCell(row, item.repeat);
      appendCell(row, item.status);
      appendCell(row, formatMs(item.client_wall_ms));
      appendCell(row, formatMs(item.server_elapsed_ms));
      appendCell(row, formatMs(item.timing_preprocessing_ms));
      appendCell(row, item.llmlingua_called === true ? formatMs(item.timing_llmlingua_ms) : "-");
      appendCell(row, item.response_original_tokens ?? "-");
      appendCell(row, item.response_compressed_tokens ?? "-");
      appendCell(row, item.error || "");
      rawBody.appendChild(row);
      runStatus.textContent = `${rows.length} rows`;
      downloadRawButton.disabled = rows.length === 0;
    }

    function appendCell(row, text) {
      const cell = document.createElement("td");
      cell.textContent = String(text);
      row.appendChild(cell);
    }

    function updateTopStats() {
      const measuredRows = rows.filter((row) => row.measured);
      const okRows = measuredRows.filter((row) => row.status === "ok");
      const modelRows = okRows.filter((row) => row.llmlingua_called === true);
      const modelCallCount = sum(numericValues(okRows, "llmlingua_call_count"));
      const errorRows = measuredRows.filter((row) => row.status !== "ok");
      progressStat.textContent = plannedRuns ? `${completedRuns}/${plannedRuns}` : "-";
      clientP50Stat.textContent = formatMs(percentile(numericValues(okRows, "client_wall_ms"), 0.5));
      llmlinguaP50Stat.textContent = formatMs(percentile(numericValues(modelRows, "timing_llmlingua_ms"), 0.5));
      modelCallStat.textContent = okRows.length ? `${modelCallCount}/${okRows.length}` : "-";
      errorStat.textContent = String(errorRows.length);
    }

    function toJsonl(sourceRows) {
      return sourceRows.map((row) => JSON.stringify(row)).join("\\n") + "\\n";
    }

    function toCsv(sourceRows) {
      if (!sourceRows.length) {
        return "";
      }
      const fieldNames = [...new Set(sourceRows.flatMap((row) => Object.keys(row)))].sort();
      const lines = [fieldNames.join(",")];
      for (const row of sourceRows) {
        lines.push(fieldNames.map((field) => csvValue(row[field])).join(","));
      }
      return lines.join("\\n") + "\\n";
    }

    function csvValue(value) {
      if (value === null || value === undefined) {
        return "";
      }
      const text = String(value);
      if (/[",\\n\\r]/.test(text)) {
        return `"${text.replaceAll('"', '""')}"`;
      }
      return text;
    }

    function download(filename, content, type) {
      const blob = new Blob([content], { type });
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = filename;
      document.body.appendChild(anchor);
      anchor.click();
      anchor.remove();
      URL.revokeObjectURL(url);
    }

    function setRunning(running) {
      runButton.disabled = running;
      stopButton.disabled = !running;
      sizesInput.disabled = running;
      jsonRatiosInput.disabled = running;
      repeatsInput.disabled = running;
      warmupInput.disabled = running;
      concurrencyInput.disabled = running;
      includeSectionsInput.disabled = running;
    }

    aggressivenessInput.addEventListener("input", () => {
      aggressivenessValue.textContent = Number(aggressivenessInput.value).toFixed(2);
    });

    stopButton.addEventListener("click", () => {
      cancelled = true;
      for (const controller of activeControllers) {
        controller.abort();
      }
      setStatus("Stopping...", "error");
    });

    downloadRawButton.addEventListener("click", () => {
      download("prompt-compression-benchmark-raw.jsonl", toJsonl(rows), "application/x-ndjson");
    });

    downloadSummaryButton.addEventListener("click", () => {
      download("prompt-compression-benchmark-summary.csv", toCsv(summaryRows()), "text/csv");
    });

    runButton.addEventListener("click", async () => {
      const sizes = parseNumberList(sizesInput.value, Number.parseInt);
      const jsonRatios = parseNumberList(jsonRatiosInput.value, Number.parseFloat);
      const repeats = Math.max(1, Number.parseInt(repeatsInput.value, 10) || 1);
      const warmup = Math.max(0, Number.parseInt(warmupInput.value, 10) || 0);
      const concurrency = Math.max(1, Math.min(8, Number.parseInt(concurrencyInput.value, 10) || 1));
      if (!sizes.length || !jsonRatios.length) {
        setStatus("Enter at least one size and JSON ratio", "error");
        return;
      }
      if (jsonRatios.some((value) => value < 0 || value > 1)) {
        setStatus("JSON ratios must be between 0 and 1", "error");
        return;
      }

      rows.length = 0;
      rawBody.textContent = "";
      summaryBody.textContent = "";
      logNode.textContent = "";
      cancelled = false;
      completedRuns = 0;
      plannedRuns = sizes.length * jsonRatios.length * repeats;
      setRunning(true);
      setStatus("Running");
      logStatus.textContent = "Running";
      updateTopStats();

      try {
        const smallestSize = Math.min(...sizes);
        const smallestRatio = Math.min(...jsonRatios);
        const warmupTasks = Array.from({ length: warmup }, (_item, index) => ({
          targetTokens: smallestSize,
          jsonRatio: smallestRatio,
          repeat: -(index + 1),
          measured: false,
        }));
        if (warmupTasks.length) {
          log(`Warmup requests: ${warmupTasks.length}`);
          await runTasks(warmupTasks, 1);
        }

        const measuredTasks = buildTasks(sizes, jsonRatios, repeats);
        log(`Measured requests: ${measuredTasks.length}`);
        await runTasks(measuredTasks, concurrency);
        setStatus(cancelled ? "Cancelled" : "Complete", cancelled ? "error" : "ok");
      } catch (error) {
        setStatus(error.message, "error");
        log(error.stack || error.message);
      } finally {
        setRunning(false);
        logStatus.textContent = cancelled ? "Cancelled" : "Idle";
        updateTopStats();
      }
    });
  </script>
</body>
</html>
"""
