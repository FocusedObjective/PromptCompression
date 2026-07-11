EMBED_HTML = """
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Prompt Compression</title>
  <style>
    :root { color-scheme: light; --bg:#f5f7fb; --panel:#fff; --text:#17202a; --muted:#617083; --border:#d7dee8; --accent:#1769aa; --accent-dark:#0e4e84; --drop-bg:#ffe4e0; --drop:#9f2f24; }
    * { box-sizing: border-box; }
    body { margin:0; background:var(--bg); color:var(--text); font-family:Inter,ui-sans-serif,system-ui,-apple-system,"Segoe UI",sans-serif; }
    main { width:min(1180px,calc(100vw - 32px)); margin:0 auto; padding:24px 0; }
    header { display:flex; justify-content:space-between; align-items:end; gap:16px; margin-bottom:18px; }
    h1 { margin:0; font-size:28px; line-height:1.15; }
    .subhead { margin:7px 0 0; color:var(--muted); font-size:14px; }
    .stats { display:flex; gap:10px; flex-wrap:wrap; justify-content:flex-end; }
    .stat { min-width:112px; padding:10px 12px; background:var(--panel); border:1px solid var(--border); border-radius:8px; box-shadow:0 10px 30px rgba(24,39,75,.08); }
    .stat strong { display:block; font-size:18px; }
    .stat span,.status { color:var(--muted); font-size:12px; }
    .workspace { display:grid; grid-template-columns:minmax(0,1fr) minmax(0,1fr); gap:16px; }
    section { min-width:0; overflow:hidden; background:var(--panel); border:1px solid var(--border); border-radius:8px; box-shadow:0 10px 30px rgba(24,39,75,.08); }
    .panel-head,.controls { display:flex; align-items:center; justify-content:space-between; gap:12px; padding:12px 16px; }
    .panel-head { border-bottom:1px solid var(--border); }
    .controls { border-top:1px solid var(--border); flex-wrap:wrap; }
    h2 { margin:0; font-size:15px; }
    textarea { display:block; width:100%; min-height:430px; max-height:65vh; padding:16px; border:0; outline:0; resize:vertical; color:var(--text); font:14px/1.55 ui-monospace,SFMono-Regular,Consolas,"Liberation Mono",monospace; }
    .output { min-height:430px; max-height:65vh; padding:16px; overflow:auto; resize:vertical; white-space:pre-wrap; overflow-wrap:anywhere; font:14px/1.6 ui-monospace,SFMono-Regular,Consolas,"Liberation Mono",monospace; }
    .token { color:#16324f; } .token.drop { color:var(--drop); background:var(--drop-bg); border-radius:4px; padding:1px 2px; text-decoration:line-through; text-decoration-thickness:1.5px; }
    .section { margin:0 0 14px; }
    .section-label { display:inline-flex; margin:4px 0 8px; padding:3px 8px; border-radius:999px; background:#e8f3ec; color:#25613b; font:600 12px/1.2 Inter,ui-sans-serif,system-ui,sans-serif; }
    .structured-block { display:block; margin:0; padding:12px; border:1px solid #b9d8c3; border-radius:7px; background:#f6fbf7; color:#16324f; white-space:pre-wrap; overflow:auto; font:inherit; }
    .examples { display:flex; align-items:center; gap:7px; color:var(--muted); font-size:12px; font-weight:650; }
    .example-button { min-height:32px; padding:0 10px; background:#e8f1f8; color:#1769aa; font-size:12px; }
    .example-button:hover { background:#dbeaf6; }
    .aggressiveness { display:flex; align-items:center; gap:9px; color:var(--muted); font-size:13px; font-weight:650; }
    input[type=range] { width:180px; accent-color:var(--accent); }
    button { min-height:38px; padding:0 15px; border:0; border-radius:7px; background:var(--accent); color:#fff; font-weight:680; cursor:pointer; }
    button:hover { background:var(--accent-dark); } button:disabled { cursor:wait; opacity:.65; }
    .copy-button { min-height:32px; padding:0 12px; font-size:13px; } .error { color:#a62b2b; }
    @media (max-width:860px) { main { width:min(100% - 24px,1180px); padding:16px 0; } header { align-items:stretch; flex-direction:column; } .stat { flex:1 1 100px; } .workspace { grid-template-columns:1fr; } textarea,.output { min-height:300px; } }
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
      </div>
    </header>
    <div class="workspace">
      <section>
        <div class="panel-head"><h2>Original Prompt</h2></div>
        <textarea id="prompt" aria-label="Prompt to compress" spellcheck="false" placeholder="Paste a prompt to compress..."></textarea>
        <div class="controls">
          <div class="examples" aria-label="Load an example">
            <span>Try an example:</span>
            <button class="example-button" id="loadJsonExampleButton" type="button">Text + JSON</button>
            <button class="example-button" id="loadHtmlExampleButton" type="button">HTML Page</button>
            <button class="example-button" id="loadTranscriptExampleButton" type="button">Meeting Transcript</button>
          </div>
          <label class="aggressiveness" for="aggressiveness">Aggressiveness <input id="aggressiveness" type="range" min="0" max="1" step="0.05" value="0.30"><strong id="aggressivenessValue">0.30</strong></label>
          <button id="compressButton" type="button">Compress</button>
        </div>
      </section>
      <section>
        <div class="panel-head"><h2>Dropped Words Highlighted</h2><button class="copy-button" id="copyButton" type="button" disabled>Copy Compressed</button></div>
        <div class="output" id="diff" aria-live="polite"></div>
        <div class="controls"><span class="status" id="resultStatus">Paste a prompt to begin</span></div>
      </section>
    </div>
  </main>
  <script>
    const promptInput = document.getElementById("prompt");
    const aggressivenessInput = document.getElementById("aggressiveness");
    const aggressivenessValue = document.getElementById("aggressivenessValue");
    const loadJsonExampleButton = document.getElementById("loadJsonExampleButton");
    const loadHtmlExampleButton = document.getElementById("loadHtmlExampleButton");
    const loadTranscriptExampleButton = document.getElementById("loadTranscriptExampleButton");
    const compressButton = document.getElementById("compressButton");
    const copyButton = document.getElementById("copyButton");
    const diff = document.getElementById("diff");
    const resultStatus = document.getElementById("resultStatus");
    const reduction = document.getElementById("reduction");
    const tokens = document.getElementById("tokens");
    let latestCompressedText = "";
    const JSON_EXAMPLE = `You are a support operations analyst preparing a concise escalation brief.
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
- Next three actions`;
    const HTML_PAGE_EXAMPLE = `Compress this downloaded web page while keeping the document structure and main facts.

<!doctype html>
<html lang="en">
<head><title>Q3 Customer Operations Update</title><style>.banner{display:block}.ad{display:block}</style></head>
<body>
  <header><nav><a href="/">Home</a><a href="/product">Product</a><a href="/contact">Contact</a></nav></header>
  <aside class="banner">Subscribe for weekly operations news and product announcements.</aside>
  <main><article>
    <h1>Q3 Customer Operations Update</h1>
    <p>Customer response times improved after routing routine billing questions to self-service support.</p>
    <p>The remaining risk is payment timeout incidents during peak traffic after the July deployment.</p>
    <h2>Required actions</h2>
    <ul><li>Keep retry_count at or below 3.</li><li>Assign the payments team to review incident INC-1042 by 2026-08-15.</li><li>Share the dashboard at https://example.com/dashboards/payments.</li></ul>
  </article></main>
  <aside class="ad">Sponsored: Upgrade your support plan today.</aside>
  <footer>Copyright 2026 Example Corp. Privacy · Terms · Careers</footer>
</body>
</html>`;
    const MEETING_TRANSCRIPT_EXAMPLE = `Create a concise escalation summary from this customer operations meeting. Keep owners, dates, incident IDs, URLs, and exact limits.

Maya (Support): Thanks everyone. To repeat the background, Acme Retail has reported intermittent checkout timeouts since the July 8 deployment window. They opened INC-1042 yesterday and their account executive needs an update before Friday.

Leo (Engineering): We saw the same issue in the payments dashboard at https://example.com/dashboards/payments. The error rate peaks around 10:00 Pacific. The working theory is retry storms, but we have not confirmed it yet.

Maya (Support): For context, the customer has already contacted us three times. The account is enterprise, their renewal is in September, and they are concerned that another outage will affect the launch campaign.

Priya (Payments): I can take the incident review. I will compare the July 8 deployment changes with the retry metrics, and I will post findings by 2026-08-15. We should not raise retry_count above 3; that is a hard safety limit.

Leo (Engineering): Agreed. I can also add targeted logging today. We do not yet need a rollback, and I do not want to promise a root cause before we have the traces.

Maya (Support): Great. I will send the account executive a short update: current risk, the likely blocker, Priya as owner, and the next actions. Please keep the wording factual.

Priya (Payments): One more note: the dashboard also shows a smaller spike for merchant group west-2, but it may be unrelated. I will include it in the investigation notes rather than the customer summary.

Output:
- Executive summary
- Blocker and owner
- Next three actions`;

    promptInput.value = JSON_EXAMPLE;
    resultStatus.textContent = "Text + JSON example loaded";

    function setStatus(message, isError = false) { resultStatus.textContent = message; resultStatus.className = isError ? "status error" : "status"; }
    function loadExample(text, name) { promptInput.value = text; latestCompressedText = ""; copyButton.disabled = true; diff.textContent = ""; reduction.textContent = "-"; tokens.textContent = "-"; setStatus(`${name} example loaded`); promptInput.focus(); }
    function renderTokens(container, labeledTokens) { for (const token of labeledTokens || []) { const span = document.createElement("span"); span.className = token.kept ? "token" : "token drop"; span.textContent = token.text; container.append(span, document.createTextNode(" ")); } }
    function sectionLabel(section) { return { toon:"JSON compressed to TOON", json:"JSON protected", json_minified:"JSON minified", html:"HTML protected", html_markdown:"HTML page converted to Markdown", nocompress:"No-compress protected", code:"Code protected", verbatim:"Verbatim protected" }[section.kind] || ""; }
    function renderResult(sections, labeledTokens) {
      diff.textContent = "";
      if (!sections || !sections.length) { renderTokens(diff, labeledTokens); return; }
      for (const section of sections) {
        if (!section.text) continue;
        const wrapper = document.createElement("div"); wrapper.className = "section";
        const label = sectionLabel(section);
        if (label) { const labelNode = document.createElement("div"); labelNode.className = "section-label"; labelNode.textContent = label; const block = document.createElement("pre"); block.className = "structured-block"; block.textContent = section.text; wrapper.append(labelNode, block); }
        else renderTokens(wrapper, section.labeled_tokens);
        diff.appendChild(wrapper);
      }
      if (!diff.textContent) diff.textContent = "No labels returned by the compressor.";
    }
    aggressivenessInput.addEventListener("input", () => { aggressivenessValue.textContent = Number(aggressivenessInput.value).toFixed(2); });
    loadJsonExampleButton.addEventListener("click", () => loadExample(JSON_EXAMPLE, "Text + JSON"));
    loadHtmlExampleButton.addEventListener("click", () => loadExample(HTML_PAGE_EXAMPLE, "HTML page"));
    loadTranscriptExampleButton.addEventListener("click", () => loadExample(MEETING_TRANSCRIPT_EXAMPLE, "Meeting transcript"));
    copyButton.addEventListener("click", async () => { if (!latestCompressedText) return; try { await navigator.clipboard.writeText(latestCompressedText); setStatus("Copied compressed prompt"); } catch { setStatus("Unable to copy automatically", true); } });
    compressButton.addEventListener("click", async () => {
      const text = promptInput.value.trim();
      if (!text) { setStatus("Paste a prompt first", true); return; }
      compressButton.disabled = true; copyButton.disabled = true; latestCompressedText = ""; diff.textContent = ""; setStatus("Compressing...");
      try {
        const response = await fetch("/compress", { method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify({ text, aggressiveness:Number(aggressivenessInput.value), include_sections:true, include_diagnostics:false }) });
        const data = await response.json(); if (!response.ok) throw new Error(data.detail || "Compression failed");
        latestCompressedText = data.compressed_text; copyButton.disabled = !latestCompressedText; renderResult(data.output_sections, data.labeled_tokens);
        reduction.textContent = `${Math.round(data.reduction * 100)}%`; tokens.textContent = `${data.original_tokens} → ${data.compressed_tokens}`; tokens.title = data.token_estimator || ""; setStatus("Compression complete");
      } catch (error) { setStatus(error.message || "Compression failed", true); }
      finally { compressButton.disabled = false; }
    });
  </script>
</body>
</html>
"""
