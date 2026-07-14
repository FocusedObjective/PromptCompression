# Deterministic compression experiment brief

## Objective

Implement and benchmark deterministic compression experiments that increase
token savings without reducing prompt utility. Attribute every token saved to a
named transform, retain enough diagnostics to explain every skip, and reject or
roll back any output that violates a hard integrity guardrail.

The current three-cohort baseline contains 334 records and 567,133 input tokens.
It saved 65,430 tokens (11.54%): 6,533 deterministically and 58,897 through
LLMLingua-2. All deterministic savings came from 12 JSON-to-TOON applications.
The baseline also contains three model-stage integrity failures affecting 14
inline-code spans and two URLs. Constraint and required-term evaluation coverage
were both zero.

The immediate strategy is therefore:

1. enable and measure already-implemented conservative transforms;
2. make transform application tokenizer-positive and integrity-gated;
3. add only exact, reversible, or tenant-approved new transforms; and
4. add a final rollback guard so model compression cannot make the output less
   safe than the deterministic result.

Do not implement global stop-word deletion, fuzzy boilerplate removal, semantic
deduplication, sentence rewriting, or removal based only on lexical frequency.

## Phase 0: make experiments causal and safe

Before changing compression behavior, make the benchmark runner able to select
an allowlisted experiment profile. Prefer an internal/debug `experiment_profile`
on `/compress`; do not expose arbitrary thresholds on the v1 production API.
Echo the resolved profile and all thresholds into provenance and include the
profile in `condition_id`.

Suggested profiles:

- `baseline`
- `strict_whitespace_token_positive`
- `json_minify_safe`
- `literal_aliases_safe`
- `toon_expanded_safe`
- `html_markdown_expanded_safe`
- `tenant_boilerplate_exact`
- `duplicate_wrapper_aliases`
- `safe_stack_v1`

For every transform, export:

- candidate records, regions, characters, and tokenizer-backed tokens;
- eligible, applied, rolled-back, and skipped counts;
- input/output tokens and actual tokens saved;
- skip and rollback reasons;
- source and output hashes;
- counterfactual output hash and token delta when disabled;
- integrity results before and after the transform; and
- corpus, compressor commit, model revision, configuration, and tenant-profile
  hashes.

Counterfactual estimates must use the same tokenizer as final accounting. Do not
report blank-line counts or candidate characters as token opportunity.

## Hard guardrails shared by every experiment

Create one central exactness/integrity policy and call it from every transform.
A candidate must be rejected when any of these are true:

- the request asks for byte-exact, verbatim, unchanged, fixture, template,
  schema, or exact-format output;
- the candidate is inside `<nocompress>`, a code fence, inline code, a tool call
  or result, a protected UI/contract/failure section, or a known protocol block;
- JSON contains duplicate keys or cannot be parsed losslessly;
- a transform would change the occurrence multiset of protected URLs, emails,
  identifiers, money, dates/numbers, templates, citations, code, or constraints;
- tokenizer-backed net savings do not clear both the transform's absolute and
  relative minimums; or
- a transform-specific structural equivalence check fails.

After the full pipeline, compare the final output with the original and the
post-deterministic output. On any protected-span, placeholder restoration,
required-term, constraint, JSON, code, or structural failure:

- if LLMLingua-2 caused the failure, return the post-deterministic output;
- if a deterministic transform caused it, return that transform's input;
- emit `output_rejected_integrity_<class>` and the rejected-output hash; and
- count the saved tokens only from the accepted output.

This rollback is P0. The three observed integrity failures should become safe
fallbacks rather than corrupted successful outputs.

## Experiment 1: tokenizer-positive strict prose whitespace

### Hypothesis

Strict prose normalization can remove copied interior spacing and excess blank
lines, but the baseline's 12,062 estimated blank-line tokens produced zero
tokenizer-measured savings. Only apply normalization when the real tokenizer
shows a positive result.

