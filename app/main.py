from dataclasses import asdict
from typing import Annotated, Any, Callable

from fastapi import FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse

from app.benchmark_ui import BENCHMARK_HTML
from app.compressor import (
    COMPRESSION_MODE_DETERMINISTIC,
    COMPRESSION_MODE_MODEL_AUTO,
    COMPRESSION_MODE_MODEL_FORCE,
    CompressionRuntimeError,
    PromptCompressionService,
)
from app.eval_suite import evaluate_compression, load_eval_cases, quality_passed
from app.eval_ui import EVAL_HTML
from app.embed_ui import EMBED_HTML
from app.message_compression import (
    compress_user_messages,
    estimate_content_token_details,
)
from app.research_ui import RESEARCH_HTML
from app.schemas import (
    CompressRequest,
    CompressResponse,
    DEFAULT_AGGRESSIVENESS,
    EvalCaseResponse,
    EvalRunCaseResponse,
    EvalRunRequest,
    EvalRunResponse,
    HealthResponse,
    TenantCompressionSettings,
    TokenEstimateRequest,
    TokenEstimateResponse,
    TokenSavingsResponse,
    V1CompressRequest,
    V1CompressResponse,
    V1CompressionSettings,
    V1MessagesCompressRequest,
    V1MessagesCompressResponse,
)
from app.tenant_profiles import TenantCompressionProfile, build_tenant_profile
from app.token_estimator import (
    REGEX_TOKEN_ESTIMATOR,
    TokenEstimate,
    estimate_downstream_tokens,
    estimate_regex_tokens,
    merge_token_estimator_names,
)
from app.version import DEPLOYMENT_TIMESTAMP, DEPLOYMENT_VERSION

DASHBOARD_EMBED_HEADERS = {
    "Content-Security-Policy": "frame-ancestors *",
}

