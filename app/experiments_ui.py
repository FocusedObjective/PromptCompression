EXPERIMENTS_HTML = """
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="description" content="Evidence ledger for Prompt Compression experiments, safety controls, benchmark results, and promotion decisions.">
  <title>Prompt Compression Experiments</title>
  <style>
    :root {
      color-scheme: light;
      --canvas: #f4f6f8;
      --paper: #ffffff;
      --paper-soft: #f8fafb;
      --ink: #17212b;
      --muted: #617080;
      --line: #d8e0e7;
      --navy: #173b57;
      --teal: #0d796f;
      --teal-soft: #e2f4f1;
      --amber: #9a5a08;
      --amber-soft: #fff1d6;
      --slate-soft: #edf1f5;
      --shadow: 0 16px 42px rgba(25, 48, 70, 0.08);
    }

    * { box-sizing: border-box; }

    html { scroll-behavior: smooth; }

    body {
      margin: 0;
      background:
        radial-gradient(circle at 85% 0%, rgba(13, 121, 111, 0.10), transparent 27rem),
        var(--canvas);
      color: var(--ink);
      font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    }

    a { color: var(--teal); text-decoration: none; }
    a:hover { text-decoration: underline; }

    .shell {
      width: min(1180px, calc(100vw - 32px));
      margin: 0 auto;
      padding: 28px 0 56px;
    }

    .masthead {
      overflow: hidden;
      position: relative;
      padding: 30px;
      border: 1px solid #244c68;
      border-radius: 16px;
      background: var(--navy);
      color: #ffffff;
      box-shadow: var(--shadow);
    }

    .masthead::after {
      content: "";
      position: absolute;
      width: 300px;
      height: 300px;
      right: -130px;
      top: -170px;
      border: 52px solid rgba(255, 255, 255, 0.06);
      border-radius: 50%;
    }

    .eyebrow {
      margin: 0 0 10px;
      color: #8fe0d5;
      font-size: 12px;
      font-weight: 780;
      letter-spacing: 0.12em;
      text-transform: uppercase;
    }

    h1 {
      max-width: 740px;
      margin: 0;
      font-size: clamp(34px, 6vw, 60px);
      line-height: 0.98;
      letter-spacing: -0.045em;
    }

    .lede {
      max-width: 760px;
      margin: 18px 0 0;
      color: #dce8ef;
      font-size: 16px;
      line-height: 1.6;
    }

    .nav-links {
      display: flex;
      gap: 16px;
      flex-wrap: wrap;
      margin-top: 22px;
    }

    .nav-link {
      color: #ffffff;
      font-size: 13px;
      font-weight: 700;
    }

    .nav-link[aria-current="page"] { color: #8fe0d5; }

    .snapshot {
      display: grid;
      grid-template-columns: repeat(4, minmax(0, 1fr));
      gap: 1px;
      overflow: hidden;
      margin-top: 18px;
      border: 1px solid var(--line);
      border-radius: 12px;
      background: var(--line);
      box-shadow: var(--shadow);
    }

    .metric {
      min-height: 112px;
      padding: 20px;
      background: var(--paper);
    }

    .metric strong {
      display: block;
      color: var(--navy);
      font-size: 29px;
      line-height: 1;
      letter-spacing: -0.035em;
    }

    .metric span {
      display: block;
      margin-top: 9px;
      color: var(--muted);
      font-size: 13px;
      line-height: 1.35;
    }

    .section {
      margin-top: 34px;
      scroll-margin-top: 20px;
    }

    .section-heading {
      display: flex;
      justify-content: space-between;
      align-items: end;
      gap: 20px;
      margin-bottom: 14px;
    }

    h2 {
      margin: 0;
      color: var(--navy);
      font-size: 25px;
      letter-spacing: -0.025em;
    }

    h3 {
      margin: 0;
      color: var(--navy);
      font-size: 16px;
      line-height: 1.3;
    }

    p { margin: 7px 0 0; color: var(--muted); line-height: 1.55; }

    .section-note {
      max-width: 640px;
      font-size: 14px;
      text-align: right;
    }

    .phase-grid {
      display: grid;
      grid-template-columns: repeat(5, minmax(0, 1fr));
      gap: 10px;
    }

    .phase {
      position: relative;
      min-height: 190px;
      padding: 18px;
      border: 1px solid var(--line);
      border-radius: 12px;
      background: var(--paper);
    }

    .phase-number {
      display: inline-grid;
      place-items: center;
      width: 28px;
      height: 28px;
      margin-bottom: 18px;
      border-radius: 50%;
      background: var(--slate-soft);
      color: var(--navy);
      font-size: 12px;
      font-weight: 800;
    }

    .phase p { font-size: 13px; }

    .status {
      display: inline-flex;
      align-items: center;
      min-height: 24px;
      padding: 0 9px;
      border-radius: 999px;
      font-size: 11px;
      font-weight: 800;
      letter-spacing: 0.03em;
      text-transform: uppercase;
      white-space: nowrap;
    }

    .accepted { background: var(--teal-soft); color: var(--teal); }
    .revise { background: var(--amber-soft); color: var(--amber); }
    .pending { background: var(--slate-soft); color: #526171; }

    .phase .status { position: absolute; left: 18px; bottom: 16px; }

    .run-grid {
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 12px;
    }

    .run-card {
      padding: 22px;
      border: 1px solid var(--line);
      border-radius: 12px;
      background: var(--paper);
      box-shadow: 0 8px 24px rgba(25, 48, 70, 0.04);
    }

    .run-head {
      display: flex;
      justify-content: space-between;
      gap: 14px;
      align-items: start;
    }

    .run-kicker {
      color: var(--teal);
      font-size: 12px;
      font-weight: 780;
      text-transform: uppercase;
      letter-spacing: 0.08em;
    }

    .run-card h3 { margin-top: 5px; font-size: 20px; }

    .run-stats {
      display: grid;
      grid-template-columns: repeat(3, minmax(0, 1fr));
      gap: 8px;
      margin-top: 18px;
    }

    .run-stat {
      padding: 12px;
      border-radius: 8px;
      background: var(--paper-soft);
    }

    .run-stat strong { display: block; color: var(--navy); font-size: 18px; }
    .run-stat span { display: block; margin-top: 4px; color: var(--muted); font-size: 11px; line-height: 1.3; }

    .run-list, .next-list {
      margin: 16px 0 0;
      padding-left: 18px;
      color: var(--muted);
      font-size: 13px;
      line-height: 1.5;
    }

    .run-list li, .next-list li { margin: 6px 0; }

    .table-wrap {
      overflow-x: auto;
      border: 1px solid var(--line);
      border-radius: 12px;
      background: var(--paper);
      box-shadow: var(--shadow);
    }

    table {
      width: 100%;
      min-width: 920px;
      border-collapse: collapse;
    }

    th, td {
      padding: 14px 15px;
      border-bottom: 1px solid var(--line);
      text-align: left;
      vertical-align: top;
      font-size: 13px;
      line-height: 1.45;
    }

    th {
      background: var(--paper-soft);
      color: #51606f;
      font-size: 11px;
      font-weight: 800;
      letter-spacing: 0.06em;
      text-transform: uppercase;
    }

    td:first-child { width: 21%; color: var(--navy); font-weight: 760; }
    td:nth-child(2) { width: 16%; }
    td:nth-child(3) { width: 38%; }
    tr:last-child td { border-bottom: 0; }

    .finding {
      display: grid;
      grid-template-columns: 5px 1fr;
      overflow: hidden;
      margin-top: 14px;
      border: 1px solid #ecd7b5;
      border-radius: 12px;
      background: #fffaf0;
    }

    .finding-bar { background: #d68a20; }
    .finding-body { padding: 18px 20px; }
    .finding-body h3 { color: #774305; }
    .finding-body p { color: #765d3c; font-size: 13px; }

    .principles {
      display: grid;
      grid-template-columns: repeat(3, minmax(0, 1fr));
      gap: 10px;
    }

    .principle {
      padding: 18px;
      border: 1px solid var(--line);
      border-radius: 10px;
      background: var(--paper);
    }

    .principle p { font-size: 13px; }

    .next-panel {
      display: grid;
      grid-template-columns: 1.1fr 0.9fr;
      gap: 1px;
      overflow: hidden;
      border: 1px solid var(--line);
      border-radius: 12px;
      background: var(--line);
    }

    .next-panel > div { padding: 22px; background: var(--paper); }

    .condition-code {
      margin-top: 14px;
      padding: 14px;
      border-radius: 8px;
      background: #122f45;
      color: #dce8ef;
      font: 12px/1.65 ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
      white-space: pre-wrap;
    }

    footer {
      display: flex;
      justify-content: space-between;
      gap: 20px;
      margin-top: 30px;
      padding-top: 18px;
      border-top: 1px solid var(--line);
      color: var(--muted);
      font-size: 12px;
      line-height: 1.5;
    }

    @media (max-width: 980px) {
      .snapshot { grid-template-columns: repeat(2, minmax(0, 1fr)); }
      .phase-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
      .principles { grid-template-columns: 1fr; }
    }

    @media (max-width: 720px) {
      .shell { width: min(100% - 20px, 1180px); padding-top: 10px; }
      .masthead { padding: 24px 20px; border-radius: 12px; }
      .snapshot, .run-grid, .next-panel { grid-template-columns: 1fr; }
      .phase-grid { grid-template-columns: 1fr; }
      .section-heading { align-items: start; flex-direction: column; }
      .section-note { text-align: left; }
      footer { flex-direction: column; }
    }
  </style>
</head>
<body>
  <main class="shell">
    <header class="masthead">
      <p class="eyebrow">Evidence ledger · Updated July 14, 2026</p>
      <h1>Compression experiments, with receipts.</h1>
      <p class="lede">A living record of what we tried, what actually saved tokens, what stayed intact, and what still needs proof. Experiments graduate only when savings are causal, repeatable, tokenizer-positive, and safe on held-out tenant data.</p>
      <nav class="nav-links" aria-label="Primary navigation">
        <a class="nav-link" href="/">Compression UI</a>
        <a class="nav-link" href="/eval">Eval Suite</a>
        <a class="nav-link" href="/benchmark">Benchmark</a>
        <a class="nav-link" href="/research">Research</a>
        <a class="nav-link" href="/docs">API Docs</a>
        <a class="nav-link" href="/experiments" aria-current="page">Experiments</a>
      </nav>
    </header>

    <section class="snapshot" aria-label="Program snapshot">
      <div class="metric"><strong>8</strong><span>implemented experiments and safety controls</span></div>
      <div class="metric"><strong>2</strong><span>documented evidence cohorts</span></div>
      <div class="metric"><strong>0</strong><span>savings transforms promoted to safe_stack_v1</span></div>
      <div class="metric"><strong>1</strong><span>accepted safety layer: shielding plus rollback</span></div>
    </section>

    <section class="section" id="program">
      <div class="section-heading">
        <div><p class="eyebrow">Program sequence</p><h2>Current phases</h2></div>
        <p class="section-note">Small, attributable changes first. Cumulative behavior waits until each experiment earns promotion independently.</p>
      </div>
      <div class="phase-grid">
        <article class="phase"><span class="phase-number">0</span><h3>Measurement &amp; integrity</h3><p>Allowlisted profiles, provenance, stage accounting, critical-clause shielding, and final rollback.</p><span class="status accepted">Safety accepted</span></article>
        <article class="phase"><span class="phase-number">1</span><h3>Existing safe features</h3><p>Strict whitespace, safe JSON minification fallback, and repeated literal aliases.</p><span class="status revise">Evaluated · revise</span></article>
        <article class="phase"><span class="phase-number">2</span><h3>Threshold expansion</h3><p>Tokenizer-backed matrices for JSON-to-TOON and HTML-to-Markdown.</p><span class="status revise">Evaluated · revise</span></article>
        <article class="phase"><span class="phase-number">3</span><h3>Tenant-specific structure</h3><p>Exact approved boilerplate and aliases for classified generated wrappers only.</p><span class="status pending">Needs eligible data</span></article>
        <article class="phase"><span class="phase-number">4</span><h3>Safe stack</h3><p>Combine only experiments with positive held-out savings and zero accepted hard failures.</p><span class="status pending">Intentionally empty</span></article>
      </div>
    </section>

    <section class="section" id="results">
      <div class="section-heading">
        <div><p class="eyebrow">Evidence to date</p><h2>Benchmark cohorts</h2></div>
        <p class="section-note">Aggregate savings across alternative arms are never presented as deployable savings.</p>
      </div>
      <div class="run-grid">
        <article class="run-card">
          <div class="run-head"><div><span class="run-kicker">Release matrix</span><h3>Fixed safety corpus</h3></div><span class="status revise">No promotions</span></div>
          <p>Ten fixed cases, three repeats, and four causal conditions per profile on release <strong>2026.07.13.205912</strong>.</p>
          <div class="run-stats">
            <div class="run-stat"><strong>840</strong><span>full-matrix records</span></div>
            <div class="run-stat"><strong>0</strong><span>accepted hard failures</span></div>
            <div class="run-stat"><strong>0</strong><span>incremental deterministic tokens</span></div>
          </div>
          <ul class="run-list">
            <li>Deterministic baseline and experiment arms were identical.</li>
            <li>Experiment-plus-model accepted 10.5% total savings after rollback.</li>
            <li>No savings experiment met the positive held-out criterion.</li>
          </ul>
        </article>

        <article class="run-card">
          <div class="run-head"><div><span class="run-kicker">Tenant subset</span><h3>Delivery Tower prompt slice</h3></div><span class="status pending">Directional only</span></div>
          <p>Twenty-five records from one tenant corpus, representing fourteen unique texts. One repeat and six profile-plus-model arms.</p>
          <div class="run-stats">
            <div class="run-stat"><strong>15.9%</strong><span>one-arm model savings</span></div>
            <div class="run-stat"><strong>0</strong><span>deterministic applications</span></div>
            <div class="run-stat"><strong>100%</strong><span>hard protected-span retention</span></div>
          </div>
          <ul class="run-list">
            <li>Savings came entirely from LLMLingua, not the named deterministic profiles.</li>
            <li>The service used <code>default:base</code>; tenant-specific settings were not exercised.</li>
            <li>Constraint and required-term coverage were zero, so semantic acceptance remains open.</li>
          </ul>
        </article>
      </div>
    </section>

    <section class="section" id="experiments">
      <div class="section-heading">
        <div><p class="eyebrow">Decision register</p><h2>Experiment results</h2></div>
        <p class="section-note"><span class="status accepted">Accepted</span> safety control · <span class="status revise">Revise</span> clean but insufficient evidence · <span class="status pending">Pending</span> missing eligible held-out data</p>
      </div>
      <div class="table-wrap">
        <table>
          <thead><tr><th>Experiment</th><th>Status</th><th>What the evidence says</th><th>Next proof</th></tr></thead>
          <tbody>
            <tr><td>Integrity rollback &amp; critical-clause shielding</td><td><span class="status accepted">Accepted safety</span></td><td>Rejected model output never counts as savings. Release and tenant cohorts recorded zero accepted hard-integrity failures; exact URLs, identifiers, code, numbers, links, constants, and shielded critical clauses were retained.</td><td>Keep unconditional. Expand semantic and entity-relationship evaluation coverage.</td></tr>
            <tr><td>Strict prose whitespace</td><td><span class="status revise">Revise</span></td><td>No tokenizer-positive application in either cohort. The tenant slice contained 336 blank lines, but all candidates correctly failed the token-savings gate.</td><td>Test prose with actual tokenizer-costly spacing while retaining Markdown and aligned-text exclusions.</td></tr>
            <tr><td>Safe JSON minification</td><td><span class="status revise">Revise</span></td><td>No minification applied. The tenant slice had 19 strict JSON arrays, all below threshold. A skipped candidate changed model placeholdering on 7 of 25 records, so its extra 33 saved tokens are not attributable to minification.</td><td>Make skipped candidates model-input neutral, then rerun the causal matrix.</td></tr>
            <tr><td>Repeated literal aliases</td><td><span class="status revise">Revise</span></td><td>No eligible repeated long URL or identifier appeared in either held-out cohort.</td><td>Use held-out prompts with naturally repeated literals and verify exact expansion.</td></tr>
            <tr><td>Expanded JSON-to-TOON</td><td><span class="status revise">Revise</span></td><td>All 36 threshold cells matched baseline on the fixed corpus. The tenant slice produced no additional eligible region.</td><td>Collect larger typed JSON records where the tokenizer gate can actually discriminate thresholds.</td></tr>
            <tr><td>Expanded HTML-to-Markdown</td><td><span class="status revise">Revise</span></td><td>All 300/500/1000-character cells produced zero applications. The tenant slice contained no eligible HTML region.</td><td>Evaluate real article/main HTML with exact link and visible-text preservation.</td></tr>
            <tr><td>Tenant-approved exact boilerplate</td><td><span class="status pending">Pending</span></td><td>Discovery remains diagnostics-only. The fixed benchmark had too few records for approval, and the tenant subset used the default profile without approved phrases.</td><td>Approve a versioned exact phrase set from at least 50 records, then evaluate held-out tenant data.</td></tr>
            <tr><td>Classified duplicate-wrapper aliases</td><td><span class="status revise">Revise</span></td><td>No classified generated-support wrapper appeared. Generic duplicate removal stayed diagnostics-only as intended.</td><td>Build a held-out corpus of explicitly classified generated wrappers with exact expansion tests.</td></tr>
          </tbody>
        </table>
      </div>

      <aside class="finding" aria-label="Open JSON finding">
        <div class="finding-bar"></div>
        <div class="finding-body"><h3>Open finding: a skipped transform must not become a hidden model experiment</h3><p>In the Delivery Tower slice, <code>json_minify_safe</code> applied zero deterministic changes, yet its model input differed on seven records because candidate detection altered placeholdering. Until skipped candidates are input-neutral—or explicitly measured as a separate intervention—we will not credit their downstream model savings to JSON minification.</p></div>
      </aside>
    </section>

    <section class="section" id="guardrails">
      <div class="section-heading">
        <div><p class="eyebrow">Promotion contract</p><h2>What “safe” means here</h2></div>
        <p class="section-note">A clean integrity report is necessary, but it is not the same thing as semantic task success.</p>
      </div>
      <div class="principles">
        <article class="principle"><h3>Causal savings</h3><p>Every applied record must clear absolute and relative tokenizer gates, and stage accounting must reconcile exactly. Rejected output contributes zero savings.</p></article>
        <article class="principle"><h3>Hard integrity</h3><p>Protected literals, constraints, required terms, JSON, code, and structural guardrails must have zero failures on accepted output.</p></article>
        <article class="principle"><h3>Downstream fidelity</h3><p>Negation, obligations, permissions, scope, thresholds, required formats, and entity-value relationships need explicit task evaluation—not proxy metrics alone.</p></article>
        <article class="principle"><h3>Repeatability</h3><p>Deterministic and final output hashes must be stable across at least three identical repeats, with stable skip and rollback reasons.</p></article>
        <article class="principle"><h3>Held-out evidence</h3><p>Discovery and evaluation records remain separate. Tenant results are reported independently, including zero-application cohorts.</p></article>
        <article class="principle"><h3>Reversible rollout</h3><p>Profiles are allowlisted and request-scoped. Removing a profile selection restores baseline behavior without rewriting tenant content.</p></article>
      </div>
    </section>

    <section class="section" id="next">
      <div class="section-heading"><div><p class="eyebrow">Next evidence</p><h2>Required shape of the next run</h2></div></div>
      <div class="next-panel">
        <div>
          <h3>Run all four causal arms</h3>
          <div class="condition-code">1  baseline deterministic
2  experiment deterministic
3  baseline model-only · deterministic off
4  experiment + model-force</div>
          <p>Use identical prompt order, tenant profile, tokenizer, model revision, aggressiveness, and at least three repeats.</p>
        </div>
        <div>
          <h3>Close the current evidence gaps</h3>
          <ul class="next-list">
            <li>Pass the actual versioned tenant profile.</li>
            <li>Populate required terms and task-specific constraints.</li>
            <li>Add relationship, negation, permission, and required-format checks.</li>
            <li>Repair skipped-JSON model-input neutrality.</li>
            <li>Include deliberately eligible records plus a held-out natural sample.</li>
          </ul>
        </div>
      </div>
    </section>

    <footer>
      <span>Current release evidence: deployment 2026.07.13.205912 · compressor commit 019e118 · LLMLingua-2 model revision 5f0c8279.</span>
      <span>This page is cumulative. Revisions add cohorts; they do not overwrite prior decisions.</span>
    </footer>
  </main>
</body>
</html>
"""