### Implementation

The strict normalizer already exists in `app/whitespace_normalizer.py`. Wrap the
candidate in an apply-or-revert gate:

- operate only on plain prose paragraphs;
- preserve Markdown lists, tables, blockquotes, YAML-like rows, aligned text,
  code fences, inline code, HTML, TOON, JSON, and two-space Markdown hard breaks;
- require at least 2 tokens and 0.5% reduction in the affected segment for the
  experiment; and
- revert byte-for-byte when savings are below threshold.

### Example

Input:

````text
Summary   has     copied spacing.



Next point.  
```python
value   =   1
```
````

Candidate output:

````text
Summary has copied spacing.

Next point.  
```python
value   =   1
```
````

The hard break and fenced code must remain exact. If the configured tokenizer
counts both versions equally, return the original.

### Tests

- prose interior runs collapse when tokenizer savings clear the gate;
- the same candidate is reverted when a stub tokenizer reports no savings;
- Markdown tables, lists, quotes, aligned columns, YAML, code fences, and hard
  breaks remain byte-identical;
- protected-span occurrence counts remain equal; and
- deterministic output SHA is stable across three runs.

## Experiment 2: safe JSON minification fallback

### Prerequisite: separate JSON discovery from strict transformation eligibility

The current detector treats every `{` or `[` as a possible JSON start, finds a
balanced region, and then calls `json.loads`. If parsing fails, scanning resumes
after the entire balanced region. This creates two problems visible in the
baseline diagnostics: bracketed Markdown/templates and JSON-like syntax inflate
`json_parse_failed`, while a valid JSON object inside an invalid outer candidate
can be skipped. The current size gate also runs before parsing, so small valid
JSON is not consistently represented in discovery metrics.

Replace the duplicated private scanning logic in `compression_pipeline.py` and
`analytics.py` with a shared `JsonRegionDetector`. Detection must answer “what
kind of structured region is this?” before transformation policy answers “may
we rewrite it?”

Suggested result type:

```python
@dataclass(frozen=True)
class JsonRegion:
    start: int
    end: int
    syntax_class: str
    parsed_value: object | None
    canonical_sha256: str | None
    duplicate_keys: tuple[str, ...]
    context_flags: frozenset[str]
    parse_error: str | None
```

Use these syntax classes at minimum:

- `strict_json_object` and `strict_json_array`;
- `ndjson` for two or more independently valid JSON lines;
- `concatenated_json` for adjacent strict JSON values;
- `jsonc_like` for comments or trailing commas;
- `javascript_object_like` for unquoted keys or single-quoted strings;
- `template_or_bracket_syntax` for `${...}`, `{{...}}`, Markdown links, and
  similar non-JSON constructs; and
- `invalid_balanced` / `invalid_unbalanced` with a stable error category.

Only strict JSON, and NDJSON under a separate explicit policy, should initially
be transformable. JSONC, JavaScript/Python literals, and template syntax should
be classified for diagnostics but preserved. Do not “repair” them with regexes;
comments, duplicate keys, string quoting, `NaN`, and trailing commas can carry
meaning or runtime-specific behavior.

For strict JSON discovery, use `json.JSONDecoder.raw_decode` from plausible
object/array starts instead of making a balanced substring the source of truth.
The scanner should:

1. skip known excluded spans such as code, inline code, templates, protected
   sections, and HTML/script blocks;
2. reject implausible starts early—e.g. a `[` followed by Markdown-link text
   rather than `{`, `[`, `"`, a JSON number, `true`, `false`, `null`, or `]`;
3. attempt `raw_decode` at the plausible start;
4. on failure, record the error and resume at `start + 1`, not the balanced
   candidate end, so later valid regions are still discoverable;
5. on success, enforce token boundaries and record the exact decoder end;
6. detect duplicate keys during the same parse using `object_pairs_hook`;
7. detect all valid regions regardless of size; and
8. apply minimum size and savings gates only during transformation eligibility.