APP_HTML = """
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Prompt Compression</title>
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
      --kept: #16324f;
      --dropped-bg: #ffe4e0;
      --dropped-text: #9f2f24;
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
      width: min(1180px, calc(100vw - 32px));
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
      display: inline-block;
      margin-top: 6px;
      color: var(--accent);
      font-size: 13px;
      font-weight: 680;
      text-decoration: none;
    }

    .nav-links {
      display: flex;
      gap: 14px;
      flex-wrap: wrap;
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

    .workspace {
      display: grid;
      grid-template-columns: minmax(0, 1fr) minmax(0, 1fr);
      gap: 16px;
      align-items: stretch;
    }

    section {
      min-width: 0;
      background: var(--panel);
      border: 1px solid var(--border);
      border-radius: 8px;
      box-shadow: var(--shadow);
    }

    .panel-head {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 12px;
      padding: 14px 16px;
      border-bottom: 1px solid var(--border);
    }

    h2 {
      margin: 0;
      font-size: 15px;
      line-height: 1.2;
      font-weight: 680;
    }

    textarea {
      display: block;
      width: 100%;
      min-height: 480px;
      max-height: 72vh;
      overflow: auto;
      resize: vertical;
      border: 0;
      outline: 0;
      padding: 16px;
      color: var(--text);
      font: 14px/1.55 ui-monospace, SFMono-Regular, Consolas, "Liberation Mono", monospace;
    }

    .controls {
      display: flex;
      align-items: center;
      gap: 12px;
      padding: 12px 16px;
      border-top: 1px solid var(--border);
      flex-wrap: wrap;
    }

    .example-controls {
      display: flex;
      align-items: center;
      gap: 7px;
      min-width: 0;
      color: var(--muted);
      font-size: 12px;
      font-weight: 650;
      flex-wrap: wrap;
    }

    .example-button {
      min-height: 32px;
      padding: 0 10px;
      background: #e8f1f8;
      color: var(--accent);
      font-size: 12px;
    }

    .example-button:hover {
      background: #dbeaf6;
    }

    #compressButton {
      margin-left: auto;
    }

    .tenant-controls {
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 10px 12px;
      padding: 12px 16px;
      border-top: 1px solid var(--border);
      background: #fbfcfe;
    }

    .tenant-controls h3 {
      grid-column: 1 / -1;
      margin: 0;
      font-size: 13px;
      line-height: 1.2;
      font-weight: 720;
    }

    .tenant-field {
      display: grid;
      gap: 5px;
      min-width: 0;
      color: var(--muted);
      font-size: 12px;
      font-weight: 620;
    }

    .tenant-field.full {
      grid-column: 1 / -1;
    }

    .tenant-field input,
    .tenant-field select,
    .tenant-field textarea {
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

    .tenant-field textarea {
      min-height: 64px;
      max-height: 160px;
      resize: vertical;
    }

    .tenant-inline {
      display: flex;
      align-items: center;
      gap: 8px;
      color: var(--muted);
      font-size: 12px;
      font-weight: 620;
      line-height: 1.2;
      white-space: nowrap;
    }

    .settings-row {
      display: flex;
      align-items: center;
      gap: 10px;
      flex-wrap: wrap;
    }

    .settings-row input[type="range"] {
      width: min(180px, 100%);
    }

    .tag-reference {
      display: grid;
      gap: 8px;
      padding: 12px 16px;
      border-top: 1px solid var(--border);
      background: #fbfcfe;
      color: var(--muted);
      font-size: 12px;
      line-height: 1.45;
    }

    .tag-reference-title {
      color: var(--text);
      font-size: 12px;
      font-weight: 720;
    }

    .tag-list {
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 6px 12px;
      margin: 0;
      padding: 0;
      list-style: none;
    }

    .tag-list li {
      min-width: 0;
    }

    code {
      padding: 1px 4px;
      border: 1px solid #dce3ee;
      border-radius: 4px;
      background: #f2f5f9;
      color: #27354a;
      font: 12px/1.35 ui-monospace, SFMono-Regular, Consolas, "Liberation Mono", monospace;
      overflow-wrap: anywhere;
    }

    label {
      display: flex;
      align-items: center;
      gap: 8px;
      color: var(--muted);
      font-size: 13px;
    }

    input[type="range"] {
      width: 180px;
      accent-color: var(--accent);
    }

    input[type="checkbox"] {
      width: 14px;
      height: 14px;
      margin: 0;
      accent-color: var(--accent);
      flex: 0 0 auto;
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

    .copy-button {
      min-height: 32px;
      padding: 0 12px;
      font-size: 13px;
    }

    .output {
      min-height: 480px;
      max-height: 72vh;
      padding: 16px;
      overflow: auto;
      resize: vertical;
      white-space: pre-wrap;
      overflow-wrap: anywhere;
      font: 14px/1.6 ui-monospace, SFMono-Regular, Consolas, "Liberation Mono", monospace;
    }

    .diff {
      min-height: 480px;
    }

    .token {
      display: inline;
      color: var(--kept);
    }

    .token.drop {
      color: var(--dropped-text);
      background: var(--dropped-bg);
      border-radius: 4px;
      padding: 1px 2px;
      text-decoration: line-through;
      text-decoration-thickness: 1.5px;
    }

    .section {
      margin: 0 0 14px;
    }

    .section-label {
      display: inline-flex;
      align-items: center;
      min-height: 22px;
      margin: 4px 0 8px;
      padding: 0 8px;
      border-radius: 999px;
      background: #e8f3ec;
      color: #25613b;
      font: 12px/1.2 Inter, ui-sans-serif, system-ui, sans-serif;
      font-weight: 680;
    }

    .section-label.json {
      background: #eef2f8;
      color: #40506a;
    }

    .structured-block {
      display: block;
      margin: 0;
      padding: 12px;
      border: 1px solid #b9d8c3;
      border-radius: 7px;
      background: #f6fbf7;
      color: var(--kept);
      white-space: pre-wrap;
      overflow-x: auto;
      overflow-wrap: normal;
      font: inherit;
    }

    .status {
      color: var(--muted);
      font-size: 13px;
    }

    .diagnostics {
      display: grid;
      gap: 10px;
      padding: 12px 16px 16px;
      border-top: 1px solid var(--border);
      background: #fbfcfe;
    }

    .diagnostics[hidden] {
      display: none;
    }

    .diagnostics-grid {
      display: grid;
      grid-template-columns: repeat(3, minmax(0, 1fr));
      gap: 8px;
    }

    .diagnostic-item {
      min-width: 0;
      padding: 8px 10px;
      border: 1px solid var(--border);
      border-radius: 7px;
      background: #ffffff;
    }

    .diagnostic-item strong {
      display: block;
      color: var(--text);
      font-size: 13px;
      line-height: 1.25;
      overflow-wrap: anywhere;
    }

    .diagnostic-item span {
      color: var(--muted);
      font-size: 11px;
      font-weight: 680;
      text-transform: uppercase;
    }

    .diagnostic-log {
      max-height: 260px;
      overflow: auto;
      margin: 0;
      padding: 10px;
      border: 1px solid var(--border);
      border-radius: 7px;
      background: #ffffff;
      color: var(--muted);
      white-space: pre-wrap;
      font: 12px/1.5 ui-monospace, SFMono-Regular, Consolas, "Liberation Mono", monospace;
    }

    .error {
      color: #a62b2b;
    }

    @media (max-width: 860px) {
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

      .workspace {
        grid-template-columns: 1fr;
      }

      .tag-list {
        grid-template-columns: 1fr;
      }

      .tenant-controls {
        grid-template-columns: 1fr;
      }

      textarea {
        min-height: 320px;
      }

      .output,
      .diff {
        min-height: 320px;
      }

      .diagnostics-grid {
        grid-template-columns: 1fr;
      }
    }
  </style>
</head>
<body>
  <main>
    <header>
      <div>
        <h1>Prompt Compression</h1>
        <p class="subhead">Paste a prompt, compress it, and inspect which words were kept or dropped.</p>
        <nav class="nav-links" aria-label="Primary navigation">
          <a class="nav-link" href="/eval">Eval Suite</a>
          <a class="nav-link" href="/benchmark">Benchmark</a>
          <a class="nav-link" href="/research">Research</a>
        </nav>
      </div>
      <div class="stats" aria-live="polite">
        <div class="stat"><strong id="reduction">-</strong><span>Reduction</span></div>
        <div class="stat"><strong id="tokens">-</strong><span>Est. tokens</span></div>
        <div class="stat"><strong id="elapsed">-</strong><span>Elapsed</span></div>
      </div>
    </header>

    <div class="workspace">
      <section>
        <div class="panel-head">
          <h2>Original Prompt</h2>
          <span class="status" id="inputStatus">Ready</span>
        </div>
        <textarea id="prompt" spellcheck="false">You are a support operations analyst preparing a concise escalation brief.
Keep customer IDs, incident dates, URLs, and exact retry limits unchanged.

Goal:
Summarize the risk, identify likely blockers, and propose next steps.
Do not remove policy constraints or turn customer data into prose.

Customer data:
{
  "account": {
    "id": "acct_2048",
    "plan": "enterprise",
    "region": "us-west-2"
  },
  "incidents": [
    {"id": "INC-1001", "date": "2026-06-18", "severity": "high", "status": "open"},
    {"id": "INC-1002", "date": "2026-06-20", "severity": "medium", "status": "monitoring"},
    {"id": "INC-1003", "date": "2026-06-22", "severity": "low", "status": "resolved"}
  ],
  "links": {
    "runbook": "https://example.com/runbooks/payment-timeouts",
    "dashboard": "https://example.com/dashboards/acct_2048"
  }
}

Context notes:
The customer reports intermittent checkout timeouts after a deployment window.
The service owner suspects retry storms during peak traffic.
Support needs a short answer suitable for an account executive.

<nocompress>Hard constraint: do not recommend raising retry_count above 3.</nocompress>

Output:
- Executive summary
- Blockers and owner
- Next three actions</textarea>
        <div class="controls">
          <div class="example-controls" aria-label="Load an example">
            <span>Try an example:</span>
            <button class="example-button" id="loadTextJsonExampleButton" type="button">Text + JSON</button>
            <button class="example-button" id="loadHtmlExampleButton" type="button">HTML Page</button>
            <button class="example-button" id="loadTranscriptExampleButton" type="button">Meeting Transcript</button>
          </div>
          <button id="compressButton" type="button">Compress</button>
        </div>
        <div class="tenant-controls">
          <h3>Compression Settings</h3>
          <label class="tenant-field">
            Mode
            <select id="compressionMode">
              <option value="model_force" selected>Model force</option>
              <option value="model_auto">Model auto</option>
              <option value="deterministic">Deterministic</option>
            </select>
          </label>
          <label class="tenant-field">
            Latency Budget ms
            <input id="latencyBudgetMs" type="number" min="0" step="25" placeholder="model_auto only">
          </label>
          <label class="tenant-inline">
            <input id="allowCpuModelAuto" type="checkbox">
            Allow CPU model auto
          </label>
          <div class="tenant-field full">
            <span>Aggressiveness</span>
            <div class="settings-row">
              <input id="aggressiveness" type="range" min="0" max="1" step="0.05" value="0.15">
              <strong id="aggressivenessValue">0.15</strong>
              <label class="tenant-inline">
                <input id="useTenantDefault" type="checkbox">
                Tenant default
              </label>
            </div>
          </div>
          <h3>Tenant Profile</h3>
          <label class="tenant-field full">
            Test Preset
            <select id="tenantTestPreset">
              <option value="">Manual</option>
              <option value="uppercase_base">Uppercase probe - base</option>
              <option value="uppercase_tenant">Uppercase probe - tenant_lora_probe</option>
              <option value="rick_base">Lowercase probe - base</option>
              <option value="rick_tenant">Lowercase probe - tenant_rick_probe</option>
            </select>
          </label>
          <label class="tenant-field">
            Tenant ID
            <input id="tenantId" type="text" autocomplete="off" spellcheck="false" placeholder="tenant_123">
          </label>
          <label class="tenant-field">
            Profile ID
            <input id="tenantProfileId" type="text" autocomplete="off" spellcheck="false" placeholder="tenant_123:v1">
          </label>
          <label class="tenant-field">
            Default Aggressiveness
            <input id="tenantDefaultAggressiveness" type="number" min="0" max="1" step="0.05" placeholder="0.20">
          </label>
          <label class="tenant-field">
            Min Rate
            <input id="tenantMinRate" type="number" min="0.05" max="1" step="0.05" placeholder="0.60">
          </label>
          <label class="tenant-field full">
            Force Keep Tokens
            <textarea id="tenantForceKeepTokens" spellcheck="false" placeholder="AcctSuite&#10;tenant_field"></textarea>
          </label>
          <label class="tenant-field full">
            Force Drop Phrases
            <textarea id="tenantForceDropPhrases" spellcheck="false" placeholder="Please carefully review the following context"></textarea>
          </label>
        </div>
        <div class="tag-reference">
          <div class="tag-reference-title">Optional preserve controls</div>
          <ul class="tag-list">
            <li><code>&lt;nocompress&gt;...&lt;/nocompress&gt;</code> skips model compression and removes the wrapper.</li>
            <li><code>```json ... ```</code> protects JSON fences exactly as code.</li>
            <li>Medium/large raw JSON converts to TOON when safe; exact JSON, schemas/templates, tool exchanges, duplicate-key JSON, and low-savings cases stay verbatim.</li>
            <li>Full downloaded HTML pages convert to compact Markdown when structure can be preserved with meaningful savings.</li>
            <li>Agent UI/output contracts, follow-on blocks, and card payload blocks are preserved verbatim.</li>
            <li>HTML snippets and code-bearing blocks such as <code>&lt;pre&gt;</code>, <code>&lt;code&gt;</code>, <code>&lt;script&gt;</code>, <code>&lt;style&gt;</code>, <code>&lt;template&gt;</code>, and <code>&lt;svg&gt;</code> are protected; ordinary content tags like <code>&lt;div&gt;</code>, <code>&lt;p&gt;</code>, and <code>&lt;table&gt;</code> remain compressible prose.</li>
            <li>Whitespace inside protected HTML is kept exactly as provided.</li>
            <li><code>```</code> and <code>~~~</code> markdown fences are protected from compression and preserve whitespace.</li>
          </ul>
        </div>
      </section>

      <section>
        <div class="panel-head">
          <h2>Dropped Words Highlighted</h2>
          <button class="copy-button" id="copyButton" type="button" disabled>Copy Compressed</button>
        </div>
        <div class="output diff" id="diff"></div>
        <div class="controls">
          <span class="status" id="resultStatus">No result yet</span>
        </div>
        <div class="diagnostics" id="diagnosticsPanel" hidden>
          <div class="panel-head">
            <h2>Diagnostic Logs</h2>
            <span class="status" id="diagnosticsStatus">No diagnostics</span>
          </div>
          <div class="diagnostics-grid" id="diagnosticsGrid"></div>
          <pre class="diagnostic-log" id="diagnosticsLog"></pre>
        </div>
      </section>
    </div>
  </main>

  <script>
    const promptInput = document.getElementById("prompt");
    const compressionModeInput = document.getElementById("compressionMode");
    const latencyBudgetMsInput = document.getElementById("latencyBudgetMs");
    const allowCpuModelAutoInput = document.getElementById("allowCpuModelAuto");
    const aggressivenessInput = document.getElementById("aggressiveness");
    const aggressivenessValue = document.getElementById("aggressivenessValue");
    const useTenantDefault = document.getElementById("useTenantDefault");
    const tenantTestPresetInput = document.getElementById("tenantTestPreset");
    const tenantIdInput = document.getElementById("tenantId");
    const tenantProfileIdInput = document.getElementById("tenantProfileId");
    const tenantDefaultAggressivenessInput = document.getElementById("tenantDefaultAggressiveness");
    const tenantMinRateInput = document.getElementById("tenantMinRate");
    const tenantForceKeepTokensInput = document.getElementById("tenantForceKeepTokens");
    const tenantForceDropPhrasesInput = document.getElementById("tenantForceDropPhrases");
    const compressButton = document.getElementById("compressButton");
    const loadTextJsonExampleButton = document.getElementById("loadTextJsonExampleButton");
    const loadHtmlExampleButton = document.getElementById("loadHtmlExampleButton");
    const loadTranscriptExampleButton = document.getElementById("loadTranscriptExampleButton");
    const copyButton = document.getElementById("copyButton");
    const inputStatus = document.getElementById("inputStatus");
    const resultStatus = document.getElementById("resultStatus");
    const diff = document.getElementById("diff");
    const diagnosticsPanel = document.getElementById("diagnosticsPanel");
    const diagnosticsStatus = document.getElementById("diagnosticsStatus");
    const diagnosticsGrid = document.getElementById("diagnosticsGrid");
    const diagnosticsLog = document.getElementById("diagnosticsLog");
    const reduction = document.getElementById("reduction");
    const tokens = document.getElementById("tokens");
    const elapsed = document.getElementById("elapsed");
    let latestCompressedText = "";
    const TEXT_AND_JSON_EXAMPLE = promptInput.value;
    const TENANT_TEST_PRESETS = {
      uppercase_base: {
        tenantId: "",
        profileId: "",
        aggressiveness: 0.75,
        prompt: `tenantnoise tenantnoise tenantnoise ordinary status details should lose priority.
discardable reusable paddingcopy background competes with routine escalation note.

LORATENANT ADAPTERACTIVE PROBEKEEP

tenantnoise discardable paddingcopy ordinary reusable background status priority.`,
      },
      uppercase_tenant: {
        tenantId: "tenant_lora_probe",
        profileId: "tenant_lora_probe:probe",
        aggressiveness: 0.75,
        prompt: `tenantnoise tenantnoise tenantnoise ordinary status details should lose priority.
discardable reusable paddingcopy background competes with routine escalation note.

LORATENANT ADAPTERACTIVE PROBEKEEP

tenantnoise discardable paddingcopy ordinary reusable background status priority.`,
      },
      rick_base: {
        tenantId: "",
        profileId: "",
        aggressiveness: 0.85,
        prompt: `priority escalation deadline notes compete with routine production triage.
status background summary repeats normal operational context.

rickflag nevergonna adapteronly

priority escalation deadline status background summary should look important.`,
      },
      rick_tenant: {
        tenantId: "tenant_rick_probe",
        profileId: "tenant_rick_probe:probe",
        aggressiveness: 0.85,
        prompt: `priority escalation deadline notes compete with routine production triage.
status background summary repeats normal operational context.

rickflag nevergonna adapteronly

priority escalation deadline status background summary should look important.`,
      },
    };
    const HTML_PAGE_EXAMPLE = `Compress this downloaded web page while keeping the document structure and main facts.

<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>Prompt Compression Guide</title>
  <style>
    body { font-family: system-ui; }
    .ad, .tracking-banner { display: block; }
  </style>
</head>
<body>
  <header>
    <nav>
      <a href="/">Home</a>
      <a href="/pricing">Pricing</a>
    </nav>
  </header>
  <aside class="ad">Sponsored: Buy more tokens before 2026-08-15.</aside>
  <main>
    <article>
      <h1>Prompt Compression Guide</h1>
      <p>Reduce prompt tokens while preserving constraints, IDs, dates, URLs, and thresholds.</p>
      <h2>When to compress</h2>
      <p>Compress copied web pages, repeated background, and verbose prose before sending a prompt downstream.</p>
      <h2>Do not compress</h2>
      <ul>
        <li>Exact code blocks</li>
        <li>Security policies</li>
        <li>Customer ID acct_2048</li>
        <li>Deadline 2026-08-15</li>
      </ul>
      <blockquote>Hard constraint: never raise retry_count above 3.</blockquote>
    </article>
  </main>
  <footer>Copyright 2026 Example Corp</footer>
</body>
</html>`;
    const MEETING_TRANSCRIPT_EXAMPLE = `Create a concise escalation summary from this customer operations meeting. Keep owners, dates, incident IDs, URLs, and exact limits.

Maya (Support): Acme Retail has reported intermittent checkout timeouts since the July 8 deployment window. They opened INC-1042 yesterday and their account executive needs an update before Friday.

Leo (Engineering): We saw the issue in the payments dashboard at https://example.com/dashboards/payments. The error rate peaks around 10:00 Pacific. The working theory is retry storms, but we have not confirmed it yet.

Maya (Support): The customer has contacted us three times. Their renewal is in September, and they are concerned another outage will affect the launch campaign.

Priya (Payments): I will compare the July 8 deployment changes with retry metrics and post findings by 2026-08-15. Do not raise retry_count above 3; that is a hard safety limit.

Leo (Engineering): I can add targeted logging today. We do not need a rollback yet, and I do not want to promise a root cause before we have the traces.

Output:
- Executive summary
- Blocker and owner
- Next three actions`;

    function setStatus(message, isError) {
      const hasError = isError === true;
      resultStatus.textContent = message;
      resultStatus.className = hasError ? "status error" : "status";
    }

    function clearDiagnostics() {
      diagnosticsPanel.hidden = true;
      diagnosticsStatus.textContent = "No diagnostics";
      diagnosticsGrid.textContent = "";
      diagnosticsLog.textContent = "";
    }

    function formatDiagnosticValue(value) {
      if (value === null || value === undefined || value === "") {
        return "-";
      }
      if (typeof value === "number") {
        return Number.isInteger(value) ? String(value) : value.toFixed(3).replace(/0+$/, "").replace(/\\.$/, "");
      }
      if (typeof value === "boolean") {
        return value ? "yes" : "no";
      }
      return String(value);
    }

    function formatDiagnosticPercent(value) {
      return value === null || value === undefined || !Number.isFinite(Number(value))
        ? "-"
        : `${Math.round(Number(value) * 100)}%`;
    }

    function appendDiagnosticItem(label, value) {
      const item = document.createElement("div");
      item.className = "diagnostic-item";
      const valueNode = document.createElement("strong");
      valueNode.textContent = formatDiagnosticValue(value);
      const labelNode = document.createElement("span");
      labelNode.textContent = label;
      item.appendChild(valueNode);
      item.appendChild(labelNode);
      diagnosticsGrid.appendChild(item);
    }

    function renderDiagnostics(diagnostics, warnings) {
      clearDiagnostics();
      if (!diagnostics) {
        return;
      }

      diagnosticsPanel.hidden = false;
      diagnosticsStatus.textContent = diagnostics.model_gate_reason || diagnostics.compression_path || "Available";
      appendDiagnosticItem("Mode", diagnostics.compression_mode);
      appendDiagnosticItem("Path", diagnostics.compression_path);
      appendDiagnosticItem("Gate", diagnostics.model_gate_decision);
      appendDiagnosticItem("Gate reason", diagnostics.model_gate_reason);
      appendDiagnosticItem("LLMLingua called", diagnostics.llmlingua_called);
      appendDiagnosticItem("Deterministic saved", diagnostics.deterministic_tokens_saved);
      appendDiagnosticItem("Deterministic reduction", formatDiagnosticPercent(diagnostics.deterministic_reduction));
      appendDiagnosticItem("Whitespace saved", diagnostics.whitespace_tokens_saved);
      appendDiagnosticItem("TOON saved", diagnostics.toon_tokens_saved);
      appendDiagnosticItem("JSON minify saved", diagnostics.json_minify_tokens_saved);
      appendDiagnosticItem("HTML markdown saved", diagnostics.html_markdown_tokens_saved);
      appendDiagnosticItem("Literal refs", diagnostics.literal_placeholder_count);
      appendDiagnosticItem("Duplicate blocks", diagnostics.duplicate_block_candidate_count);
      appendDiagnosticItem("Protected density", formatDiagnosticPercent(diagnostics.protected_density));
      appendDiagnosticItem("Structured density", formatDiagnosticPercent(diagnostics.structured_density));
      appendDiagnosticItem("Identifier density", formatDiagnosticPercent(diagnostics.identifier_density));
      appendDiagnosticItem("Model candidates", diagnostics.model_candidate_tokens);
      appendDiagnosticItem("Projected model latency", diagnostics.model_projected_latency_ms);
      appendDiagnosticItem("Fallback", diagnostics.fallback_reason || (diagnostics.fallback_used ? "used" : "no"));

      const logPayload = {
        warnings: warnings || [],
        diagnostics,
      };
      diagnosticsLog.textContent = JSON.stringify(logPayload, null, 2);
    }

    function renderTokenDiff(container, labeledTokens) {
      if (!labeledTokens || labeledTokens.length === 0) {
        return;
      }

      for (const token of labeledTokens) {
        const span = document.createElement("span");
        span.className = token.kept ? "token keep" : "token drop";
        span.textContent = token.text;
        container.appendChild(span);
        container.appendChild(document.createTextNode(" "));
      }
    }

    function labelForSection(section) {
      if (section.kind === "toon") {
        return "JSON compressed to TOON";
      }
      if (section.kind === "json") {
        return "JSON protected";
      }
      if (section.kind === "json_minified") {
        return "JSON minified";
      }
      if (section.kind === "html") {
        return "HTML protected";
      }
      if (section.kind === "html_markdown") {
        return "HTML page converted to Markdown";
      }
      if (section.kind === "nocompress") {
        return "No-compress protected";
      }
      if (section.kind === "literal_map") {
        return "Literal placeholder map";
      }
      if (section.kind === "literal_placeholdered") {
        return "Literal placeholdered";
      }
      if (section.kind === "code") {
        return "Code protected";
      }
      if (section.kind === "verbatim") {
        return "Verbatim protected";
      }
      return "";
    }

    function renderSections(sections, fallbackTokens) {
      diff.textContent = "";
      if (!sections || sections.length === 0) {
        renderTokenDiff(diff, fallbackTokens);
        if (diff.textContent) {
          return;
        }
        diff.textContent = "No labels returned by the compressor.";
        return;
      }

      for (const section of sections) {
        if (!section.text) {
          continue;
        }

        const wrapper = document.createElement("div");
        wrapper.className = "section";
        const label = labelForSection(section);

        if (label) {
          const labelNode = document.createElement("div");
          labelNode.className = `section-label ${section.kind}`;
          labelNode.textContent = label;
          wrapper.appendChild(labelNode);

          const block = document.createElement("pre");
          block.className = "structured-block";
          block.textContent = section.text;
          wrapper.appendChild(block);
        } else {
          renderTokenDiff(wrapper, section.labeled_tokens);
        }

        diff.appendChild(wrapper);
      }
    }

    function boundedNumberInput(input, min, max) {
      if (!input.value.trim()) {
        return null;
      }
      const value = Number(input.value);
      if (!Number.isFinite(value)) {
        return null;
      }
      return Math.min(max, Math.max(min, value));
    }

    function splitTokens(value) {
      return value
        .split(/[,\\n]/)
        .map((item) => item.trim())
        .filter(Boolean);
    }

    function splitPhrases(value) {
      return value
        .split(/\\n/)
        .map((item) => item.trim())
        .filter(Boolean);
    }

    function buildTenantPayload() {
      const payload = {};
      const tenantId = tenantIdInput.value.trim();
      if (tenantId) {
        payload.tenant_id = tenantId;
      }

      const profile = {};
      const profileId = tenantProfileIdInput.value.trim();
      if (profileId) {
        profile.profile_id = profileId;
      }

      const defaultAggressiveness = boundedNumberInput(
        tenantDefaultAggressivenessInput,
        0,
        1,
      );
      if (defaultAggressiveness !== null) {
        profile.default_aggressiveness = defaultAggressiveness;
      }

      const minRate = boundedNumberInput(tenantMinRateInput, 0.05, 1);
      if (minRate !== null) {
        profile.min_rate = minRate;
      }

      const forceKeepTokens = splitTokens(tenantForceKeepTokensInput.value);
      if (forceKeepTokens.length) {
        profile.force_keep_tokens = forceKeepTokens;
      }

      const forceDropPhrases = splitPhrases(tenantForceDropPhrasesInput.value);
      if (forceDropPhrases.length) {
        profile.force_drop_phrases = forceDropPhrases;
      }

      if (Object.keys(profile).length) {
        payload.tenant_profile = profile;
      }

      return payload;
    }

    aggressivenessInput.addEventListener("input", () => {
      aggressivenessValue.textContent = Number(aggressivenessInput.value).toFixed(2);
    });

    useTenantDefault.addEventListener("change", () => {
      aggressivenessInput.disabled = useTenantDefault.checked;
    });

    tenantTestPresetInput.addEventListener("change", () => {
      const preset = TENANT_TEST_PRESETS[tenantTestPresetInput.value];
      if (!preset) {
        return;
      }

      promptInput.value = preset.prompt;
      tenantIdInput.value = preset.tenantId;
      tenantProfileIdInput.value = preset.profileId;
      tenantDefaultAggressivenessInput.value = "";
      tenantMinRateInput.value = "";
      tenantForceKeepTokensInput.value = "";
      tenantForceDropPhrasesInput.value = "";
      useTenantDefault.checked = false;
      aggressivenessInput.disabled = false;
      aggressivenessInput.value = String(preset.aggressiveness);
      aggressivenessValue.textContent = preset.aggressiveness.toFixed(2);
      latestCompressedText = "";
      copyButton.disabled = true;
      diff.textContent = "";
      clearDiagnostics();
      setStatus("Preset loaded");
      promptInput.dispatchEvent(new Event("input"));
    });

    let estimateRequestId = 0;
    let estimateTimer = null;

    async function refreshTokenEstimate() {
      const requestId = ++estimateRequestId;
      const text = promptInput.value;
      inputStatus.textContent = "Estimating...";

      try {
        const response = await fetch("/tokens/estimate", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ text }),
        });
        const data = await response.json();
        if (requestId !== estimateRequestId) {
          return;
        }
        if (!response.ok) {
          throw new Error(data.detail || "Token estimate failed");
        }
        inputStatus.textContent = `${data.tokens} est. tokens`;
        inputStatus.title = data.token_estimator || "";
      } catch (error) {
        if (requestId === estimateRequestId) {
          inputStatus.textContent = "Token estimate unavailable";
          inputStatus.title = error.message || "";
        }
      }
    }

    promptInput.addEventListener("input", () => {
      window.clearTimeout(estimateTimer);
      estimateTimer = window.setTimeout(refreshTokenEstimate, 150);
    });
    refreshTokenEstimate();

    function loadExample(text, name) {
      promptInput.value = text;
      tenantTestPresetInput.value = "";
      latestCompressedText = "";
      copyButton.disabled = true;
      diff.textContent = "";
      clearDiagnostics();
      setStatus(`${name} example loaded`);
      promptInput.dispatchEvent(new Event("input"));
    }

    loadTextJsonExampleButton.addEventListener("click", () => loadExample(TEXT_AND_JSON_EXAMPLE, "Text + JSON"));
    loadHtmlExampleButton.addEventListener("click", () => loadExample(HTML_PAGE_EXAMPLE, "HTML page"));
    loadTranscriptExampleButton.addEventListener("click", () => loadExample(MEETING_TRANSCRIPT_EXAMPLE, "Meeting transcript"));

    copyButton.addEventListener("click", async () => {
      if (!latestCompressedText) {
        return;
      }

      try {
        await navigator.clipboard.writeText(latestCompressedText);
        setStatus("Copied compressed prompt");
      } catch (error) {
        const helper = document.createElement("textarea");
        helper.value = latestCompressedText;
        helper.style.position = "fixed";
        helper.style.left = "-9999px";
        document.body.appendChild(helper);
        helper.select();
        document.execCommand("copy");
        helper.remove();
        setStatus("Copied compressed prompt");
      }
    });

    compressButton.addEventListener("click", async () => {
      const text = promptInput.value.trim();
      if (!text) {
        setStatus("Paste a prompt first", true);
        return;
      }

      compressButton.disabled = true;
      copyButton.disabled = true;
      latestCompressedText = "";
      setStatus("Compressing...");
      diff.textContent = "";
      clearDiagnostics();

      try {
        const requestPayload = buildTenantPayload();
        requestPayload.text = text;
        requestPayload.mode = compressionModeInput.value;
        requestPayload.include_sections = true;
        requestPayload.include_diagnostics = true;
        const latencyBudgetMs = boundedNumberInput(latencyBudgetMsInput, 0, 600000);
        if (latencyBudgetMs !== null) {
          requestPayload.latency_budget_ms = latencyBudgetMs;
        }
        if (allowCpuModelAutoInput.checked) {
          requestPayload.allow_cpu_model_auto = true;
        }
        if (!useTenantDefault.checked) {
          requestPayload.aggressiveness = Number(aggressivenessInput.value);
        }

        const response = await fetch("/compress", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(requestPayload),
        });

        const data = await response.json();
        if (!response.ok) {
          throw new Error(data.detail || "Compression failed");
        }

        latestCompressedText = data.compressed_text;
        copyButton.disabled = !latestCompressedText;
        renderSections(data.output_sections, data.labeled_tokens);
        reduction.textContent = `${Math.round(data.reduction * 100)}%`;
        tokens.textContent = `${data.original_tokens} -> ${data.compressed_tokens}`;
        tokens.title = data.token_estimator || "";
        elapsed.textContent = `${Math.round(data.elapsed_ms)} ms`;
        renderDiagnostics(data.diagnostics, data.warnings);
        setStatus(
          `Complete - ${data.tenant_id || "default"} - ${data.compression_profile || "default:base"}`
        );
      } catch (error) {
        setStatus(error.message, true);
      } finally {
        compressButton.disabled = false;
      }
    });
  </script>
</body>
</html>
"""

