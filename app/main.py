from concurrent.futures import Future, ThreadPoolExecutor
import copy
from dataclasses import asdict, dataclass, replace
from typing import Annotated, Any, Callable
import hashlib
import json
import os
import time
import uuid

from fastapi import Depends, FastAPI, Header, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse

from app.benchmark_ui import BENCHMARK_HTML
from app.compressor import (
    COMPRESSION_MODE_DETERMINISTIC,
    COMPRESSION_MODE_MODEL_AUTO,
    COMPRESSION_MODE_MODEL_FORCE,
    CompressionResult,
    CompressionRuntimeError,
    PromptCompressionService,
)
from app.content_cache import ContentCompressionCache
from app.eval_suite import evaluate_compression, load_eval_cases, quality_passed
from app.eval_ui import EVAL_HTML
from app.embed_ui import EMBED_HTML
from app.experiments_ui import EXPERIMENTS_HTML
from app.message_compression import (
    ToolResultCompressionPolicy,
    compress_user_messages,
    estimate_content_token_details,
)
from app.responses_compression import compress_responses_input
from app.gpu_policy import GPU_COMPRESSION_POLICY
from app.research_ui import RESEARCH_HTML
from app.response_cache import LocalResponseCache
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
    V1ResponsesCompressRequest,
    V1ResponsesCompressResponse,
)
from app.tenant_profiles import TenantCompressionProfile, build_tenant_profile
from app.telemetry import CompressionTelemetry
from app.token_estimator import (
    REGEX_TOKEN_ESTIMATOR,
    TokenEstimate,
    estimate_downstream_tokens,
    estimate_regex_tokens,
    merge_token_estimator_names,
)
from app.version import DEPLOYMENT_TIMESTAMP, DEPLOYMENT_VERSION
from app.usagetap_authorization import (
    UsageTapAuthorization,
    UsageTapAuthorizationClient,
    UsageTapAuthorizationError,
    UsageTapAuthorizationFailureCache,
)
from app.demo_access import (
    DemoAccessError,
    DemoAuthorization,
    DemoSessionManager,
    demo_client_identifier,
)
from app.usagetap_metering import (
    UsageTapMeteringClient,
    UsageTapMeteringError,
)

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

    .field-help {
      color: var(--muted);
      font-size: 11px;
      font-weight: 500;
    }

    .auth-row {
      display: flex;
      align-items: center;
      gap: 8px;
      flex-wrap: wrap;
    }

    .auth-row input {
      flex: 1 1 280px;
    }

    .auth-row button {
      flex: 0 0 auto;
      min-height: 34px;
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
          <a class="nav-link" href="/experiments">Experiments</a>
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
          <div class="tenant-field full">
            <label for="compressionApiKey">Compression API Key</label>
            <div class="auth-row">
              <input
                id="compressionApiKey"
                type="password"
                autocomplete="new-password"
                autocapitalize="none"
                spellcheck="false"
                data-1p-ignore="true"
                data-lpignore="true"
                placeholder="utk-... or cmp-..."
              >
              <button class="example-button" id="startDemoButton" type="button">Start 10-minute demo</button>
            </div>
            <span class="field-help" id="demoAccessStatus">Enter an API key with Use Compression permission or start a bounded demo session. Credentials stay in page memory only.</span>
          </div>
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
          <label class="tenant-inline">
            <input id="includeDetailedAnalytics" type="checkbox" checked>
            Detailed analytics
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
            <li><code>&lt;compress-json paths=&quot;$.description,$.comments[*].body&quot;&gt;...&lt;/compress-json&gt;</code> lets this profiler compress only the selected JSON string values.</li>
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
    const compressionApiKeyInput = document.getElementById("compressionApiKey");
    const startDemoButton = document.getElementById("startDemoButton");
    const demoAccessStatus = document.getElementById("demoAccessStatus");
    const compressionModeInput = document.getElementById("compressionMode");
    const latencyBudgetMsInput = document.getElementById("latencyBudgetMs");
    const allowCpuModelAutoInput = document.getElementById("allowCpuModelAuto");
    const includeDetailedAnalyticsInput = document.getElementById("includeDetailedAnalytics");
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
    const COMPRESSION_CREDENTIAL_PATTERN = /^(?:cmp-[A-Za-z0-9_-]{43}|utk-[A-Za-z0-9_-]{43}|demo-v1\\.[A-Za-z0-9_-]+\\.[A-Za-z0-9_-]+)$/;
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

    startDemoButton.addEventListener("click", async () => {
      startDemoButton.disabled = true;
      demoAccessStatus.textContent = "Starting a bounded demo session...";
      try {
        const response = await fetch("/demo/session", {
          method: "POST",
          headers: { "Accept": "application/json" },
          cache: "no-store",
        });
        const data = await response.json();
        if (!response.ok || typeof data.token !== "string") {
          throw new Error(data.detail || "Demo access is unavailable");
        }
        compressionApiKeyInput.value = data.token;
        const expiresAt = new Date(Number(data.expiresAt) * 1000);
        demoAccessStatus.textContent =
          `Demo active until ${expiresAt.toLocaleTimeString()} - ` +
          `${data.maxOperations} operations, ${data.maxInputCharsPerOperation.toLocaleString()} chars each; ` +
          `${data.dailyOperationsRemaining} operations remain today for this network.`;
        setStatus("Demo session ready");
      } catch (error) {
        demoAccessStatus.textContent = error.message;
        setStatus(error.message, true);
      } finally {
        startDemoButton.disabled = false;
      }
    });

    compressButton.addEventListener("click", async () => {
      const text = promptInput.value.trim();
      if (!text) {
        setStatus("Paste a prompt first", true);
        return;
      }
      const compressionApiKey = compressionApiKeyInput.value.trim();
      if (!COMPRESSION_CREDENTIAL_PATTERN.test(compressionApiKey)) {
        setStatus("Enter a key with Use Compression permission or start a demo session", true);
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
        requestPayload.include_detailed_analytics = includeDetailedAnalyticsInput.checked;
        requestPayload.allow_inline_json_compression_paths = true;
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
          headers: {
            "Content-Type": "application/json",
            "Authorization": `Bearer ${compressionApiKey}`,
          },
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
usage_tap_authorization_client = UsageTapAuthorizationClient.from_environment()
usage_tap_authorization_failure_cache = (
    UsageTapAuthorizationFailureCache.from_environment()
)
usage_tap_authorization_executor = ThreadPoolExecutor(
    max_workers=8,
    thread_name_prefix="usagetap-authorization",
)
usage_tap_metering_client = UsageTapMeteringClient.from_environment()
demo_session_manager = DemoSessionManager.from_environment()
compression_response_cache = LocalResponseCache.from_environment()
message_content_cache = ContentCompressionCache.from_environment()
compression_telemetry = CompressionTelemetry()
eval_cases = load_eval_cases()

_RESPONSE_CACHE_SCHEMA_VERSION = "compression-response-v2"
_TRANSIENT_CACHE_WARNING_FRAGMENTS = (
    "cold_model",
    "fallback",
    "missing_latency_baseline",
    "origin_unavailable",
    "output_rejected",
    "timeout",
)


class _UnserializableCacheResponse(Exception):
    def __init__(self, response: Any) -> None:
        super().__init__("Compression response does not support cache serialization.")
        self.response = response


@dataclass(frozen=True, slots=True)
class PendingUsageTapAuthorization:
    future: Future[UsageTapAuthorization] | None = None
    demo_authorization: DemoAuthorization | None = None


def require_usage_tap_compression_authorization(
    request: Request,
    authorization: Annotated[
        str | None,
        Header(alias="Authorization"),
    ] = None,
) -> UsageTapAuthorization:
    """Authorize one operation and retain only its verified UsageTap identity."""
    try:
        verified = usage_tap_authorization_client.authorize(authorization)
    except UsageTapAuthorizationError as exc:
        raise HTTPException(
            status_code=exc.status_code,
            detail=exc.public_message,
        ) from None

    request.state.usagetap_authorization = verified
    request.state.usagetap_customer_id = verified.customer_id
    return verified


def start_usage_tap_compression_authorization(
    request: Request,
    authorization: Annotated[
        str | None,
        Header(alias="Authorization"),
    ] = None,
) -> PendingUsageTapAuthorization:
    """Reject obvious abuse, then start the remote check without blocking inference."""
    if _looks_like_demo_authorization(authorization):
        try:
            demo_authorization = demo_session_manager.validate_authorization_header(
                authorization
            )
        except DemoAccessError as exc:
            raise HTTPException(
                status_code=exc.status_code,
                detail=exc.public_message,
                headers=_demo_error_headers(exc),
            ) from None
        return PendingUsageTapAuthorization(
            demo_authorization=demo_authorization,
        )

    try:
        validated_header = usage_tap_authorization_client.validate_incoming_credential(
            authorization
        )
    except UsageTapAuthorizationError as exc:
        raise HTTPException(
            status_code=exc.status_code,
            detail=exc.public_message,
        ) from None

    cached_failure = usage_tap_authorization_failure_cache.get(validated_header)
    if cached_failure is not None:
        raise HTTPException(
            status_code=cached_failure.status_code,
            detail=cached_failure.public_message,
        )

    request.state.compression_operation_id = uuid.uuid4().hex
    future = usage_tap_authorization_executor.submit(
        _authorize_and_cache_failure,
        validated_header,
    )
    return PendingUsageTapAuthorization(future=future)


def _looks_like_demo_authorization(authorization_header: str | None) -> bool:
    return bool(
        isinstance(authorization_header, str)
        and authorization_header.casefold().startswith("bearer demo-v1.")
    )


def reserve_demo_compression_operation(
    request: Request,
    pending: PendingUsageTapAuthorization,
    *,
    input_chars: int,
) -> None:
    demo_authorization = pending.demo_authorization
    if demo_authorization is None:
        return
    try:
        demo_session_manager.reserve_operation(
            demo_authorization,
            input_chars=input_chars,
        )
    except DemoAccessError as exc:
        raise HTTPException(
            status_code=exc.status_code,
            detail=exc.public_message,
            headers=_demo_error_headers(exc),
        ) from None
    request.state.demo_authorization = demo_authorization


def _authorize_and_cache_failure(
    authorization_header: str,
) -> UsageTapAuthorization:
    try:
        return usage_tap_authorization_client.authorize(authorization_header)
    except UsageTapAuthorizationError as exc:
        usage_tap_authorization_failure_cache.record(authorization_header, exc)
        raise


def _demo_error_headers(exc: DemoAccessError) -> dict[str, str] | None:
    if exc.retry_after_seconds is None:
        return None
    return {"Retry-After": str(exc.retry_after_seconds)}


def complete_usage_tap_compression_authorization(
    request: Request,
    pending: PendingUsageTapAuthorization,
) -> UsageTapAuthorization | DemoAuthorization:
    if pending.demo_authorization is not None:
        request.state.demo_authorization = pending.demo_authorization
        return pending.demo_authorization
    if pending.future is None:
        raise HTTPException(
            status_code=503,
            detail="Compression authorization is temporarily unavailable.",
        )
    try:
        verified = pending.future.result()
    except UsageTapAuthorizationError as exc:
        raise HTTPException(
            status_code=exc.status_code,
            detail=exc.public_message,
        ) from None

    request.state.usagetap_authorization = verified
    request.state.usagetap_customer_id = verified.customer_id
    return verified


def record_usage_tap_compression_metering(
    request: Request,
    *,
    input_tokens: int,
    output_tokens: int,
) -> None:
    """Record verified savings before releasing a compression response."""
    if isinstance(
        getattr(request.state, "demo_authorization", None),
        DemoAuthorization,
    ):
        return
    verified = getattr(request.state, "usagetap_authorization", None)
    if not isinstance(verified, UsageTapAuthorization):
        raise HTTPException(
            status_code=503,
            detail="Compression metering is temporarily unavailable.",
        )

    operation_id = getattr(request.state, "compression_operation_id", None)
    if not isinstance(operation_id, str) or not operation_id:
        raise HTTPException(
            status_code=503,
            detail="Compression metering is temporarily unavailable.",
        )
    try:
        metering = usage_tap_metering_client.record_compression_savings(
            customer_id=verified.customer_id,
            operation_id=operation_id,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        )
    except UsageTapMeteringError as exc:
        raise HTTPException(
            status_code=503,
            detail=exc.public_message,
        ) from None

    request.state.compression_operation_id = operation_id
    request.state.usagetap_metering = metering


@app.get("/", response_class=HTMLResponse)
def index() -> HTMLResponse:
    return HTMLResponse(content=APP_HTML, headers=DASHBOARD_EMBED_HEADERS)


@app.post("/demo/session", response_class=JSONResponse)
def create_demo_session(request: Request) -> JSONResponse:
    direct_host = request.client.host if request.client is not None else None
    client_identifier = demo_client_identifier(
        request.headers.get("x-forwarded-for"),
        direct_host,
        trust_forwarded_for=bool(os.getenv("K_SERVICE")),
    )
    try:
        session = demo_session_manager.issue_session(client_identifier)
    except DemoAccessError as exc:
        raise HTTPException(
            status_code=exc.status_code,
            detail=exc.public_message,
            headers=_demo_error_headers(exc),
        ) from None
    return JSONResponse(
        content={
            "token": session.token,
            "expiresAt": session.expires_at,
            "maxOperations": session.max_operations,
            "maxInputChars": session.max_input_chars,
            "maxInputCharsPerOperation": session.max_input_chars_per_operation,
            "dailySessionsRemaining": session.daily_sessions_remaining,
            "dailyOperationsRemaining": session.daily_operations_remaining,
            "dailyInputCharsRemaining": session.daily_input_chars_remaining,
        },
        headers={
            "Cache-Control": "no-store, max-age=0",
            "Pragma": "no-cache",
        },
    )


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


@app.get("/experiments", response_class=HTMLResponse)
def experiments_index() -> HTMLResponse:
    return HTMLResponse(content=EXPERIMENTS_HTML, headers=DASHBOARD_EMBED_HEADERS)


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
    runtime_info = getattr(compression_service, "runtime_info", None)
    runtime = runtime_info() if callable(runtime_info) else {}
    runtime["response_cache"] = compression_response_cache.stats()
    runtime["content_cache"] = message_content_cache.stats()
    runtime["compression_policy"] = GPU_COMPRESSION_POLICY.schema_version
    runtime["telemetry"] = compression_telemetry.snapshot()
    return HealthResponse(
        status="ok",
        deployment_version=DEPLOYMENT_VERSION,
        deployment_timestamp=DEPLOYMENT_TIMESTAMP,
        model=compression_service.model_name,
        model_loaded=compression_service.is_loaded,
        runtime=runtime,
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


def _run_compress_request(
    request: CompressRequest,
    x_tenant_id: str | None = None,
    x_request_id: str | None = None,
    *,
    model_auto_plan_only: bool = False,
) -> CompressionResult:
    tenant_profile = _tenant_profile_from_request(
        body_tenant_id=request.tenant_id,
        header_tenant_id=x_tenant_id,
        settings=request.tenant_profile,
    )
    aggressiveness = _resolve_compress_aggressiveness(request, tenant_profile)
    mode = _resolve_compress_mode(request)
    try:
        compression_kwargs = dict(
            text=request.text,
            aggressiveness=aggressiveness,
            include_sections=request.include_sections,
            tenant_profile=tenant_profile,
            mode=mode,
            latency_budget_ms=request.latency_budget_ms,
            allow_cpu_model_auto=request.allow_cpu_model_auto,
            min_model_candidate_tokens=request.min_model_candidate_tokens,
            model_chunk_chars=request.model_chunk_chars,
            collect_diagnostics=request.include_diagnostics,
            collect_detailed_analytics=request.include_detailed_analytics,
            input_format=request.input_format,
            html_mode=request.html_mode,
        )
        if request.allow_inline_json_compression_paths:
            compression_kwargs["allow_inline_json_compression_paths"] = True
        if not request.apply_deterministic_transforms:
            compression_kwargs["apply_deterministic_transforms"] = False
        if request.evaluate_disabled_transforms:
            compression_kwargs["evaluate_disabled_transforms"] = True
        if request.evaluation_constraints is not None:
            compression_kwargs["evaluation_constraints"] = (
                request.evaluation_constraints.model_dump()
            )
        if request.experiment_profile is not None:
            compression_kwargs["experiment_profile"] = request.experiment_profile
        if x_request_id:
            compression_kwargs["request_id"] = x_request_id
        if model_auto_plan_only:
            compression_kwargs["model_auto_plan_only"] = True
        return compression_service.compress(**compression_kwargs)
    except CompressionRuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


def compress(
    request: CompressRequest,
    x_tenant_id: Annotated[str | None, Header(alias="X-Tenant-ID")] = None,
    x_request_id: Annotated[str | None, Header(alias="X-Request-ID")] = None,
) -> CompressResponse:
    result = _run_compress_request(
        request,
        x_tenant_id=x_tenant_id,
        x_request_id=x_request_id,
    )

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
        experiment_profile=result.experiment_profile,
    )


def plan_compress_model_auto(
    request: CompressRequest,
    *,
    x_tenant_id: str | None = None,
    x_request_id: str | None = None,
) -> tuple[CompressResponse, bool]:
    """Run deterministic compression and the GPU gate without loading a model."""
    result = _run_compress_request(
        request,
        x_tenant_id=x_tenant_id,
        x_request_id=x_request_id,
        model_auto_plan_only=True,
    )
    if result.token_savings is None:
        raise HTTPException(
            status_code=500,
            detail="Compression result did not include token-savings attribution.",
        )
    response = CompressResponse(
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
        output_sections=[asdict(section) for section in result.output_sections],
        diagnostics=(
            asdict(result.diagnostics)
            if request.include_diagnostics and result.diagnostics is not None
            else None
        ),
        experiment_profile=result.experiment_profile,
    )
    return response, result.model_required


def _response_cache_key(
    *,
    route: str,
    request_payload: dict[str, Any],
    tenant_profile: TenantCompressionProfile,
    effective_settings: dict[str, Any],
) -> str:
    """Hash the complete, resolved compression behavior without credentials."""
    identity = {
        "schema": _RESPONSE_CACHE_SCHEMA_VERSION,
        "compression_policy": GPU_COMPRESSION_POLICY.schema_version,
        "route": route,
        "request": request_payload,
        "tenant_profile": asdict(tenant_profile),
        "effective_settings": effective_settings,
        "runtime": {
            "deployment_version": DEPLOYMENT_VERSION,
            "model": compression_service.model_name,
            "source_sha256": os.getenv("COMPRESSOR_SOURCE_SHA256", "unknown"),
        },
    }
    canonical = json.dumps(
        identity,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _request_allows_response_cache(request: CompressRequest) -> bool:
    return not (
        request.include_diagnostics
        or request.evaluate_disabled_transforms
        or request.evaluation_constraints is not None
        or request.experiment_profile is not None
    )


def _warnings_are_cache_stable(warnings: list[str]) -> bool:
    return not any(
        fragment in warning.casefold()
        for warning in warnings
        for fragment in _TRANSIENT_CACHE_WARNING_FRAGMENTS
    )


def _compress_response_is_cacheable(response: CompressResponse) -> bool:
    return (
        not response.training_sample_recorded
        and response.compressed_tokens < response.original_tokens
        and not response.token_savings.fallback_used
        and _warnings_are_cache_stable(response.warnings)
    )


def _v1_response_is_cacheable(
    response: (
        V1CompressResponse
        | V1MessagesCompressResponse
        | V1ResponsesCompressResponse
    ),
) -> bool:
    return (
        not response.training_sample_recorded
        and response.tokens_saved > 0
        and _warnings_are_cache_stable(response.warnings)
    )


def _run_cached_response(
    *,
    key: str,
    response_type: type,
    compute: Callable[[], Any],
    cacheable: Callable[[Any], bool],
    request_cacheable: bool = True,
) -> tuple[Any, str, bytes | None, bool]:
    if not request_cacheable:
        return compute(), "bypass", None, False

    started = time.perf_counter()

    def serialized_compute() -> tuple[bytes, bool]:
        computed = compute()
        if not callable(getattr(computed, "model_dump_json", None)):
            raise _UnserializableCacheResponse(computed)
        payload = computed.model_dump_json(exclude_none=True).encode("utf-8")
        return payload, cacheable(computed)

    try:
        lookup = compression_response_cache.get_or_compute(
            key,
            serialized_compute,
            store_result=False,
        )
    except _UnserializableCacheResponse as exc:
        return exc.response, "bypass", None, False
    parsed = response_type.model_validate_json(lookup.payload)
    if lookup.status in {"hit", "shared"}:
        lookup_elapsed_ms = (time.perf_counter() - started) * 1000.0
        if isinstance(parsed, CompressResponse):
            parsed.elapsed_ms = lookup_elapsed_ms
        else:
            parsed.compression_time = lookup_elapsed_ms
    return parsed, lookup.status, lookup.payload, lookup.cacheable


def _commit_response_cache(
    *,
    key: str,
    payload: bytes | None,
    status: str,
    cacheable: bool,
) -> str:
    """Store only after authorization and metering have succeeded."""
    if status not in {"miss", "shared"} or not cacheable or payload is None:
        return status
    stored = compression_response_cache.put(key, payload)
    if status == "shared":
        return "shared"
    return "store" if stored else "bypass"


@app.post(
    "/compress",
    response_model=CompressResponse,
    response_model_exclude_none=True,
)
def compress_http(
    http_request: Request,
    http_response: Response,
    request: CompressRequest,
    pending_authorization: Annotated[
        PendingUsageTapAuthorization,
        Depends(start_usage_tap_compression_authorization),
    ],
    x_tenant_id: Annotated[str | None, Header(alias="X-Tenant-ID")] = None,
    x_request_id: Annotated[str | None, Header(alias="X-Request-ID")] = None,
    cache_control: Annotated[str | None, Header(alias="Cache-Control")] = None,
) -> CompressResponse:
    reserve_demo_compression_operation(
        http_request,
        pending_authorization,
        input_chars=len(request.text),
    )
    tenant_profile = _tenant_profile_from_request(
        body_tenant_id=request.tenant_id,
        header_tenant_id=x_tenant_id,
        settings=request.tenant_profile,
    )
    cache_key = _response_cache_key(
        route="/compress",
        request_payload=request.model_dump(mode="json"),
        tenant_profile=tenant_profile,
        effective_settings={
            "aggressiveness": _resolve_compress_aggressiveness(
                request,
                tenant_profile,
            ),
            "mode": _resolve_compress_mode(request),
        },
    )
    request_cacheable = (
        request.cache
        and not _cache_control_disables_storage(cache_control)
        and _request_allows_response_cache(request)
    )
    response, cache_status, cache_payload, cacheable = _run_cached_response(
        key=cache_key,
        response_type=CompressResponse,
        compute=lambda: compress(
            request,
            x_tenant_id=x_tenant_id,
            x_request_id=x_request_id,
        ),
        cacheable=_compress_response_is_cacheable,
        request_cacheable=request_cacheable,
    )
    complete_usage_tap_compression_authorization(
        http_request,
        pending_authorization,
    )
    record_usage_tap_compression_metering(
        http_request,
        input_tokens=response.original_tokens,
        output_tokens=response.compressed_tokens,
    )
    cache_status = _commit_response_cache(
        key=cache_key,
        payload=cache_payload,
        status=cache_status,
        cacheable=cacheable,
    )
    http_response.headers["X-Compression-Cache"] = cache_status
    compression_telemetry.record(
        route="/compress",
        mode=response.compression_mode,
        cache_status=cache_status,
        input_tokens=response.original_tokens,
        output_tokens=response.compressed_tokens,
        elapsed_ms=response.elapsed_ms,
        warnings=response.warnings,
    )
    return response


def _compress_v1_response(
    request: V1CompressRequest,
    x_tenant_id: str | None = None,
    *,
    model_auto_plan_only: bool = False,
) -> tuple[V1CompressResponse, bool]:
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
        compression_kwargs = dict(
            text=request.input,
            aggressiveness=aggressiveness,
            include_sections=False,
            tenant_profile=tenant_profile,
            mode=mode,
            latency_budget_ms=latency_budget_ms,
            collect_diagnostics=False,
            input_format=(
                request.compression_settings.input_format
                if request.compression_settings is not None
                else "auto"
            ),
            html_mode=(
                request.compression_settings.html_mode
                if request.compression_settings is not None
                else "visible_text"
            ),
        )
        if model_auto_plan_only:
            compression_kwargs["model_auto_plan_only"] = True
        result = compression_service.compress(**compression_kwargs)
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

    response = V1CompressResponse(
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
    return response, result.model_required


def compress_v1(
    request: V1CompressRequest,
    x_tenant_id: Annotated[str | None, Header(alias="X-Tenant-ID")] = None,
) -> V1CompressResponse:
    response, _model_required = _compress_v1_response(
        request,
        x_tenant_id=x_tenant_id,
    )
    return response


def plan_v1_compress_model_auto(
    request: V1CompressRequest,
    *,
    x_tenant_id: str | None = None,
) -> tuple[V1CompressResponse, bool]:
    return _compress_v1_response(
        request,
        x_tenant_id=x_tenant_id,
        model_auto_plan_only=True,
    )


@app.post(
    "/v1/compress",
    response_model=V1CompressResponse,
)
def compress_v1_http(
    http_request: Request,
    http_response: Response,
    request: V1CompressRequest,
    pending_authorization: Annotated[
        PendingUsageTapAuthorization,
        Depends(start_usage_tap_compression_authorization),
    ],
    x_tenant_id: Annotated[str | None, Header(alias="X-Tenant-ID")] = None,
    cache_control: Annotated[str | None, Header(alias="Cache-Control")] = None,
) -> V1CompressResponse:
    reserve_demo_compression_operation(
        http_request,
        pending_authorization,
        input_chars=len(request.input),
    )
    tenant_profile = _tenant_profile_from_request(
        body_tenant_id=request.tenant_id,
        header_tenant_id=x_tenant_id,
        settings=request.tenant_profile,
    )
    cache_key = _response_cache_key(
        route="/v1/compress",
        request_payload=request.model_dump(mode="json"),
        tenant_profile=tenant_profile,
        effective_settings={
            "aggressiveness": _resolve_v1_aggressiveness(
                request.compression_settings,
                tenant_profile,
            ),
            "mode": _resolve_v1_mode(request.compression_settings),
            "latency_budget_ms": _resolve_v1_latency_budget_ms(
                request.compression_settings
            ),
        },
    )
    response, cache_status, cache_payload, cacheable = _run_cached_response(
        key=cache_key,
        response_type=V1CompressResponse,
        compute=lambda: compress_v1(request, x_tenant_id=x_tenant_id),
        cacheable=_v1_response_is_cacheable,
        request_cacheable=(
            _v1_cache_enabled(request.compression_settings)
            and not _cache_control_disables_storage(cache_control)
        ),
    )
    complete_usage_tap_compression_authorization(
        http_request,
        pending_authorization,
    )
    record_usage_tap_compression_metering(
        http_request,
        input_tokens=response.input_tokens,
        output_tokens=response.output_tokens,
    )
    cache_status = _commit_response_cache(
        key=cache_key,
        payload=cache_payload,
        status=cache_status,
        cacheable=cacheable,
    )
    http_response.headers["X-Compression-Cache"] = cache_status
    compression_telemetry.record(
        route="/v1/compress",
        mode=_resolve_v1_mode(request.compression_settings),
        cache_status=cache_status,
        input_tokens=response.input_tokens,
        output_tokens=response.output_tokens,
        elapsed_ms=response.compression_time,
        warnings=response.warnings,
    )
    return response


def _compress_v1_messages_response(
    request: V1MessagesCompressRequest,
    x_tenant_id: str | None = None,
    *,
    content_cache: ContentCompressionCache | None = None,
    content_cache_enabled: bool = True,
    model_auto_plan_only: bool = False,
) -> tuple[V1MessagesCompressResponse, bool]:
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

    messages = [copy.deepcopy(message) for message in request.messages]
    fail_open_used = False
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
            content_cache=content_cache,
            content_cache_enabled=(
                content_cache_enabled and not model_auto_plan_only
            ),
            tool_result_policy=_resolve_v1_tool_result_policy(
                request.compression_settings,
            ),
            model_auto_plan_only=model_auto_plan_only,
        )
    except (CompressionRuntimeError, TimeoutError) as exc:
        if not _v1_fail_open_enabled(request.compression_settings):
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        fail_open_used = True
        result = compress_user_messages(
            messages,
            compression_service=compression_service,
            aggressiveness=0.0,
            role_aggressiveness={
                str(message.get("role", "")).lower(): 0.0 for message in messages
            },
            tenant_profile=tenant_profile,
            mode=COMPRESSION_MODE_DETERMINISTIC,
            content_cache=None,
            content_cache_enabled=False,
        )
        result = replace(
            result,
            warnings=[*result.warnings, "compression_fail_open_original_preserved"],
        )

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

    response = V1MessagesCompressResponse(
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
        fail_open_used=fail_open_used,
    )
    return response, result.model_required


def compress_v1_messages(
    request: V1MessagesCompressRequest,
    x_tenant_id: Annotated[str | None, Header(alias="X-Tenant-ID")] = None,
    *,
    content_cache: ContentCompressionCache | None = None,
    content_cache_enabled: bool = True,
) -> V1MessagesCompressResponse:
    response, _model_required = _compress_v1_messages_response(
        request,
        x_tenant_id=x_tenant_id,
        content_cache=content_cache,
        content_cache_enabled=content_cache_enabled,
    )
    return response


def plan_v1_messages_model_auto(
    request: V1MessagesCompressRequest,
    *,
    x_tenant_id: str | None = None,
) -> tuple[V1MessagesCompressResponse, bool]:
    return _compress_v1_messages_response(
        request,
        x_tenant_id=x_tenant_id,
        content_cache=None,
        content_cache_enabled=False,
        model_auto_plan_only=True,
    )


@app.post(
    "/v1/messages/compress",
    response_model=V1MessagesCompressResponse,
)
def compress_v1_messages_http(
    http_request: Request,
    http_response: Response,
    request: V1MessagesCompressRequest,
    pending_authorization: Annotated[
        PendingUsageTapAuthorization,
        Depends(start_usage_tap_compression_authorization),
    ],
    x_tenant_id: Annotated[str | None, Header(alias="X-Tenant-ID")] = None,
    cache_control: Annotated[str | None, Header(alias="Cache-Control")] = None,
) -> V1MessagesCompressResponse:
    reserve_demo_compression_operation(
        http_request,
        pending_authorization,
        input_chars=_messages_input_chars(request),
    )
    tenant_profile = _tenant_profile_from_request(
        body_tenant_id=request.tenant_id,
        header_tenant_id=x_tenant_id,
        settings=request.tenant_profile,
    )
    cache_key = _response_cache_key(
        route="/v1/messages/compress",
        request_payload=request.model_dump(mode="json"),
        tenant_profile=tenant_profile,
        effective_settings={
            "aggressiveness": _resolve_v1_aggressiveness(
                request.compression_settings,
                tenant_profile,
            ),
            "role_aggressiveness": _resolve_v1_role_aggressiveness(
                request.compression_settings
            ),
            "mode": _resolve_v1_mode(request.compression_settings),
            "latency_budget_ms": _resolve_v1_latency_budget_ms(
                request.compression_settings
            ),
            "compact_empty_user_messages": (
                _resolve_v1_compact_empty_user_messages(
                    request.compression_settings
                )
            ),
            "compact_duplicate_user_text_parts": (
                _resolve_v1_compact_duplicate_user_text_parts(
                    request.compression_settings
                )
            ),
        },
    )
    request_cacheable = (
        _v1_cache_enabled(request.compression_settings)
        and not _cache_control_disables_storage(cache_control)
    )
    response, cache_status, cache_payload, cacheable = _run_cached_response(
        key=cache_key,
        response_type=V1MessagesCompressResponse,
        compute=lambda: compress_v1_messages(
            request,
            x_tenant_id=x_tenant_id,
            content_cache=message_content_cache,
            content_cache_enabled=request_cacheable,
        ),
        cacheable=_v1_response_is_cacheable,
        request_cacheable=request_cacheable,
    )
    complete_usage_tap_compression_authorization(
        http_request,
        pending_authorization,
    )
    record_usage_tap_compression_metering(
        http_request,
        input_tokens=response.input_tokens,
        output_tokens=response.output_tokens,
    )
    cache_status = _commit_response_cache(
        key=cache_key,
        payload=cache_payload,
        status=cache_status,
        cacheable=cacheable,
    )
    http_response.headers["X-Compression-Cache"] = cache_status
    if response.fail_open_used:
        http_response.headers["Cache-Control"] = "no-store"
    content_hits = sum(stat.content_cache_hits for stat in response.message_stats)
    content_misses = sum(stat.content_cache_misses for stat in response.message_stats)
    content_stores = sum(stat.content_cache_stores for stat in response.message_stats)
    http_response.headers["X-Compression-Content-Cache"] = (
        f"hits={content_hits}; misses={content_misses}; stores={content_stores}"
    )
    compression_telemetry.record(
        route="/v1/messages/compress",
        mode=_resolve_v1_mode(request.compression_settings),
        cache_status=cache_status,
        input_tokens=response.input_tokens,
        output_tokens=response.output_tokens,
        elapsed_ms=response.compression_time,
        warnings=response.warnings,
        fail_open_used=response.fail_open_used,
        content_cache_hits=content_hits,
        content_cache_misses=content_misses,
        content_cache_stores=content_stores,
        tool_actions=[
            stat.tool_result_action
            for stat in response.message_stats
            if stat.tool_result_action is not None
        ],
    )
    return response


def _compress_v1_responses_response(
    request: V1ResponsesCompressRequest,
    x_tenant_id: str | None = None,
    *,
    content_cache: ContentCompressionCache | None = None,
    content_cache_enabled: bool = True,
    model_auto_plan_only: bool = False,
) -> tuple[V1ResponsesCompressResponse, bool]:
    tenant_profile = _tenant_profile_from_request(
        body_tenant_id=request.tenant_id,
        header_tenant_id=x_tenant_id,
        settings=request.tenant_profile,
    )
    aggressiveness = _resolve_v1_aggressiveness(
        request.compression_settings,
        tenant_profile,
    )
    role_aggressiveness = _responses_role_aggressiveness(
        request.compression_settings,
        aggressiveness,
    )
    mode = _resolve_v1_mode(request.compression_settings)
    latency_budget_ms = _resolve_v1_latency_budget_ms(request.compression_settings)
    fail_open_used = False

    try:
        result = compress_responses_input(
            request.input,
            compression_service=compression_service,
            aggressiveness=aggressiveness,
            role_aggressiveness=role_aggressiveness,
            tenant_profile=tenant_profile,
            mode=mode,
            latency_budget_ms=latency_budget_ms,
            content_cache=content_cache,
            content_cache_enabled=(
                content_cache_enabled and not model_auto_plan_only
            ),
            model_auto_plan_only=model_auto_plan_only,
        )
    except (CompressionRuntimeError, TimeoutError) as exc:
        if not _v1_fail_open_enabled(request.compression_settings):
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        fail_open_used = True
        result = compress_responses_input(
            request.input,
            compression_service=compression_service,
            aggressiveness=0.0,
            role_aggressiveness={
                "developer": 0.0,
                "system": 0.0,
                "user": 0.0,
            },
            tenant_profile=tenant_profile,
            mode=COMPRESSION_MODE_DETERMINISTIC,
            content_cache=None,
            content_cache_enabled=False,
        )
        result = replace(
            result,
            warnings=[*result.warnings, "compression_fail_open_original_preserved"],
        )

    compressed_request = request.model_dump(
        exclude={"compression_settings", "tenant_id", "tenant_profile"},
        exclude_unset=True,
    )
    compressed_request["input"] = result.input
    downstream_input = _estimate_v1_responses_downstream_tokens(
        request.input,
        request.model,
    )
    downstream_output = _estimate_v1_responses_downstream_tokens(
        result.input,
        request.model,
    )
    response = V1ResponsesCompressResponse(
        compressed_request=compressed_request,
        input=result.input,
        input_tokens=result.input_tokens,
        output_tokens=result.output_tokens,
        original_input_tokens=result.input_tokens,
        tokens_saved=max(0, result.input_tokens - result.output_tokens),
        compression_ratio=(
            0.0
            if result.output_tokens == 0
            else result.input_tokens / result.output_tokens
        ),
        compression_time=result.elapsed_ms,
        token_estimator=result.token_estimator,
        downstream_estimated_input_tokens=downstream_input.count,
        downstream_estimated_output_tokens=downstream_output.count,
        downstream_token_estimator=merge_token_estimator_names(
            [downstream_input.estimator, downstream_output.estimator]
        ),
        tenant_id=tenant_profile.tenant_id,
        compression_profile=tenant_profile.profile_id,
        compression_profile_source=tenant_profile.source,
        training_sample_recorded=False,
        item_stats=[asdict(stat) for stat in result.stats],
        warnings=result.warnings,
        fail_open_used=fail_open_used,
    )
    return response, result.model_required


def compress_v1_responses(
    request: V1ResponsesCompressRequest,
    x_tenant_id: Annotated[str | None, Header(alias="X-Tenant-ID")] = None,
    *,
    content_cache: ContentCompressionCache | None = None,
    content_cache_enabled: bool = True,
) -> V1ResponsesCompressResponse:
    response, _model_required = _compress_v1_responses_response(
        request,
        x_tenant_id=x_tenant_id,
        content_cache=content_cache,
        content_cache_enabled=content_cache_enabled,
    )
    return response


def plan_v1_responses_model_auto(
    request: V1ResponsesCompressRequest,
    *,
    x_tenant_id: str | None = None,
) -> tuple[V1ResponsesCompressResponse, bool]:
    return _compress_v1_responses_response(
        request,
        x_tenant_id=x_tenant_id,
        content_cache=None,
        content_cache_enabled=False,
        model_auto_plan_only=True,
    )


@app.post(
    "/v1/responses/compress",
    response_model=V1ResponsesCompressResponse,
)
def compress_v1_responses_http(
    http_request: Request,
    http_response: Response,
    request: V1ResponsesCompressRequest,
    pending_authorization: Annotated[
        PendingUsageTapAuthorization,
        Depends(start_usage_tap_compression_authorization),
    ],
    x_tenant_id: Annotated[str | None, Header(alias="X-Tenant-ID")] = None,
    cache_control: Annotated[str | None, Header(alias="Cache-Control")] = None,
) -> V1ResponsesCompressResponse:
    reserve_demo_compression_operation(
        http_request,
        pending_authorization,
        input_chars=_responses_input_chars(request),
    )
    tenant_profile = _tenant_profile_from_request(
        body_tenant_id=request.tenant_id,
        header_tenant_id=x_tenant_id,
        settings=request.tenant_profile,
    )
    cache_key = _response_cache_key(
        route="/v1/responses/compress",
        request_payload=request.model_dump(mode="json"),
        tenant_profile=tenant_profile,
        effective_settings={
            "aggressiveness": _resolve_v1_aggressiveness(
                request.compression_settings,
                tenant_profile,
            ),
            "role_aggressiveness": _responses_role_aggressiveness(
                request.compression_settings,
                _resolve_v1_aggressiveness(
                    request.compression_settings,
                    tenant_profile,
                ),
            ),
            "mode": _resolve_v1_mode(request.compression_settings),
            "latency_budget_ms": _resolve_v1_latency_budget_ms(
                request.compression_settings
            ),
        },
    )
    request_cacheable = (
        _v1_cache_enabled(request.compression_settings)
        and not _cache_control_disables_storage(cache_control)
    )
    response, cache_status, cache_payload, cacheable = _run_cached_response(
        key=cache_key,
        response_type=V1ResponsesCompressResponse,
        compute=lambda: compress_v1_responses(
            request,
            x_tenant_id=x_tenant_id,
            content_cache=message_content_cache,
            content_cache_enabled=request_cacheable,
        ),
        cacheable=_v1_response_is_cacheable,
        request_cacheable=request_cacheable,
    )
    complete_usage_tap_compression_authorization(
        http_request,
        pending_authorization,
    )
    record_usage_tap_compression_metering(
        http_request,
        input_tokens=response.input_tokens,
        output_tokens=response.output_tokens,
    )
    cache_status = _commit_response_cache(
        key=cache_key,
        payload=cache_payload,
        status=cache_status,
        cacheable=cacheable,
    )
    http_response.headers["X-Compression-Cache"] = cache_status
    if response.fail_open_used:
        http_response.headers["Cache-Control"] = "no-store"
    content_hits = sum(stat.content_cache_hits for stat in response.item_stats)
    content_misses = sum(stat.content_cache_misses for stat in response.item_stats)
    content_stores = sum(stat.content_cache_stores for stat in response.item_stats)
    http_response.headers["X-Compression-Content-Cache"] = (
        f"hits={content_hits}; misses={content_misses}; stores={content_stores}"
    )
    compression_telemetry.record(
        route="/v1/responses/compress",
        mode=_resolve_v1_mode(request.compression_settings),
        cache_status=cache_status,
        input_tokens=response.input_tokens,
        output_tokens=response.output_tokens,
        elapsed_ms=response.compression_time,
        warnings=response.warnings,
        fail_open_used=response.fail_open_used,
        content_cache_hits=content_hits,
        content_cache_misses=content_misses,
        content_cache_stores=content_stores,
    )
    return response


def _messages_input_chars(request: V1MessagesCompressRequest) -> int:
    return sum(
        len(
            json.dumps(
                message,
                ensure_ascii=False,
                separators=(",", ":"),
                default=str,
            )
        )
        for message in request.messages
    )


def _responses_input_chars(request: V1ResponsesCompressRequest) -> int:
    if isinstance(request.input, str):
        return len(request.input)
    return len(
        json.dumps(
            request.input,
            ensure_ascii=False,
            separators=(",", ":"),
            default=str,
        )
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
        json_compression_policy_id=(
            None if settings is None else settings.json_compression_policy_id
        ),
        json_value_compression_paths=(
            () if settings is None else settings.json_value_compression_paths
        ),
        json_value_min_tokens=(
            200 if settings is None else settings.json_value_min_tokens
        ),
        json_value_max_reduction=(
            0.25 if settings is None else settings.json_value_max_reduction
        ),
        json_value_max_values=(
            8 if settings is None else settings.json_value_max_values
        ),
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


def _responses_role_aggressiveness(
    settings: V1CompressionSettings | None,
    default_aggressiveness: float,
) -> dict[str, float]:
    resolved = {
        "developer": default_aggressiveness,
        "system": default_aggressiveness,
        "user": default_aggressiveness,
    }
    explicit = _resolve_v1_role_aggressiveness(settings)
    if explicit is not None:
        for role in resolved:
            if role in explicit:
                resolved[role] = explicit[role]
    return resolved


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


def _resolve_v1_tool_result_policy(
    settings: V1CompressionSettings | None,
) -> ToolResultCompressionPolicy | None:
    if settings is None or settings.tool_result_policy is None:
        return None
    policy = settings.tool_result_policy
    return ToolResultCompressionPolicy(
        mode=policy.mode,
        aggressiveness=policy.aggressiveness,
        min_tokens=policy.min_tokens,
        max_reduction=policy.max_reduction,
        rollout_mode=policy.rollout_mode,
        rollout_percentage=policy.rollout_percentage,
        rollout_key=policy.rollout_key,
    )


def _v1_cache_enabled(settings: V1CompressionSettings | None) -> bool:
    return settings is None or settings.cache


def _v1_fail_open_enabled(settings: V1CompressionSettings | None) -> bool:
    return settings is None or settings.fail_open


def _cache_control_disables_storage(cache_control: str | None) -> bool:
    if cache_control is None:
        return False
    return any(
        directive.strip().casefold() == "no-store"
        for directive in cache_control.split(",")
    )


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

    message_estimates = []
    for message in request_messages:
        item_type = message.get("type")
        if isinstance(item_type, str) and item_type != "message":
            message_estimates.append(
                estimate_text_tokens(
                    json.dumps(
                        message,
                        ensure_ascii=False,
                        separators=(",", ":"),
                        default=str,
                    )
                )
            )
        else:
            message_estimates.append(
                estimate_content_token_details(
                    message.get("content"),
                    estimate_text_tokens=estimate_text_tokens,
                )
            )
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


def _estimate_v1_responses_downstream_tokens(
    responses_input: str | list[Any],
    model: str,
) -> TokenEstimate:
    if isinstance(responses_input, str):
        return estimate_downstream_tokens(responses_input, model)
    estimates = [
        estimate_downstream_tokens(
            json.dumps(
                item,
                ensure_ascii=False,
                separators=(",", ":"),
                default=str,
            ),
            model,
        )
        for item in responses_input
    ]
    if not estimates:
        return TokenEstimate(count=0, estimator=REGEX_TOKEN_ESTIMATOR)
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