A region discovered inside a failed outer bracket or an ambiguous template may
be reported but must remain ineligible unless its surrounding boundaries are
proven safe. Detection recall must not silently broaden rewrite authority.

Replace the current 300-character backward text window with section-aware
context flags. Exactness, schema, fixture, example, tool-protocol, and output-
format instructions may be farther than 300 characters from the JSON region.

For embedded JSON, store a canonical hash per source region. The existing JSON
round-trip integrity check applies only when the entire prompt parses as JSON;
it does not validate embedded transformed regions. Every transformed embedded
region needs its own typed canonical comparison, protected-literal comparison,
and source/output provenance.

#### Detector tests

- small strict JSON is detected but may be ineligible for transformation;
- braces inside JSON strings and escaped quotes produce the correct end offset;
- a Markdown link, template expression, UI marker, and prose brackets are not
  counted as strict JSON;
- an unmatched or invalid opening bracket before later valid JSON does not hide
  the later region;
- an invalid outer region containing a valid inner object reports both, but the
  inner object remains rewrite-ineligible by default;
- two adjacent JSON objects are classified as concatenated JSON;
- two valid JSON lines are classified as NDJSON and preserve line order;
- JSON with comments, trailing commas, single quotes, or unquoted keys is
  classified but unchanged;
- duplicate-key JSON is detected in one parse and remains unchanged;
- fenced JSON, tool exchanges, schemas, templates, fixtures, and exact-output
  contexts are detected but rewrite-ineligible;
- arrays of objects, arrays of scalars, nested arrays, Unicode, nulls, booleans,
  and numeric-looking strings retain their typed values; and
- analytics and preprocessing consume the same detector output, so candidate
  counts and gate reasons reconcile exactly.

### Hypothesis

The baseline observed 1,614 JSON minification candidates while the transform was
tenant-disabled. Valid JSON that is not suitable for TOON can still be
minified safely and more predictably than model compression.

### Implementation

Enable the existing JSON fallback behind `json_minify_safe` and fix its
counterfactual reporting. Apply only when:

- the balanced region parses as JSON;
- it is not fenced JSON, a schema/template/fixture/example, an exact-output
  request, a tool exchange, or duplicate-key JSON;
- parsing the candidate and output yields deeply equal values, including value
  types and array order;
- all string values and protected literals remain occurrence-equal; and
- tokenizer savings are at least 8 tokens and 5% of the JSON region.

Do not rely on character reduction for the final decision.

### Example

Input:

```json
{
  "ticket": "UT-1042",
  "status": "open",
  "retry_limit": 3
}
```

Output:

```json
{"ticket":"UT-1042","status":"open","retry_limit":3}
```

### Tests

- parsed input and output are deeply equal;
- Unicode strings, escaped characters, booleans, nulls, decimals, nested
  arrays, and key order required by the implementation are covered;
- exact JSON, fenced JSON, tool payloads, duplicate keys, and invalid JSON are
  unchanged with explicit skip reasons;
- low-token-savings JSON is unchanged; and
- JSON-to-TOON continues to take precedence when it passes its own gate.

## Experiment 3: repeated literal aliases

### Hypothesis

Protected-span substitution currently improves model safety but saves no final
tokens. The already-implemented literal map can safely reduce repeated long
URLs and identifiers when the map plus aliases is token-positive.

### Implementation

Benchmark the existing `literal_placeholdering_enabled` path first. Then extend
candidate discovery only if the first run is useful:

- URLs of at least 32 characters repeated at least twice;
- UUIDs, long constants, hashes, build IDs, tenant IDs, request IDs, and long
  file paths repeated at least three times;
- compressible prose only; never JSON, code, schemas, templates, tool payloads,
  or exact-output requests;