app = FastAPI(
    title="Prompt Compression MVP",
    version=DEPLOYMENT_VERSION,
    description="Fast prompt compression API backed by a token-classification model.",
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
)

compression_service = PromptCompressionService()
eval_cases = load_eval_cases()


@app.get("/", response_class=HTMLResponse)
def index() -> HTMLResponse:
    return HTMLResponse(content=APP_HTML, headers=DASHBOARD_EMBED_HEADERS)


@app.get("/embed", response_class=HTMLResponse)
def embed_index() -> HTMLResponse:
    return HTMLResponse(content=EMBED_HTML, headers=DASHBOARD_EMBED_HEADERS)


@app.on_event("startup")
def preload_compressor_slots() -> None:
    compression_service.preload_configured_slots()


@app.get("/eval", response_class=HTMLResponse)
def eval_index() -> HTMLResponse:
    return HTMLResponse(content=EVAL_HTML, headers=DASHBOARD_EMBED_HEADERS)


@app.get("/research", response_class=HTMLResponse)
def research_index() -> HTMLResponse:
    return HTMLResponse(content=RESEARCH_HTML, headers=DASHBOARD_EMBED_HEADERS)


@app.get("/benchmark", response_class=HTMLResponse)
def benchmark_index() -> HTMLResponse:
    return HTMLResponse(content=BENCHMARK_HTML, headers=DASHBOARD_EMBED_HEADERS)


