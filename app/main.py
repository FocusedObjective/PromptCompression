from dataclasses import asdict

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse

from app.compressor import CompressionRuntimeError, PromptCompressionService
from app.schemas import CompressRequest, CompressResponse, HealthResponse

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
        <div class="stat"><strong id="tokens">-</strong><span>Tokens</span></div>
        <div class="stat"><strong id="elapsed">-</strong><span>Elapsed</span></div>
      </div>
    </header>

    <div class="workspace">
      <section>
        <div class="panel-head">
          <h2>Original Prompt</h2>
          <span class="status" id="inputStatus">Ready</span>
        </div>
        <textarea id="prompt" spellcheck="false">Prompts are production code. Manage them that way.

Do not remove API keys, URLs, dates, or hard constraints.
The assistant must return concise output and preserve critical details.</textarea>
        <div class="controls">
          <label>
            Aggressiveness
            <input id="aggressiveness" type="range" min="0" max="1" step="0.05" value="0.25">
            <strong id="aggressivenessValue">0.25</strong>
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

    function renderDiff(labeledTokens) {
      diff.textContent = "";
      if (!labeledTokens || labeledTokens.length === 0) {
        diff.textContent = "No labels returned by the compressor.";
        return;
      }

      for (const token of labeledTokens) {
        const span = document.createElement("span");
        span.className = token.kept ? "token keep" : "token drop";
        span.textContent = token.text;
        diff.appendChild(span);
        diff.appendChild(document.createTextNode(" "));
      }
    }

    aggressivenessInput.addEventListener("input", () => {
      aggressivenessValue.textContent = Number(aggressivenessInput.value).toFixed(2);
    });

    promptInput.addEventListener("input", () => {
      const count = promptInput.value.trim() ? promptInput.value.trim().split(/\\s+/).length : 0;
      inputStatus.textContent = `${count} words`;
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
          }),
        });

        const data = await response.json();
        if (!response.ok) {
          throw new Error(data.detail || "Compression failed");
        }

        latestCompressedText = data.compressed_text;
        copyButton.disabled = !latestCompressedText;
        renderDiff(data.labeled_tokens);
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

compression_service = PromptCompressionService()


@app.get("/", response_class=HTMLResponse)
def index() -> str:
    return APP_HTML


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
        )
    except CompressionRuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    return CompressResponse(**asdict(result))