- collision-free aliases and a non-compressible legend at the start;
- require at least 16 tokens and 5% reduction for the affected record; and
- verify that deterministic expansion of the alias map recreates the original
  affected text exactly.

### Example

Input:

```text
Fetch https://example.com/really/long/path?alpha=1&beta=2.
Retry https://example.com/really/long/path?alpha=1&beta=2 if needed.
```

Output:

```text
[A]=https://example.com/really/long/path?alpha=1&beta=2
Fetch [A]. Retry [A] if needed.
```

### Tests

- map-inclusive savings must pass both gates;
- alias expansion round-trips exactly;
- an existing `[A]` forces selection of another alias;
- overlapping URL/number spans are counted once;
- JSON values, code, exact-output contexts, and one-off literals are unchanged;
- the legend is excluded from LLMLingua-2; and
- URLs and identifiers remain available to the downstream prompt through the
  legend.

## Experiment 4: expand JSON-to-TOON with token gates

### Hypothesis

JSON-to-TOON is the only proven deterministic contributor: 6,533 tokens from 12
applications, about 544 tokens each. More small homogeneous arrays may be
profitable even when they miss the current 300-character/four-line gate.

### Implementation

Test a small threshold matrix rather than choosing one value immediately:

- minimum JSON characters: 120, 200, 300;
- minimum JSON lines: 2, 3, 4;
- minimum token savings: 16 and 32; and
- minimum tokenizer reduction: 5% and 8%.

Parse and classify before applying. Preserve the existing exact-context,
duplicate-key, tool-exchange, and fenced-JSON exclusions. Add a TOON semantic
round-trip check if the library supports decoding; otherwise compare a typed
canonical representation produced during encoding and add downstream QA cases
that query keys, values, row associations, order, nulls, and types.

### Example

Input:

```json
{"users":[{"id":1,"name":"Alice"},{"id":2,"name":"Bob"}]}
```

Expected TOON shape:

```text
users[2]{id,name}:
  1,Alice
  2,Bob
```

### Tests

- homogeneous arrays retain row-to-value association and order;
- nested objects, nulls, booleans, Unicode, escaped strings, and numeric-looking
  strings retain their types;
- non-homogeneous arrays and ambiguous encodings fall back to minified or exact
  JSON;
- exact-output and tool-exchange cases remain unchanged; and
- each threshold cell reports candidate, apply, savings, and integrity counts.

## Experiment 5: lower the HTML-to-Markdown size gate safely

### Hypothesis

Forty HTML candidates were rejected only for being below the 1,000-character
minimum. Some shorter page-like fragments may still yield safe token savings.

### Implementation

Test minimum sizes of 300, 500, and 1,000 characters. Require at least 16 tokens
and 20% tokenizer reduction. Apply only to page-like HTML with a clear
`main`/`article` content region or an explicitly identified downloaded page.

Before accepting, compare an extracted preservation signature:

- visible main/article text in order;
- heading and list item text;
- link labels and destinations;
- code and `pre` contents byte-for-byte;
- table cell values and order; and
- protected literals and constraint clauses.

Reject forms, templates, SVG, scripts used as data, code-bearing fragments,
unknown custom elements, or pages without a confidently identified content
region. Do not assume `nav`, `aside`, or `footer` is irrelevant unless the
profile explicitly classifies that page chrome as removable.

### Example

Input:

```html
<main><h1>Incident</h1><p>Do not raise retry_limit above 3.</p>
<a href="https://example.com/runbook">Runbook</a></main>
```

Output:

```markdown
# Incident

Do not raise retry_limit above 3.

[Runbook](https://example.com/runbook)
```

### Tests

- links, tables, headings, lists, code/pre, constraints, IDs, and numbers retain
  their order and exact values;
- page chrome is removed only with an explicit content-region classification;
- short fragments without token savings are unchanged;
- custom elements and exact HTML requests are unchanged; and
- the converter never emits empty content.

## Experiment 6: tenant-approved exact boilerplate removal

