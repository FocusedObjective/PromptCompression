from dataclasses import asdict

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse

from app.compressor import CompressionRuntimeError, PromptCompressionService
from app.schemas import (
    CompressRequest,
    CompressResponse,
    DEFAULT_AGGRESSIVENESS,
    HealthResponse,
    V1CompressRequest,
    V1CompressResponse,
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

      textarea {
        min-height: 320px;
      }

      .output,
      .diff {
        min-height: 320px;
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
        <div class="tag-reference">
          <div class="tag-reference-title">Optional preserve controls</div>
          <ul class="tag-list">
            <li><code>&lt;nocompress&gt;...&lt;/nocompress&gt;</code> skips model compression and removes the wrapper.</li>
            <li><code>```json ... ```</code> protects JSON fences exactly as code.</li>
            <li>Medium/large raw JSON converts to TOON when safe; exact JSON, schemas/templates, tool exchanges, duplicate-key JSON, and low-savings cases stay verbatim.</li>
            <li>Agent UI/output contracts, follow-on blocks, and card payload blocks are preserved verbatim.</li>
            <li>HTML/code-bearing blocks such as <code>&lt;html&gt;</code>, <code>&lt;pre&gt;</code>, <code>&lt;code&gt;</code>, <code>&lt;script&gt;</code>, <code>&lt;style&gt;</code>, <code>&lt;template&gt;</code>, and <code>&lt;svg&gt;</code> are protected; ordinary content tags like <code>&lt;div&gt;</code>, <code>&lt;p&gt;</code>, and <code>&lt;table&gt;</code> remain compressible prose.</li>
            <li>Whitespace inside protected HTML is kept exactly as provided.</li>
            <li><code>```</code> and <code>~~~</code> markdown fences are protected from compression and preserve whitespace.</li>
          </ul>
        </div>
        <div class="controls">
          <label>
            Aggressiveness
            <input id="aggressiveness" type="range" min="0" max="1" step="0.05" value="0.15">
            <strong id="aggressivenessValue">0.15</strong>
          </label>
          <button id="compressButton" type="button">Compress</button>
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
      </section>
    </div>
  </main>

  <script>
    const promptInput = document.getElementById("prompt");
    const aggressivenessInput = document.getElementById("aggressiveness");
    const aggressivenessValue = document.getElementById("aggressivenessValue");
    const compressButton = document.getElementById("compressButton");
    const copyButton = document.getElementById("copyButton");
    const inputStatus = document.getElementById("inputStatus");
    const resultStatus = document.getElementById("resultStatus");
    const diff = document.getElementById("diff");
    const reduction = document.getElementById("reduction");
    const tokens = document.getElementById("tokens");
    const elapsed = document.getElementById("elapsed");
    let latestCompressedText = "";

    function setStatus(message, isError = false) {
      resultStatus.textContent = message;
      resultStatus.className = isError ? "status error" : "status";
    }

    function estimateTokenCount(text) {
      return (text.match(/[\\p{L}\\p{N}]+|[^\\s]/gu) || []).length;
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
      if (section.kind === "html") {
        return "HTML protected";
      }
      if (section.kind === "nocompress") {
        return "No-compress protected";
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

    aggressivenessInput.addEventListener("input", () => {
      aggressivenessValue.textContent = Number(aggressivenessInput.value).toFixed(2);
    });

    promptInput.addEventListener("input", () => {
      const count = estimateTokenCount(promptInput.value);
      inputStatus.textContent = `${count} est. tokens`;
    });
    promptInput.dispatchEvent(new Event("input"));

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

      try {
        const response = await fetch("/compress", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            text,
            aggressiveness: Number(aggressivenessInput.value),
            include_sections: true,
          }),
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
        elapsed.textContent = `${Math.round(data.elapsed_ms)} ms`;
        setStatus("Complete");
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
    version="0.1.0",
    description="Fast prompt compression API backed by a token-classification model.",
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
)

compression_service = PromptCompressionService()


@app.get("/", response_class=HTMLResponse)
def index() -> HTMLResponse:
    return HTMLResponse(content=APP_HTML, headers=DASHBOARD_EMBED_HEADERS)


@app.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    return HealthResponse(
        status="ok",
        model=compression_service.model_name,
        model_loaded=compression_service.is_loaded,
    )


@app.post("/compress", response_model=CompressResponse)
def compress(request: CompressRequest) -> CompressResponse:
    try:
        result = compression_service.compress(
            text=request.text,
            aggressiveness=request.aggressiveness,
            include_sections=request.include_sections,
        )
    except CompressionRuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    return CompressResponse(
        compressed_text=result.compressed_text,
        original_tokens=result.original_tokens,
        compressed_tokens=result.compressed_tokens,
        reduction=result.reduction,
        aggressiveness=result.aggressiveness,
        target_rate=result.target_rate,
        model=result.model,
        elapsed_ms=result.elapsed_ms,
        labeled_tokens=[asdict(token) for token in result.labeled_tokens],
        output_sections=[
            asdict(section)
            for section in result.output_sections
        ],
    )


@app.post("/v1/compress", response_model=V1CompressResponse)
def compress_v1(
    request: V1CompressRequest,
) -> V1CompressResponse:
    aggressiveness = DEFAULT_AGGRESSIVENESS
    if (
        request.compression_settings is not None
        and request.compression_settings.aggressiveness is not None
    ):
        aggressiveness = request.compression_settings.aggressiveness

    try:
        result = compression_service.compress(
            text=request.input,
            aggressiveness=aggressiveness,
            include_sections=False,
        )
    except CompressionRuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    tokens_saved = max(0, result.original_tokens - result.compressed_tokens)
    compression_ratio = (
        0.0
        if result.compressed_tokens == 0
        else result.original_tokens / result.compressed_tokens
    )

    return V1CompressResponse(
        output=result.compressed_text,
        output_tokens=result.compressed_tokens,
        input_tokens=result.original_tokens,
        original_input_tokens=result.original_tokens,
        tokens_saved=tokens_saved,
        compression_ratio=compression_ratio,
        compression_time=result.elapsed_ms,
        warnings=[],
    )
