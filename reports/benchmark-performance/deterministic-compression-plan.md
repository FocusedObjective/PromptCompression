# Deterministic Compression and LLMLingua-2 Gating Plan

Generated: 2026-07-05

## Purpose

The CPU/GPU benchmark shows that LLMLingua-2 is operationally expensive for the
amount of pure-prose savings it usually provides. Pure freeform text lands around
8-10% reduction in the benchmark, while JSON/TOON-heavy inputs produce much
larger savings and also reduce model work.

This plan recommends making deterministic compression the default product path,
then treating LLMLingua-2 as an opt-in or conservative auto mode. The bias should
be to skip LLMLingua-2 unless the request shape and observed tenant history make
the incremental savings clearly worth the latency, cost, and semantic risk.

## Current State Review

### Deterministic preprocessing already implemented

The current non-LLMLingua path is stronger than simple whitespace trimming:

- `app/whitespace_normalizer.py` trims trailing spaces and tabs, preserves
  Markdown hard-line-break spacing, collapses long blank-line runs, and preserves
  fenced code contents.
- `app/whitespace_normalizer.py` treats full HTML block documents with tags such
  as `html`, `pre`, `code`, `script`, `style`, `template`, and `svg` as
  non-compressible.
- `app/compression_pipeline.py` strips `<nocompress>...</nocompress>` wrappers
  while preserving the wrapped body verbatim.
- `app/compression_pipeline.py` protects Markdown code fences. Valid fenced JSON
  is preserved as JSON rather than TOONified, because fenced JSON is often an
  example, template, or exact syntax fixture.
- `app/compression_pipeline.py` protects UI contracts, follow-on blocks, failure
  sections, and data payload sections with regex-based section spans.
- `app/compression_pipeline.py` detects raw balanced JSON inside prose, parses it
  with `json.loads`, and only TOONifies medium/large JSON when the context does
  not ask for exact JSON and the TOON result clears the configured savings
  threshold.
- `app/compression_pipeline.py` avoids TOON for duplicate-key JSON and known LLM
  tool/function exchange payloads across OpenAI, Anthropic, Google, and xAI style
  structures.
- `app/tenant_profiles.py` supports exact request-supplied
  `force_drop_phrases`, and `app/compressor.py` applies them only to compressible
  segments.
- `app/protected_spans.py` identifies URLs, emails, inline code, money, hard
  constraints, identifiers, numbers, and constants. `app/compressor.py`
  placeholders these spans before LLMLingua calls, then restores them.
- `app/message_compression.py` only compresses user messages and text parts.
  System, assistant, tool, image, and other non-user content is preserved.

### Current LLMLingua-2 gate

Today the model runs when all of these are true:

- The segment is compressible and non-blank.
- The target retention rate is below 1.0, which means aggressiveness is not 0.
- The segment clears `COMPRESSOR_MIN_SEGMENT_CHARS`.
- The segment clears `COMPRESSOR_MIN_SEGMENT_TOKENS`.

That gate is simple and safe, but it does not consider:

- how much was already saved deterministically;
- how many tokens LLMLingua-2 is expected to save incrementally;
- whether the current device is CPU or GPU;
- projected latency from chunk count;
- protected-span density;
- code/schema/tool-call density;
- tenant-specific measured model lift;
- downstream model cost.

## Product Recommendation

Default behavior should be deterministic compression only.

Expose LLMLingua-2 in two explicit ways:

- `mode=deterministic`: default. Run only cheap, explainable transforms.
- `mode=model_auto`: run deterministic first, then run LLMLingua-2 only if the
  conservative ROI gate passes.
- `mode=model_force`: run LLMLingua-2 after deterministic preprocessing unless a
  hard safety rule blocks it.

For `/v1/compress` and `/v1/messages/compress`, keep response compatibility but
add warnings/diagnostics that make skip decisions visible:

```json
{
  "warnings": ["llmlingua_skipped_low_expected_incremental_savings"]
}
```

For the lower-level `/compress` debug endpoint, add detailed diagnostics:

```json
{
  "deterministic_original_tokens": 100000,
  "deterministic_output_tokens": 73500,
  "deterministic_reduction": 0.265,
  "model_gate_decision": "skip",
  "model_gate_reason": "deterministic_savings_sufficient",
  "model_expected_incremental_savings_tokens": 820,
  "model_projected_latency_ms": 3100
}
```

## Deterministic Compression Roadmap

### Phase 1: Measure deterministic savings as a first-class output

Add a deterministic-only compression result before any LLMLingua decision.

Implementation shape:

- Split preprocessing into a named deterministic transform pipeline.
- Record input chars/tokens, output chars/tokens, segment counts, protected
  segment counts, TOON savings, whitespace savings, force-drop savings, and
  skipped model candidate tokens.