### Hypothesis

The baseline used `default:base` for both tenants and found no force-drop
phrases. Exact, high-frequency tenant boilerplate can yield more than global
rules while keeping the risk bounded.

### Implementation

Build an offline discovery report; do not automatically activate candidates.
For each tenant, find exact normalized paragraphs or line blocks that:

- appear in at least 50 records and at least 30% of the discovery sample;
- appear across multiple conversations or documents;
- contain no protected spans, negations, obligations, permissions, scope words,
  questions, imperatives, owners, deadlines, amounts, or task-specific fields;
- occur only in compressible prose; and
- save at least 8 tokens per affected record.

Require explicit profile approval and versioning. Validate on a held-out sample.
Use the existing `force_drop_phrases` mechanism only after approval.

### Example

Approved tenant phrase:

```text
This report was generated automatically by Example Support Analytics.
```

The exact phrase may be removed from prose. A similar sentence, a phrase inside
quoted customer content, or a phrase containing a date or instruction must not
be removed.

### Tests

- exact match only; no fuzzy or case-insensitive expansion unless separately
  approved;
- no deletion inside nocompress, JSON, code, HTML data, schemas, or tool payloads;
- protected or instruction-bearing candidates are rejected by discovery;
- held-out token savings and all guardrails are reported by tenant profile; and
- profile rollback restores baseline behavior without a deployment.

## Experiment 7: alias only structurally redundant duplicate wrappers

### Hypothesis

The baseline found 120 exact duplicate candidates covering 7,171 tokens, but all
were correctly blocked as structurally unsafe. A narrow aliasing experiment may
capture generated wrappers without deleting arbitrary repeated paragraphs.

### Implementation

Keep generic duplicate detection measure-only. Add an opt-in transform for
approved structural classes such as repeated generated report headers, quoted
email headers, and repeated log preambles. Preserve one canonical block plus a
map, and replace later occurrences with position-preserving aliases.

Never alias a block containing a constraint, negation, permission/scope term,
question, answer-bearing fact, protected span, code, structured data, or fewer
than the configured minimum tokens. Require exact alias expansion to recreate
the original and at least 32 tokens and 10% record-level savings.

### Example

```text
[D1]=Generated support export. Internal routing metadata follows.

[D1]
Ticket A details...

[D1]
Ticket B details...
```

Do not apply this representation to repeated warnings such as “Never delete the
backup” because repetition may encode emphasis.

### Tests

- only allowlisted wrapper classifiers are eligible;
- expansion round-trips byte-for-byte;
- occurrence positions and counts are preserved;
- instruction-bearing and protected blocks remain unchanged;
- separated duplicates with different surrounding roles are rejected; and
- generic duplicate candidates remain diagnostics-only.

## Experiment 8: critical-clause shielding and model-output rollback

This experiment primarily protects utility, but it enables safer use of the
deterministic gains above.

### Implementation

Expand protection from isolated tokens to complete clauses when a clause
contains one of these combinations:

- negation plus an action;
- obligation/permission/scope term plus an action or condition;
- `unless`, `except`, or `only if`;
- a threshold, amount, date, identifier, or URL plus a governing verb; or
- a required output format or ordering instruction.

Mark the clause non-compressible for LLMLingua-2. Do not alter it
deterministically except through a proven reversible structured transform.
Always run the final integrity comparison and fall back to deterministic output
on failure.

Examples that must remain exact:

```text
Do not delete the contract exception.
The customer may receive a credit only if the outage exceeds 240 minutes.
Keep retry_limit at 3 unless legal approves a written amendment.
```

### Tests

- clause extraction handles punctuation, bullets, Markdown, and sentence
  boundaries without swallowing unrelated paragraphs;
- all existing protected-span tests continue to pass;
- injected model outputs that remove or alter a URL, inline code, number, or
  critical clause are rejected and replaced by deterministic output;