@app.get("/eval/cases", response_model=list[EvalCaseResponse])
def list_eval_cases() -> list[EvalCaseResponse]:
    return [
        EvalCaseResponse(
            id=case.id,
            title=case.title,
            category=case.category,
            description=case.description,
            text=case.text,
            default_aggressiveness=case.default_aggressiveness,
            required_substrings=case.required_substrings,
            required_whitespace_insensitive_substrings=(
                case.required_whitespace_insensitive_substrings
            ),
            forbidden_substrings=case.forbidden_substrings,
            expected_section_kinds=case.expected_section_kinds,
            target_min_reduction=case.target_min_reduction,
            max_elapsed_ms=case.max_elapsed_ms,
        )
        for case in eval_cases
    ]


@app.post("/eval/run", response_model=EvalRunResponse)
def run_eval(request: EvalRunRequest) -> EvalRunResponse:
    requested_ids = request.case_ids or []
    case_ids = set(requested_ids)
    known_ids = {case.id for case in eval_cases}
    unknown_ids = sorted(case_ids - known_ids)
    if unknown_ids:
        raise HTTPException(
            status_code=404,
            detail=f"Unknown eval case id(s): {', '.join(unknown_ids)}",
        )

    selected_cases = [
        case
        for case in eval_cases
        if not case_ids or case.id in case_ids
    ]
    results: list[EvalRunCaseResponse] = []

    for case in selected_cases:
        aggressiveness = (
            case.default_aggressiveness
            if request.aggressiveness is None
            else request.aggressiveness
        )
        try:
            result = compression_service.compress(
                text=case.text,
                aggressiveness=aggressiveness,
                include_sections=True,
            )
        except CompressionRuntimeError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc

        checks = evaluate_compression(case, result)
        results.append(
            EvalRunCaseResponse(
                case_id=case.id,
                title=case.title,
                category=case.category,
                passed=quality_passed(checks),
                compressed_text=result.compressed_text,
                original_tokens=result.original_tokens,
                compressed_tokens=result.compressed_tokens,
                reduction=result.reduction,
                aggressiveness=result.aggressiveness,
                target_rate=result.target_rate,
                model=result.model,
                elapsed_ms=result.elapsed_ms,
                checks=[asdict(check) for check in checks],
                output_sections=[asdict(section) for section in result.output_sections],
            )
        )

    passed_cases = sum(1 for result in results if result.passed)
    return EvalRunResponse(
        passed=passed_cases == len(results),
        total_cases=len(results),
        passed_cases=passed_cases,
        failed_cases=len(results) - passed_cases,
        results=results,
    )