- Add `deterministic_reduction` and `deterministic_tokens_saved` to diagnostics.
- Add `compression_path` with values such as `deterministic_only`,
  `deterministic_plus_model`, and `unchanged`.

Why first:

- It lets us prove whether deterministic compression alone is enough.
- It gives the model gate real numbers instead of static segment thresholds.
- It makes product behavior explainable.

### Phase 2: Extend safe whitespace and Markdown normalization

Current whitespace normalization is intentionally conservative. Keep that default,
then add a stricter prose-only normalizer behind a config flag.

Recommended additions:

- Collapse interior runs of 2+ spaces to one space only inside prose paragraphs.
- Do not collapse indentation in code fences, tables, blockquotes, ordered lists,
  unordered lists, definition lists, YAML-like blocks, or ASCII-aligned text.
- Preserve Markdown hard line breaks with two trailing spaces.
- Collapse 3+ blank lines to one blank line for prose-only text. Keep the
  current more conservative behavior for mixed Markdown until tests prove parity.
- Normalize repeated horizontal rules only when they are clearly decorative, not
  section delimiters.
- Remove trailing whitespace before token estimation so savings are reported
  consistently.

Expected impact:

- Low single-digit percent on clean prose.
- 5%+ on copied documents with excessive blank lines or spacing.
- Very low semantic risk if limited to prose paragraphs.

Tests:

- Golden tests for Markdown lists, tables, code fences, hard line breaks,
  blockquotes, and pasted plain text.
- Token-count regression tests with both regex estimator and tokenizer-backed
  estimator when available.

### Phase 3: Add deterministic boilerplate removal

The tenant profile already supports exact `force_drop_phrases`. Build on that
instead of adding broad fuzzy deletion first.

Recommended additions:

- Add built-in optional boilerplate phrase groups:
  `email_signature`, `legal_footer`, `support_ticket_wrapper`,
  `generated_report_wrapper`, and `chat_acknowledgement`.
- Keep built-in drops disabled by default until each group has eval coverage.
- Add tenant-learned boilerplate suggestions from repeated exact spans across
  sampled traffic, but require explicit tenant approval or config before dropping.
- Apply drops only to compressible prose segments.
- Never drop from code, JSON, TOON, HTML, tool calls, schemas, UI contracts, or
  `<nocompress>` spans.

Examples of safe exact-drop candidates:

- repeated support system preambles;
- repeated email confidentiality footers;
- repeated generated report headers;
- repeated "Please review the following context" wrappers;
- repeated assistant acknowledgements in user-supplied chat transcripts.

Expected impact:

- 0% on clean text.
- 5-30% on repetitive enterprise prompts, support tickets, and copied email
  chains.

### Phase 4: Add exact duplicate-block handling

Start with exact duplicate detection, not semantic dedupe.

Recommended behavior:

- Detect repeated paragraphs or repeated line blocks above a token threshold.
- Replace second and later occurrences with a short deterministic reference only
  in `mode=deterministic_lossy` or when a tenant enables it.
- For default `mode=deterministic`, only report duplicate candidates in
  diagnostics without changing text.

Reason for caution:

- Exact duplicates can still be intentional. Some prompts repeat constraints for
  emphasis, and replacing them can weaken instructions.

Safer first target:

- Repeated quoted email headers.
- Repeated stack trace prefixes.
- Repeated log timestamp prefixes.
- Repeated generated wrappers around many records.

### Phase 5: Add repeated literal placeholdering

Current protected-span placeholdering exists to protect text from LLMLingua-2,
not to reduce deterministic output size. Add deterministic placeholdering only
when it is clearly token-positive.

Good candidates:

- long URLs repeated 2+ times;
- long file paths repeated 2+ times;
- UUIDs, hashes, build IDs, tenant IDs, and request IDs repeated 3+ times;
- long stack-frame prefixes repeated many times.

Recommended output:

```text
[A]=https://example.com/really/long/path?with=query
...
See [A] for details. The retry URL is [A].
```

Gate it by token savings:

- Build the candidate placeholder map.
- Estimate output tokens with the map included.
- Apply only if net savings clears a threshold, such as 50 tokens and 5% of the
  affected block.

Safety:

- Do not placeholder in exact JSON/schema/template contexts.
- Do not placeholder if the downstream task is likely to require byte-exact
  reproduction.
- Do not placeholder single occurrences.

### Phase 6: Add JSON fallback minification where TOON is blocked

When TOON is blocked or below threshold, some JSON can still be minified safely.

Recommended gate:

- Only minify parsed JSON when context does not request exact whitespace,
  fixture formatting, byte stability, or duplicate-key preservation.
- Never minify duplicate-key JSON.
- Never minify JSON inside Markdown fences unless explicitly enabled.
- Never minify tool-call or tool-result JSON.
- Apply only if tokenizer-estimated savings clear a threshold.

Expected impact:

- Stronger than prose normalization for pretty-printed JSON.
- Lower semantic risk than LLMLingua-2, but higher formatting risk than current
  verbatim preservation.

### Phase 7: Compact chat/history artifacts deterministically

For `/v1/messages/compress`, add message-aware deterministic compaction before
text-level compression.

Recommended additions:

- Drop empty user messages.
- Drop duplicate user text parts when exact duplicates appear in the same
  request and the tenant enables duplicate compaction.
- Compact repeated pasted chat transcript markers inside a user message.
- Preserve all non-user roles by default.
- Preserve tool calls and tool results byte-stable by default.

This should remain separate from vendor message objects so compatibility stays
clean.

## Conservative LLMLingua-2 Auto-Gate

### Hard skip rules

Skip LLMLingua-2 if any hard rule is true:

- `mode=deterministic`.
- Aggressiveness resolves to 0.
- No compressible prose remains after deterministic preprocessing.
- Device is CPU and `COMPRESSOR_ALLOW_CPU_MODEL_AUTO` is not enabled.
- The request asks for exact/byte-stable output.
- Protected, code, JSON, HTML, schema, or tool-call content dominates the
  remaining candidate text.
- The model is cold and the request has a tight synchronous latency budget.
- Projected model latency exceeds the request latency budget.
- Expected incremental savings are below the configured minimum.
- Placeholder count is likely to force chunk fallback.
- Tenant or route policy disallows model compression.

### Default auto-run thresholds

Initial conservative defaults:

```text
COMPRESSOR_MODEL_AUTO_ENABLED=false
COMPRESSOR_ALLOW_CPU_MODEL_AUTO=false
COMPRESSOR_MIN_MODEL_CANDIDATE_TOKENS=20000
COMPRESSOR_MIN_MODEL_INCREMENTAL_SAVINGS_TOKENS=2000
COMPRESSOR_MIN_MODEL_INCREMENTAL_REDUCTION=0.05
COMPRESSOR_MAX_MODEL_PROJECTED_LATENCY_MS=2500
COMPRESSOR_MAX_PROTECTED_DENSITY=0.20
COMPRESSOR_MAX_STRUCTURED_DENSITY=0.35
COMPRESSOR_SKIP_MODEL_IF_DETERMINISTIC_REDUCTION_GTE=0.12
```

These defaults intentionally skip most requests. They should be tuned from
production traces, not guesses.

### Expected-savings model

Start with a simple deterministic estimate:

```text
candidate_tokens = tokens in compressible prose selected for the model
expected_model_reduction = tenant_observed_model_reduction or global_default
expected_incremental_savings = candidate_tokens * expected_model_reduction
```

Initial global defaults:

```text
pure_prose_default_model_reduction = 0.08
mixed_prose_default_model_reduction = 0.05
high_protected_density_model_reduction = 0.03
```

Then adjust downward:

- subtract 0.02 when protected density exceeds 10%;
- subtract 0.02 when average segment length is small;
- subtract 0.02 when text has many IDs, URLs, paths, or numbers;
- subtract 0.02 when deterministic compression already saved 10%+;
- set to 0 when exactness or tool-call rules fire.

Only use tenant-specific observed reduction after enough samples, such as 50+
successful model runs for that tenant/profile.

### Projected-latency model

Use diagnostics already emitted by the benchmark:

```text
projected_chunks = ceil(model_input_chars / COMPRESSOR_MODEL_CHUNK_CHARS)
projected_latency_ms =
  p50_fixed_overhead_ms +
  projected_chunks * p50_llmlingua_chunk_ms +
  p50_token_estimate_ms
```

Maintain rolling metrics by device and route:

- GPU warm p50/p95 fixed overhead.
- GPU warm p50/p95 per chunk.
- CPU p50/p95 per chunk if CPU auto mode is explicitly enabled.
- Token estimation p50/p95 by tokenizer type.

If metrics are missing, skip. This is the "err no" policy.

### ROI decision

Run LLMLingua-2 only if all are true:

```text
mode == model_auto
model_auto_enabled
hard_skip_rules == false
candidate_tokens >= min_candidate_tokens
expected_incremental_savings_tokens >= min_incremental_savings_tokens
expected_incremental_reduction >= min_incremental_reduction
projected_latency_ms <= max_projected_latency_ms
deterministic_reduction < skip_if_deterministic_reduction_gte
```