- rejected model tokens are not counted as savings; and
- the three known baseline failure shapes produce zero accepted integrity
  failures.

## Optional messages-route experiment

Benchmark the existing `compact_empty_user_messages` and
`compact_duplicate_user_text_parts` settings separately on message-shaped
corpora. Preserve every non-user message, tool call/result, image, and metadata
field. Drop duplicate user text only when it is byte-identical within the same
request and the setting is explicit. Do not mix these results into the plain
text `/compress` baseline.

## Benchmark matrix

Use the same fixed prompts, order, tenant profile, tokenizer, compressor commit,
model revision, and aggressiveness for every condition. Run at least three
identical repeats.

For each experiment profile, run:

1. `mode=deterministic`, profile disabled (current baseline deterministic path);
2. `mode=deterministic`, profile enabled;
3. `mode=model_force`, `apply_deterministic_transforms=false` (model-only arm);
4. `mode=model_force`, profile enabled (deterministic + model); and
5. after individual attribution, `safe_stack_v1` in deterministic and
   deterministic + model modes.

Do not compare the prior mixed cohorts as a release trend. Re-run the same input
corpus on one clean commit. Preserve the 2026-07-14 baseline files.

### Required primary metrics

- deterministic tokens saved and reduction by transform;
- model incremental tokens saved and reduction;
- total reduction;
- deterministic share of total savings;
- protected-span, placeholder, JSON, structural, constraint, and required-term
  failure rates with applicable denominators;
- output rollback count and reasons;
- exact safety-clause and protected-literal retention by stage;
- downstream QA/task pass rate on critical facts, relationships, negation,
  ordering, answerability, and structured output;
- deterministic and final SHA stability across repeats; and
- p50/p95 latency and milliseconds per 1,000 accepted tokens saved.

### Acceptance criteria

An experiment may enter `safe_stack_v1` only when:

- deterministic output is exactly repeatable;
- accepted outputs have zero protected-span, placeholder, constraint,
  required-term, JSON, code, and structural failures;
- no downstream eval category regresses beyond a preregistered paired margin;
- every applied record clears its absolute and relative tokenizer-savings gate;
- savings remain positive on a held-out corpus, not only the discovery sample;
- per-tenant results are reported separately; and
- all skipped and rolled-back candidates have stable reasons.

Prefer the experiment with slightly lower savings and a simpler proof of
equivalence. Do not offset one integrity failure with many successful records.

## Expected implementation locations

- `app/compression_pipeline.py`: JSON/TOON/HTML gates and structural checks.
- `app/whitespace_normalizer.py`: token-positive strict prose normalization.
- `app/compressor.py`: literal aliases, duplicate-wrapper aliases, critical
  clause shielding, final validation, and rollback.
- `app/protected_spans.py`: critical clause and additional literal detection.
- `app/analytics.py` and `app/schemas.py`: candidate funnels, counterfactuals,
  rollback diagnostics, and experiment provenance.
- `app/benchmark_ui.py`: experiment profile selection and condition IDs.
- `tests/test_whitespace_normalizer.py`, `tests/test_compression_pipeline.py`,
  `tests/test_html_compactor.py`, `tests/test_protected_spans.py`,
  `tests/test_compressor.py`, and `tests/test_main.py`: unit and integration
  coverage.
- `data/eval_cases.json`: paired cases with real constraints and downstream
  answer keys so evaluation coverage is no longer zero.

## Delivery sequence

1. Implement diagnostics, fixed-corpus condition IDs, and final rollback.
2. Run existing-feature experiments: strict whitespace, JSON minify, and literal
   aliases.
3. Run threshold experiments for TOON and HTML.
4. Add tenant boilerplate discovery and held-out evaluation.
5. Only then implement duplicate-wrapper aliases.
6. Build `safe_stack_v1` from experiments that individually pass the acceptance
   criteria and re-run the full paired matrix.
