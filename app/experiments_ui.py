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
      <p class="eyebrow">Evidence ledger · Updated July 15, 2026</p>
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
      <div class="metric"><strong>10</strong><span>tracked experiments and safety decisions</span></div>
      <div class="metric"><strong>7</strong><span>completed evidence cohorts used in the register</span></div>
      <div class="metric"><strong>0</strong><span>savings transforms promoted to safe_stack_v1</span></div>
      <div class="metric"><strong>2</strong><span>permanent safety defaults: shielding and rollback</span></div>
    </section>

    <section class="section" id="program">
      <div class="section-heading">
        <div><p class="eyebrow">Program sequence</p><h2>Current phases</h2></div>
        <p class="section-note">Small, attributable changes first. Cumulative behavior waits until each experiment earns promotion independently.</p>
      </div>
      <div class="phase-grid">
        <article class="phase"><span class="phase-number">0</span><h3>Measurement &amp; integrity</h3><p>Final rollback remains unconditional. Critical-clause shielding is now the default, with an explicit benchmark-only off ablation.</p><span class="status accepted">Promoted defaults</span></article>
        <article class="phase"><span class="phase-number">1</span><h3>Existing safe features</h3><p>Strict whitespace, safe JSON minification fallback, and repeated literal aliases.</p><span class="status pending">Evaluated · parked</span></article>
        <article class="phase"><span class="phase-number">2</span><h3>Threshold expansion</h3><p>Tokenizer-backed matrices for JSON-to-TOON and HTML-to-Markdown.</p><span class="status pending">Evaluated · parked</span></article>
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

        <article class="run-card">
          <div class="run-head"><div><span class="run-kicker">Tenant 1</span><h3>Large structured-prompt slice</h3></div><span class="status revise">Positive TOON signal</span></div>
          <p>Fifteen selected records, twelve unique baseline inputs, six profile-plus-model arms, and one repeat. Seven arm requests returned errors and are excluded from integrity rates.</p>
          <div class="run-stats">
            <div class="run-stat"><strong>39</strong><span>incremental deterministic tokens</span></div>
            <div class="run-stat"><strong>1</strong><span>matched TOON application</span></div>
            <div class="run-stat"><strong>0</strong><span>accepted hard failures</span></div>
          </div>
          <ul class="run-list">
            <li>Expanded TOON changed one of fourteen completed matched records: a positive 0.01% signal, not promotion evidence.</li>
            <li>The other five profiles produced zero incremental deterministic savings.</li>
            <li>Twenty-nine unsafe inline-code model candidates were rejected; constraint and required-term coverage remained zero.</li>
          </ul>
        </article>

        <article class="run-card">
          <div class="run-head"><div><span class="run-kicker">Tenant 2</span><h3>Structured workflow slice</h3></div><span class="status revise">No incremental profile savings</span></div>
          <p>Twenty-five selected records, twenty unique inputs, six profile-plus-model arms, and one repeat. All 175 configured arm records completed.</p>
          <div class="run-stats">
            <div class="run-stat"><strong>0</strong><span>incremental deterministic tokens</span></div>
            <div class="run-stat"><strong>18</strong><span>unsafe model candidates rejected</span></div>
            <div class="run-stat"><strong>100%</strong><span>accepted hard-integrity pass rate</span></div>
          </div>
          <ul class="run-list">
            <li>Baseline TOON already saved 1,428 tokens on two records; every experiment profile matched that deterministic output exactly.</li>
            <li>Each experiment-plus-model arm saved 40 model tokens, but there was no model-only arm for causal attribution.</li>
            <li>The run used one repeat and crossed an application deployment boundary, so it cannot support promotion.</li>
          </ul>
        </article>

        <article class="run-card">
          <div class="run-head"><div><span class="run-kicker">Focused release matrix · July 15</span><h3>Safety default and final transform decisions</h3></div><span class="status accepted">Safety confirmed</span></div>
          <p>Three fixed-corpus matrices, four conditions and three repeats each: 360 records on deployment <strong>2026.07.14.131419</strong>. A pre-fix run exposed the missing <code>Never imply</code> clause; the corrected v2 run is the decision source.</p>
          <div class="run-stats">
            <div class="run-stat"><strong>0</strong><span>errors or accepted hard failures</span></div>
            <div class="run-stat"><strong>9</strong><span>fewer rollbacks with shielding on</span></div>
            <div class="run-stat"><strong>138</strong><span>more accepted tokens saved with shielding on</span></div>
          </div>
          <ul class="run-list">
            <li>Shielding on: 624 accepted tokens saved, 6 rollbacks, 682 ms p50. Shielding off: 486 tokens, 15 rollbacks, 741 ms p50.</li>
            <li>All categorized relationship, negation, permission, and required-format checks passed after the detector repair.</li>
            <li>TOON and JSON experiment deterministic arms matched baseline at 381 tokens saved; neither added an application or token.</li>
          </ul>
        </article>
      </div>

      <aside class="finding" aria-label="Additional cohort quality note">
        <div class="finding-bar"></div>
        <div class="finding-body"><h3>Run-quality note: errors are not integrity failures</h3><p>The two new exports configured 280 arm records and completed 273. Seven Tenant 1 rows contained no analytics or integrity result and are classified as harness/API errors, not failed compression outputs. Across completed records, the guardrail rejected 47 unsafe model candidates while accepted outputs recorded zero hard-integrity failures. Both runs still lack three repeats, the full four-arm matrix, and semantic constraint coverage.</p></div>
      </aside>
    </section>

    <section class="section" id="experiments">
      <div class="section-heading">
        <div><p class="eyebrow">Decision register</p><h2>Experiment results</h2></div>
        <p class="section-note"><span class="status accepted">Promoted</span> permanent safety default · <span class="status revise">Run next</span> actionable evidence candidate · <span class="status pending">Parked</span> insufficient demand or eligible data</p>
      </div>
      <div class="table-wrap">
        <table>
          <thead><tr><th>Experiment</th><th>Status</th><th>What the evidence says</th><th>Next proof</th></tr></thead>
          <tbody>
            <tr><td>Final integrity validation &amp; rollback</td><td><span class="status accepted">Permanent default</span></td><td>Rejected model output never counts as savings. The two new cohorts rejected 47 unsafe candidates—35 inline-code and 12 identifier changes—while all 273 completed accepted outputs passed hard-integrity checks.</td><td>Keep unconditional on every model path and continue reporting rollback reasons separately from accepted output.</td></tr>
            <tr><td>Critical-clause shielding</td><td><span class="status accepted">Permanent default</span></td><td>The corrected ablation favored shielding on every operational measure: 624 versus 486 accepted tokens saved, 6 versus 15 rollbacks, and 682 versus 741 ms p50. All categorized downstream checks passed.</td><td>Keep on by default. Retain the off profile only as a benchmark/diagnostic control; rollback remains unconditional.</td></tr>
            <tr><td>Shielding on/off guardrail ablation</td><td><span class="status accepted">Completed</span></td><td>Three repeats of 10 fixed cases produced 120 records with zero errors and zero accepted integrity or downstream failures. The run also exposed and verified the repaired <code>Never imply</code> clause classification.</td><td>No further ablation required before release; extend the clause corpus when new policy verbs appear.</td></tr>
            <tr><td>Expanded JSON-to-TOON</td><td><span class="status pending">Parked</span></td><td>The fixed rerun did not reproduce incremental savings: baseline and experiment deterministic arms both saved 381 tokens with identical applications. The earlier 39-token tenant observation remains directional only.</td><td>Reopen only with a separate natural held-out corpus containing eligible records; do not populate <code>safe_stack_v1</code>.</td></tr>
            <tr><td>Safe JSON minification<br><code>json_minify_safe</code></td><td><span class="status pending">Parked</span></td><td>The repair is verified: zero skipped-record model-input hash mismatches. However, the experiment again applied zero minifications and added zero deterministic tokens over baseline.</td><td>Reopen only when natural traffic contains tokenizer-positive eligible JSON; the hidden model-input experiment is closed.</td></tr>
            <tr><td>Strict prose whitespace</td><td><span class="status pending">Parked</span></td><td>Eight candidate rewrites produced zero tokenizer savings and no incremental deterministic output change.</td><td>Reopen only when telemetry supplies naturally tokenizer-costly prose spacing.</td></tr>
            <tr><td>Repeated literal aliases</td><td><span class="status pending">Parked</span></td><td>No eligible repeated long URL or identifier appeared in the fixed corpus or any of the three tenant slices.</td><td>Reopen only with naturally occurring held-out examples and exact expansion checks.</td></tr>
            <tr><td>Expanded HTML-to-Markdown</td><td><span class="status pending">Parked</span></td><td>No cohort applied the transform; candidates were absent or below threshold.</td><td>Reopen only when real article/main HTML is common enough to justify a dedicated held-out corpus.</td></tr>
            <tr><td>Tenant-approved exact boilerplate</td><td><span class="status pending">Deferred</span></td><td>Discovery remains diagnostics-only. No tenant supplied an approved, versioned phrase set.</td><td>Collect at least 50 discovery records, approve a versioned exact phrase set, then evaluate separate held-out tenant data.</td></tr>
            <tr><td>Classified duplicate-wrapper aliases</td><td><span class="status pending">Parked</span></td><td>No classified generated-support wrapper appeared in either new cohort. Generic duplicate removal stayed diagnostics-only as intended.</td><td>Reopen only if production telemetry shows this explicit wrapper class is common.</td></tr>
          </tbody>
        </table>
      </div>

      <aside class="finding" aria-label="Resolved JSON implementation finding">
        <div class="finding-bar"></div>
        <div class="finding-body"><h3>Resolved and verified: skipped JSON is input-neutral</h3><p>A skipped small-JSON minification candidate now stays on the same prose path as baseline. The corrected matrix recorded zero baseline/experiment model-input hash mismatches, zero minification applications, and zero incremental savings. The historical seven-record interaction remains excluded from all savings claims.</p></div>
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
            <li><strong>Verified:</strong> categorized relationship, negation, permission, and required-format checks passed in the corrected focused run.</li>
            <li><strong>Verified:</strong> skipped-JSON model-input neutrality had zero matched hash differences.</li>
            <li><strong>Verified:</strong> the focused runner completed 360 records with zero harness/API errors.</li>
            <li>Do not run another savings-transform matrix until a natural held-out sample contains eligible, tokenizer-positive records.</li>
            <li>Include deliberately eligible records plus a held-out natural sample.</li>
          </ul>
        </div>
      </div>
    </section>

    <footer>
      <span>Decision evidence spans seven cohorts through July 15, 2026 · focused model revision recorded as local_or_unknown.</span>
      <span>This page is cumulative. Revisions add cohorts; they do not overwrite prior decisions.</span>
    </footer>
  </main>
</body>
</html>
"""