Optional cost-aware gate:

```text
expected_downstream_savings_usd =
  expected_incremental_savings_tokens * downstream_input_cost_per_token

run only if:
  expected_downstream_savings_usd >= compression_runtime_cost_usd * 3
```

Do not make cost-aware mode the first implementation unless downstream model
pricing is reliably present.

### Gate diagnostics

Add structured skip reasons:

```text
llmlingua_skipped_mode_deterministic
llmlingua_skipped_aggressiveness_zero
llmlingua_skipped_cpu_auto_disabled
llmlingua_skipped_no_candidate_prose
llmlingua_skipped_low_candidate_tokens
llmlingua_skipped_low_expected_incremental_savings
llmlingua_skipped_high_projected_latency
llmlingua_skipped_high_protected_density
llmlingua_skipped_high_structured_density
llmlingua_skipped_deterministic_savings_sufficient
llmlingua_skipped_missing_latency_baseline
llmlingua_skipped_exact_output_context
```

When LLMLingua-2 runs, record:

- expected incremental savings;
- actual incremental savings over deterministic output;
- projected latency;
- actual latency;
- candidate tokens;
- protected density;
- structured density;
- chunk count;
- tenant/profile id.

This makes it possible to tighten or relax the gate empirically.

## API and Schema Changes

Recommended request additions:

```json
{
  "compression_settings": {
    "mode": "deterministic",
    "aggressiveness": 0.15,
    "latency_budget_ms": 2500
  }
}
```

Keep backward compatibility:

- If `mode` is omitted, use `deterministic` for v1 endpoints.
- Keep the existing `aggressiveness` behavior for `/compress`, but route it
  through `model_auto` only when the feature flag is enabled.
- Add warnings rather than changing response shapes on v1 endpoints.
- Put detailed gate fields behind `include_diagnostics`.

## Test Plan

Unit tests:

- deterministic-only mode never loads or calls LLMLingua-2;
- model-auto skips when deterministic savings clear threshold;
- model-auto skips on CPU by default;
- model-auto skips when projected latency is unknown;
- model-auto skips high protected density text;
- model-auto runs on GPU-like configured service when expected savings and
  latency thresholds pass;
- each skip reason is emitted exactly and consistently;
- deterministic whitespace strict mode preserves Markdown tables, lists, code
  fences, and hard line breaks;
- JSON minify never touches duplicate-key JSON, fenced JSON by default, or tool
  exchange JSON;
- placeholdering applies only when the map-inclusive output saves tokens.

Integration tests:

- `/compress` diagnostics include deterministic and model-gate fields.
- `/v1/compress` and `/v1/messages/compress` include warnings but preserve
  compatible response bodies.
- Existing protected-content eval cases still pass.
- Benchmark runner can compare `deterministic`, `model_auto`, and `model_force`.

Benchmark additions:

- Add deterministic-only columns:
  `deterministic_tokens_saved`, `deterministic_reduction`,
  `model_incremental_tokens_saved`, and `model_incremental_reduction`.
- Add gate decision columns:
  `model_gate_decision`, `model_gate_reason`, `expected_incremental_savings`,
  and `projected_latency_ms`.
- Run three matrices:
  deterministic only, deterministic plus forced LLMLingua-2, and model auto.

## Implementation Order

1. Add compression modes and diagnostics without changing default behavior.
2. Add deterministic-only mode and make it callable in tests.
3. Add deterministic savings measurement.
4. Add the conservative model-auto gate with hard skip reasons.
5. Add benchmark support for deterministic/model-auto/model-force comparison.
6. Switch v1 default to deterministic after compatibility tests.
7. Add strict prose whitespace normalization behind a feature flag.
8. Add exact boilerplate removal groups, disabled by default.
9. Add JSON minify fallback behind a feature flag.
10. Add repeated literal placeholdering behind a feature flag.
11. Tune thresholds from production or benchmark traces.

## Recommended Initial Policy

Ship deterministic compression as the default. Keep LLMLingua-2 available, but
do not run it automatically until model-auto has diagnostics and enough traffic
history to show positive ROI.

The first production policy should be:

```text
Default: deterministic
Model auto: disabled
Model force: available only on GPU routes or internal/debug routes
CPU model auto: disabled
```

After enough traces:

```text
Enable model_auto only for tenants/routes where:
  p50 incremental model savings >= 2000 tokens
  p50 incremental reduction >= 5%
  p95 projected latency is inside SLA
  protected-content regressions are zero in evals
```

This keeps the product honest: deterministic compression is the dependable
cost-saving layer, and LLMLingua-2 is an optional accelerator for the subset of
requests where it earns its latency.