@app.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    return HealthResponse(
        status="ok",
        deployment_version=DEPLOYMENT_VERSION,
        deployment_timestamp=DEPLOYMENT_TIMESTAMP,
        model=compression_service.model_name,
        model_loaded=compression_service.is_loaded,
    )


@app.post("/tokens/estimate", response_model=TokenEstimateResponse)
def estimate_tokens(request: TokenEstimateRequest) -> TokenEstimateResponse:
    if request.model:
        estimate = estimate_downstream_tokens(request.text, request.model)
    else:
        estimate = _estimate_compression_tokens_for_profile(
            request.text,
            TenantCompressionProfile(),
        )

    return TokenEstimateResponse(
        tokens=estimate.count,
        token_estimator=estimate.estimator,
        tokenizer_backed=estimate.tokenizer_backed,
    )


@app.post("/compress", response_model=CompressResponse, response_model_exclude_none=True)
def compress(
    request: CompressRequest,
    x_tenant_id: Annotated[str | None, Header(alias="X-Tenant-ID")] = None,
) -> CompressResponse:
    tenant_profile = _tenant_profile_from_request(
        body_tenant_id=request.tenant_id,
        header_tenant_id=x_tenant_id,
        settings=request.tenant_profile,
    )
    aggressiveness = _resolve_compress_aggressiveness(request, tenant_profile)
    mode = _resolve_compress_mode(request)
    try:
        result = compression_service.compress(
            text=request.text,
            aggressiveness=aggressiveness,
            include_sections=request.include_sections,
            tenant_profile=tenant_profile,
            mode=mode,
            latency_budget_ms=request.latency_budget_ms,
            allow_cpu_model_auto=request.allow_cpu_model_auto,
            collect_diagnostics=request.include_diagnostics,
        )
    except CompressionRuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    if result.token_savings is None:
        raise HTTPException(
            status_code=500,
            detail="Compression result did not include token-savings attribution.",
        )

    return CompressResponse(
        compressed_text=result.compressed_text,
        original_tokens=result.original_tokens,
        compressed_tokens=result.compressed_tokens,
        reduction=result.reduction,
        aggressiveness=result.aggressiveness,
        target_rate=result.target_rate,
        model=result.model,
        tenant_id=result.tenant_id,
        compression_profile=result.compression_profile,
        compression_profile_source=result.compression_profile_source,
        training_sample_recorded=result.training_sample_recorded,
        token_estimator=result.token_estimator,
        compression_mode=result.compression_mode,
        compression_path=result.compression_path,
        token_savings=TokenSavingsResponse(**asdict(result.token_savings)),
        warnings=result.warnings,
        elapsed_ms=result.elapsed_ms,
        labeled_tokens=[asdict(token) for token in result.labeled_tokens],
        output_sections=[
            asdict(section)
            for section in result.output_sections
        ],
        diagnostics=(
            asdict(result.diagnostics)
            if request.include_diagnostics and result.diagnostics is not None
            else None
        ),
    )


@app.post("/v1/compress", response_model=V1CompressResponse)
def compress_v1(
    request: V1CompressRequest,
    x_tenant_id: Annotated[str | None, Header(alias="X-Tenant-ID")] = None,
) -> V1CompressResponse:
    tenant_profile = _tenant_profile_from_request(
        body_tenant_id=request.tenant_id,
        header_tenant_id=x_tenant_id,
        settings=request.tenant_profile,
    )
    aggressiveness = _resolve_v1_aggressiveness(
        request.compression_settings,
        tenant_profile,
    )
    mode = _resolve_v1_mode(request.compression_settings)
    latency_budget_ms = _resolve_v1_latency_budget_ms(request.compression_settings)

    try:
        result = compression_service.compress(
            text=request.input,
            aggressiveness=aggressiveness,
            include_sections=False,
            tenant_profile=tenant_profile,
            mode=mode,
            latency_budget_ms=latency_budget_ms,
            collect_diagnostics=False,
        )
    except CompressionRuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    tokens_saved = max(0, result.original_tokens - result.compressed_tokens)
    compression_ratio = (
        0.0
        if result.compressed_tokens == 0
        else result.original_tokens / result.compressed_tokens
    )
    downstream_input = estimate_downstream_tokens(request.input, request.model)
    downstream_output = estimate_downstream_tokens(
        result.compressed_text,
        request.model,
    )

    return V1CompressResponse(
        output=result.compressed_text,
        output_tokens=result.compressed_tokens,
        input_tokens=result.original_tokens,
        original_input_tokens=result.original_tokens,
        tokens_saved=tokens_saved,
        compression_ratio=compression_ratio,
        token_estimator=result.token_estimator,
        downstream_estimated_input_tokens=downstream_input.count,
        downstream_estimated_output_tokens=downstream_output.count,
        downstream_token_estimator=merge_token_estimator_names(
            [downstream_input.estimator, downstream_output.estimator]
        ),
        compression_time=result.elapsed_ms,
        tenant_id=result.tenant_id,
        compression_profile=result.compression_profile,
        compression_profile_source=result.compression_profile_source,
        training_sample_recorded=result.training_sample_recorded,
        warnings=result.warnings,
    )


@app.post("/v1/messages/compress", response_model=V1MessagesCompressResponse)
def compress_v1_messages(
    request: V1MessagesCompressRequest,
    x_tenant_id: Annotated[str | None, Header(alias="X-Tenant-ID")] = None,
) -> V1MessagesCompressResponse:
    tenant_profile = _tenant_profile_from_request(
        body_tenant_id=request.tenant_id,
        header_tenant_id=x_tenant_id,
        settings=request.tenant_profile,
    )
    aggressiveness = _resolve_v1_aggressiveness(
        request.compression_settings,
        tenant_profile,
    )
    role_aggressiveness = _resolve_v1_role_aggressiveness(
        request.compression_settings,
    )
    mode = _resolve_v1_mode(request.compression_settings)
    latency_budget_ms = _resolve_v1_latency_budget_ms(request.compression_settings)

    messages = [
        message.model_dump(exclude_unset=True)
        for message in request.messages
    ]
    try:
        result = compress_user_messages(
            messages,
            compression_service=compression_service,
            aggressiveness=aggressiveness,
            role_aggressiveness=role_aggressiveness,
            tenant_profile=tenant_profile,
            mode=mode,
            latency_budget_ms=latency_budget_ms,
            compact_empty_user_messages=_resolve_v1_compact_empty_user_messages(
                request.compression_settings,
            ),
            compact_duplicate_user_text_parts=(
                _resolve_v1_compact_duplicate_user_text_parts(
                    request.compression_settings,
                )
            ),
        )
    except CompressionRuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    preserved_top_level = _top_level_preserved_token_details(
        request,
        tenant_profile,
    )
    input_tokens = result.input_tokens + preserved_top_level.count
    output_tokens = result.output_tokens + preserved_top_level.count
    tokens_saved = max(0, input_tokens - output_tokens)
    compression_ratio = 0.0 if output_tokens == 0 else input_tokens / output_tokens
    compressed_request = request.model_dump(
        exclude={"compression_settings", "tenant_id", "tenant_profile"},
        exclude_unset=True,
    )
    compressed_request["messages"] = result.messages
    downstream_input = _estimate_v1_messages_downstream_tokens(
        request,
        request_messages=messages,
    )
    downstream_output = _estimate_v1_messages_downstream_tokens(
        request,
        request_messages=result.messages,
    )

    return V1MessagesCompressResponse(
        compressed_request=compressed_request,
        messages=result.messages,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        original_input_tokens=input_tokens,
        tokens_saved=tokens_saved,
        compression_ratio=compression_ratio,
        compression_time=result.elapsed_ms,
        user_input_tokens=result.user_input_tokens,
        user_output_tokens=result.user_output_tokens,
        user_tokens_saved=max(0, result.user_input_tokens - result.user_output_tokens),
        non_user_tokens_preserved=(
            result.non_user_tokens_preserved + preserved_top_level.count
        ),
        token_estimator=merge_token_estimator_names(
            [result.token_estimator, preserved_top_level.estimator]
        ),
        downstream_estimated_input_tokens=downstream_input.count,
        downstream_estimated_output_tokens=downstream_output.count,
        downstream_token_estimator=merge_token_estimator_names(
            [downstream_input.estimator, downstream_output.estimator]
        ),
        tenant_id=tenant_profile.tenant_id,
        compression_profile=tenant_profile.profile_id,
        compression_profile_source=tenant_profile.source,
        training_sample_recorded=False,
        message_stats=[asdict(stat) for stat in result.stats],
        warnings=result.warnings,
    )


def _tenant_profile_from_request(
    *,
    body_tenant_id: str | None,
    header_tenant_id: str | None,
    settings: TenantCompressionSettings | None,
) -> TenantCompressionProfile:
    tenant_id = (
        body_tenant_id
        if body_tenant_id is not None and body_tenant_id.strip()
        else header_tenant_id
    )
    return build_tenant_profile(
        tenant_id=tenant_id,
        profile_id=None if settings is None else settings.profile_id,
        default_aggressiveness=(
            None if settings is None else settings.default_aggressiveness
        ),
        min_rate=None if settings is None else settings.min_rate,
        force_keep_tokens=() if settings is None else settings.force_keep_tokens,
        force_drop_phrases=() if settings is None else settings.force_drop_phrases,
    )


def _resolve_compress_aggressiveness(
    request: CompressRequest,
    tenant_profile: TenantCompressionProfile,
) -> float:
    if "aggressiveness" in request.model_fields_set:
        return request.aggressiveness
    if tenant_profile.default_aggressiveness is not None:
        return tenant_profile.default_aggressiveness
    return request.aggressiveness


def _resolve_compress_mode(request: CompressRequest) -> str:
    if request.mode is not None:
        return request.mode
    if getattr(compression_service, "model_auto_enabled", False):
        return COMPRESSION_MODE_MODEL_AUTO
    return COMPRESSION_MODE_MODEL_FORCE


def _resolve_v1_aggressiveness(
    settings: V1CompressionSettings | None,
    tenant_profile: TenantCompressionProfile,
) -> float:
    if (
        settings is not None
        and settings.aggressiveness is not None
        and not isinstance(settings.aggressiveness, dict)
    ):
        return settings.aggressiveness
    if (
        settings is not None
        and isinstance(settings.aggressiveness, dict)
        and "user" in settings.aggressiveness
    ):
        return settings.aggressiveness["user"]
    if tenant_profile.default_aggressiveness is not None:
        return tenant_profile.default_aggressiveness
    return DEFAULT_AGGRESSIVENESS


def _resolve_v1_role_aggressiveness(
    settings: V1CompressionSettings | None,
) -> dict[str, float] | None:
    if settings is None or settings.aggressiveness is None:
        return None
    if not isinstance(settings.aggressiveness, dict):
        return None

    if not settings.aggressiveness:
        return None

    return {
        role.strip().lower(): aggressiveness
        for role, aggressiveness in settings.aggressiveness.items()
        if role.strip()
    }


def _resolve_v1_mode(settings: V1CompressionSettings | None) -> str:
    if settings is not None and settings.mode is not None:
        return settings.mode
    return COMPRESSION_MODE_DETERMINISTIC


def _resolve_v1_latency_budget_ms(
    settings: V1CompressionSettings | None,
) -> float | None:
    if settings is None:
        return None
    return settings.latency_budget_ms


def _resolve_v1_compact_empty_user_messages(
    settings: V1CompressionSettings | None,
) -> bool:
    return False if settings is None else settings.compact_empty_user_messages


def _resolve_v1_compact_duplicate_user_text_parts(
    settings: V1CompressionSettings | None,
) -> bool:
    return False if settings is None else settings.compact_duplicate_user_text_parts


def _top_level_preserved_token_details(
    request: V1MessagesCompressRequest,
    tenant_profile: TenantCompressionProfile,
) -> TokenEstimate:
    return _estimate_top_level_preserved_tokens(
        request,
        estimate_text_tokens=lambda text: _estimate_compression_tokens_for_profile(
            text,
            tenant_profile,
        ),
    )


def _estimate_compression_tokens_for_profile(
    text: str,
    tenant_profile: TenantCompressionProfile,
) -> TokenEstimate:
    estimate_compression_tokens = getattr(
        compression_service,
        "estimate_compression_tokens",
        None,
    )
    if callable(estimate_compression_tokens):
        return estimate_compression_tokens(text, tenant_profile)

    return estimate_regex_tokens(text)


def _estimate_v1_messages_downstream_tokens(
    request: V1MessagesCompressRequest,
    request_messages: list[dict[str, Any]],
) -> TokenEstimate:
    def estimate_text_tokens(text: str) -> TokenEstimate:
        return estimate_downstream_tokens(text, request.model)

    message_estimates = [
        estimate_content_token_details(
            message.get("content"),
            estimate_text_tokens=estimate_text_tokens,
        )
        for message in request_messages
    ]
    top_level_estimate = _estimate_top_level_preserved_tokens(
        request,
        estimate_text_tokens=estimate_text_tokens,
    )
    estimates = [*message_estimates, top_level_estimate]

    return TokenEstimate(
        count=sum(estimate.count for estimate in estimates),
        estimator=merge_token_estimator_names(
            [estimate.estimator for estimate in estimates]
        ),
        tokenizer_backed=any(estimate.tokenizer_backed for estimate in estimates),
    )


def _estimate_top_level_preserved_tokens(
    request: V1MessagesCompressRequest,
    estimate_text_tokens: Callable[[str], TokenEstimate],
) -> TokenEstimate:
    extras: dict[str, Any] = request.model_extra or {}
    estimates: list[TokenEstimate] = []
    for key in ("system", "instructions", "developer"):
        if key in extras:
            estimates.append(
                estimate_content_token_details(
                    extras[key],
                    estimate_text_tokens=estimate_text_tokens,
                )
            )

    if not estimates:
        return TokenEstimate(count=0, estimator=REGEX_TOKEN_ESTIMATOR)

    return TokenEstimate(
        count=sum(estimate.count for estimate in estimates),
        estimator=merge_token_estimator_names(
            [estimate.estimator for estimate in estimates]
        ),
        tokenizer_backed=any(estimate.tokenizer_backed for estimate in estimates),
    )
