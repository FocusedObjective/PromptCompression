# Tenant-Adaptive LLMLingua-2 Compression Plan

## Goal

Use the existing LLMLingua-2 compression service as the default model, but make
compression improve per customer over time. Each request should include a
`tenant_id`; the service records token usage and selected training samples for
that tenant. After enough traffic, an offline job builds a tenant-specific
compression profile, evaluates it against the base model, and promotes it only
when it is better enough to justify using it.

The target is pragmatic improvement, not perfect compression. A good first win
is learning which tenant-specific tokens must be preserved and which repeated
boilerplate can be dropped more aggressively than the base model would drop it.

## Plan Maintenance

This document is the running source of truth for tenant-adaptive compression
decisions. When a design decision changes during implementation, update this
plan in the same change or immediately after the discussion.

Current decisions:

- The customer request path should never train models.
- LLMLingua-2 remains the default production baseline for the MVP because it is
  fast, extractive, token-label based, and easy to audit.
- The default hosted architecture is one shared base LLMLingua-2 model plus
  tenant profile rules.
- First tenant-specific implementation uses request-scoped API inputs only:
  `tenant_id`, `tenant_profile`, and optional `X-Tenant-ID`. No local database,
  event sink, or profile lookup is used in the request path.
- Tenant controls are compressor metadata and must not be forwarded inside
  downstream-compatible `compressed_request` payloads.
- Do not create full-size fine-tuned model copies per tenant by default.
- Use tenant LoRA/adapters only for high-volume tenants that justify the added
  hosting complexity.
- Consider grouped adapters by request style before one adapter per tenant.
- Use the light teacher LLM offline to improve labels; do not run it inline for
  normal compression requests.
- Keep full per-tenant checkpoints as an exception path only, not the standard
  multi-tenant design.
- Newer compressor models must beat LLMLingua-2 on this repo's tenant/eval suite
  before replacing it in the request path.
- Research papers, model checkpoints, and implementation repos are tracked in
  the app at `GET /research`.

## Product Behavior

Initial API-only behavior:

```text
request with tenant_id and optional tenant_profile
  -> build request-scoped profile from API body or X-Tenant-ID
  -> compress with base LLMLingua-2 plus request-supplied tenant rules
  -> return compressed request, stats, and profile metadata
```

No raw prompt text, counters, or training samples are stored by the service in
this first pass.

Later behavior:

```text
tenant reaches training threshold
  -> offline job builds base KEEP/DROP labels from tenant requests
  -> light teacher LLM audits mistaken drops and safe drops
  -> deterministic validators reject risky or ambiguous examples
  -> fine-tune/adapt token classifier or generate a stronger tenant profile
  -> evaluate candidate against tenant holdout examples
  -> promote candidate if it improves savings without quality regressions
```

The API request path should never train a model. Training should happen in a
separate script, scheduled job, or admin command so customer latency stays
predictable.

## Implementation Strategy

Start with three adaptation pieces:

1. Tenant profile rules.
2. Teacher-audited training labels.
3. Optional tenant adapter/delta on top of the shared base model.

Tenant profile rules are the lightweight first version and should ship before
model fine-tuning. They can improve behavior with much less operational risk:

- Force-keep tenant-specific identifiers, field names, product names, and domain
  terms.
- Force-keep structured payload keys that frequently appear in that tenant's
  requests.
- Bias repeated boilerplate sections toward deletion.
- Tune default aggressiveness or min retention rate per tenant.

Fine-tuning comes after profiles are working. For hosting many tenants, the
preferred shape is not one full model per tenant. Keep one shared LLMLingua-2
base model loaded, then apply tenant profile rules and, later, small tenant
adapter weights when the traffic justifies it. LLMLingua-2 is a token
classification approach, so training data should be original text plus KEEP/DROP
labels for each model token. Do not trust the base compressor labels directly.
Use a light LLM offline to identify where the base model dropped important words
or kept obvious filler, then keep only examples that pass deterministic checks.

## Base Model Choice

Reviewed on 2026-06-27.

Decision: keep LLMLingua-2 as the default runtime compressor for the MVP. It is
not the newest prompt-compression research, but it is still the best fit for this
product shape because the service needs fast, deterministic, extractive,
tenant-auditable token deletion.

Current runtime baseline:

```text
microsoft/llmlingua-2-bert-base-multilingual-cased-meetingbank
```

Useful nearby baseline to benchmark:

```text
microsoft/llmlingua-2-xlm-roberta-large-meetingbank
```

The BERT-base multilingual checkpoint is smaller and should stay the latency/cost
baseline. The XLM-RoBERTa-large checkpoint is larger and may improve quality, so
it should be a benchmark candidate rather than an immediate default.

Why LLMLingua-2 remains the production baseline:

- It is extractive: output is produced by keeping or dropping original tokens,
  not rewriting customer text.
- It exposes token-level keep/drop labels, which directly supports tenant
  training labels and teacher-audit correction.
- It uses a token-classification model, so tenant adapters and corrected labels
  fit the model objective.
- It is already integrated through `llmlingua.PromptCompressor`.
- It is safer for exact IDs, schemas, code, JSON, tool payloads, and contractual
  wording than an abstractive compressor.

Newer options to track:

- DAC, 2025: dynamic attention-aware task-agnostic prompt compression. This is
  the most relevant extractive-style research candidate to benchmark if an
  implementation is practical.
- SCOPE, 2025: generative chunking-and-summarization compression. Potentially
  better at high compression ratios, but it rewrites text and is heavier.
- Cmprsr, 2025: an abstractive small-LLM compressor trained for compression-rate
  adherence and downstream quality. Interesting as a teacher or benchmark, but
  too heavy and nondeterministic for the first runtime path.
- CompactPrompt, 2025: a broader prompt/data compression pipeline. Useful design
  inspiration for structured data, but not a direct replacement for the current
  token classifier.

Runtime replacement rule:

Do not switch the default base model only because a newer paper exists. Replace
LLMLingua-2 only if the candidate wins on this service's own evals:

```text
quality errors <= LLMLingua-2
tokens saved > LLMLingua-2 by meaningful margin
latency overhead stays below downstream-token savings
structured/protected content remains safe
tenant training labels remain auditable
```

Near-term benchmark plan:

1. Keep BERT-base LLMLingua-2 as the default.
2. Add an eval switch for the XLM-RoBERTa-large LLMLingua-2 checkpoint.
3. Add a local benchmark command that compares base models on:
   - built-in eval cases
   - sampled tenant holdout cases
   - latency
   - token savings
   - teacher-audited mistaken drops
4. Treat generative compressors as offline teachers or benchmark challengers
   until they prove they are safe enough for runtime use.

Primary references:

- LLMLingua-2 paper: https://arxiv.org/abs/2403.12968
- LLMLingua repository: https://github.com/microsoft/LLMLingua
- BERT-base LLMLingua-2 checkpoint:
  https://huggingface.co/microsoft/llmlingua-2-bert-base-multilingual-cased-meetingbank
- XLM-RoBERTa-large LLMLingua-2 checkpoint:
  https://huggingface.co/microsoft/llmlingua-2-xlm-roberta-large-meetingbank
- DAC: https://arxiv.org/abs/2507.11942
- SCOPE: https://arxiv.org/abs/2508.15813
- Cmprsr: https://arxiv.org/abs/2511.12281
- Prompt Compression in the Wild:
  https://arxiv.org/abs/2604.02985

## PCToolkit Assessment

Reviewed on 2026-06-27:

```text
https://github.com/3DAgentWorld/Toolkit-for-Prompt-Compression
https://arxiv.org/abs/2403.17411
```

Decision: use PCToolkit as a benchmark/reference source, not as a production
runtime dependency.

What it provides:

- A unified `PromptCompressor` wrapper over multiple compression methods.
- Built-in compressor adapters for Selective Context, LLMLingua,
  LongLLMLingua, LLMLingua-2, SCRL, and KiS.
- Dataset loaders for Arxiv, ShareGPT, BBC, GSM8K, LongBench, BBH, Gigaword,
  DUC2004, BNC, Broadcast, Google, IconQA, and OK-VQA-style evaluations.
- Metric examples for BLEU, ROUGE, BERTScore, edit/fuzzy similarity, exact/count
  scoring, QA F1, retrieval scoring, and code similarity.
- A useful evaluation framing: compare compressor output on reconstruction,
  summarization, math, QA, few-shot, synthetic, code, boolean, multiple choice,
  and visual QA tasks.

What we should use:

- Steal the benchmark shape, not the runtime code.
- Add PCToolkit-inspired metrics to `scripts/benchmark_compressors.py`.
- Add optional benchmark adapters for:
  - LLMLingua-2 BERT-base, already used here.
  - LLMLingua-2 XLM-RoBERTa-large.
  - Selective Context as a simple extractive baseline.
- Borrow its dataset/task taxonomy when expanding local eval cases.
- Use BERTScore/ROUGE/BLEU only as secondary signals. They should not replace
  exact preservation checks, teacher-audited mistaken drops, or downstream task
  pass/fail checks.

What we should not use directly:

- Do not import PCToolkit into the production API. It brings a broad dependency
  set and multiple model families that are unnecessary for low-latency runtime.
- Do not use its runner directly for production evaluation. The runner contains
  hard-coded API-key placeholders, OpenAI-compatible gateway assumptions, high
  thread counts, and task-specific prompt logic.
- Do not use SCRL or KiS as default runtime compressors. They are useful
  research baselines, but they rewrite/summarize and require extra model
  artifacts.
- Do not replace this repo's protected-span and role-aware message handling with
  PCToolkit's generic wrapper.

Implementation note:

Add a local, narrow benchmark interface instead of adopting PCToolkit wholesale:

```text
CompressorCandidate
  name
  compress(text, target_rate/aggressiveness) -> CompressionResultLike
```

Then implement candidate adapters only where useful:

```text
LocalLLMLingua2BertCandidate
LocalLLMLingua2XlmRCandidate
SelectiveContextCandidate
FutureExternalCandidate
```

The benchmark command should report:

```text
quality failures
mistaken drops from teacher audit
required-span failures
structured/protected-content failures
token savings
latency
break-even estimate
```

## API Changes

Add optional tenant identity to all compression entry points.

For `POST /compress`:

```json
{
  "tenant_id": "tenant_123",
  "tenant_profile": {
    "profile_id": "tenant_123:v1",
    "default_aggressiveness": 0.2,
    "min_rate": 0.6,
    "force_keep_tokens": ["AcctSuite", "tenant_field"],
    "force_drop_phrases": ["Please carefully review the following context"]
  },
  "text": "Prompts are production code. Manage them that way.",
  "aggressiveness": 0.15,
  "include_sections": false
}
```

For `POST /v1/compress` and `POST /v1/messages/compress`, support both:

- `tenant_id` in the JSON body.
- `X-Tenant-ID` header for clients that cannot modify payloads.

Recommended response additions:

```json
{
  "tenant_id": "tenant_123",
  "compression_profile": "tenant_123:v3",
  "compression_profile_source": "api",
  "training_sample_recorded": false
}
```

Use `default` or `anonymous` when no tenant is provided. Do not train anonymous
traffic.

Implemented API-supplied profile fields:

```text
profile_id
default_aggressiveness
min_rate
force_keep_tokens
force_drop_phrases
```

Runtime behavior:

- Merge `force_keep_tokens` into LLMLingua's `force_tokens` list after protected
  placeholders.
- Apply `min_rate` when mapping aggressiveness to target retention rate.
- Remove exact `force_drop_phrases` only from compressible segments, leaving
  no-compress, JSON, code, HTML, and other protected content untouched.
- Return `training_sample_recorded=false` until an explicit external storage
  path exists.

`tenant_id`, `tenant_profile`, and `compression_settings` are excluded from
`compressed_request` in compatible message responses so they are not forwarded to
the downstream model request.

## Persistence Status

The data model below is deferred. The current service intentionally has no local
database. Future token accounting, rollups, raw samples, and profile promotion
should be implemented through an explicit external storage/event sink chosen by
deployment, not an implicit local SQLite database.

## Future Data Model

When persistence is added, keep the data model small and append-only. Do not
add a local database to the API service by default. For hosted deployments, use
an explicit external store such as Cloud SQL, Firestore, or append-only JSONL in
Cloud Storage, hidden behind a small event sink interface so storage can change
later.

### `compression_events`

One row per compression request.

```text
id
created_at
tenant_id
endpoint
request_hash
model_name
model_version
profile_id
aggressiveness
target_rate
input_tokens
output_tokens
tokens_saved
reduction
elapsed_ms
user_input_tokens
user_output_tokens
non_user_tokens_preserved
sample_recorded
error
```

Do not put raw prompt text in this table.

### `tenant_token_rollups`

Aggregated counters by tenant and day or hour.

```text
tenant_id
bucket_start
request_count
input_tokens
output_tokens
tokens_saved
sampled_tokens
sampled_request_count
error_count
```

This drives billing, dashboards, and training eligibility.

### `training_samples`

Only store raw text for tenants that explicitly allow training data retention.

```text
id
created_at
tenant_id
request_hash
source_endpoint
source_role
source_part_index
raw_text_encrypted
compressed_text
base_labeled_tokens_json
base_model_name
base_profile_id
aggressiveness
input_tokens
output_tokens
audit_status
label_quality_score
corrected_labels_uri
teacher_model
deterministic_checks_json
metadata_json
expires_at
```

For `/v1/messages/compress`, store only compressible user text parts by default.
System, developer, assistant, and tool messages should stay out of the training
sample table unless there is an explicit reason and customer permission.

### `training_label_audits`

One row per offline label-audit attempt. This keeps the prompt sample table
small and lets failed audits be inspected without polluting the training set.

```text
id
created_at
tenant_id
training_sample_id
teacher_model
base_model_name
audit_status              # accepted, rejected, uncertain, failed
compression_quality       # pass, fail, uncertain
mistaken_drops_json
safe_drop_phrases_json
critical_keep_spans_json
corrected_labels_uri
rejection_reasons_json
input_tokens
output_tokens
audit_cost_estimate
```

Only `accepted` audits should feed model training. `uncertain` and `failed`
audits are still useful for improving prompts and deterministic validators, but
they should not become labels.

### `tenant_profiles`

Current and historical adaptation artifacts.

```text
id
tenant_id
version
status                  # candidate, active, disabled, failed
profile_type            # rules, adapter, grouped_adapter, full_checkpoint
base_model_name
artifact_uri
adapter_group
force_keep_tokens_json
force_drop_phrases_json
default_aggressiveness
min_rate
metrics_json
created_at
promoted_at
```

## Sampling Policy

Record metrics for every request. Sample raw training text conservatively.

Suggested MVP defaults:

- Raw sample rate: 5-10% of eligible requests.
- Always sample requests over a useful size, such as 500 estimated tokens.
- Never sample if `tenant_id` is missing.
- Never sample tenants without `training_enabled=true`.
- Skip empty text, tiny text, exact JSON/schema/tool-call blocks, and protected
  no-compress blocks.
- Encrypt raw samples at rest and attach a retention window.

Training eligibility:

```text
sampled_request_count >= 50
or sampled_tokens >= 1,000,000
```

For better model fine-tuning, prefer:

```text
sampled_request_count >= 200
and sampled_tokens >= 2,000,000
```

The first threshold can build a rules-only tenant profile. The second threshold
can attempt fine-tuning.

## Label Generation

The main problem is that normal customer requests do not include ground-truth
compressed text. Use pseudo-labels, but filter them hard. The training set gets
better by correcting the base compressor, not by blindly copying it.

Recommended pipeline:

1. Split sampled requests into train and holdout sets by request hash.
2. Run the base compressor with `include_sections=true` at several retention
   rates, such as safe, balanced, and aggressive.
3. Save the base compressed text and base token labels.
4. Run deterministic extraction on the original text:
   - IDs and ID-like tokens.
   - JSON keys and field names.
   - Product names and proper nouns that recur.
   - Domain-specific terms that appear near instructions or constraints.
   - Numbers, dates, money, URLs, emails, and negations.
   - Required output formats, schema names, and exact field names.
5. Mine repeated boilerplate:
   - Repeated headings and wrapper phrases.
   - Stable policy text already preserved elsewhere.
   - Long repeated intros that rarely contain tenant-specific entities.
6. Ask a light teacher LLM to audit the base result. The teacher should identify
   mistaken drops, safe drops, critical keep spans, and uncertain cases.
7. Correct the base KEEP/DROP labels:
   - Flip mistaken drops to KEEP.
   - Keep deterministic protected spans as KEEP.
   - Flip safe boilerplate phrases to DROP only when exact span alignment is
     clean.
   - Leave uncertain spans unchanged or reject the sample.
8. Convert the corrected compressed text back into token KEEP/DROP labels by
   aligning it to the original input.
9. Reject training examples when:
   - Alignment is ambiguous.
   - Required entities disappeared.
   - Structured content becomes invalid.
   - Output has fewer than a minimum useful token count.
   - Compression saves almost nothing.
   - The teacher returns `uncertain`.
   - The teacher references text that is not an exact span from the original.
   - Deterministic checks disagree with the teacher.

## Teacher LLM Audit

The teacher LLM should be a small, cheap model used offline. It should not run in
the customer request path, and it should not rewrite or summarize prompts. Its
job is to audit an extractive compression result.

Inputs to the teacher:

```text
tenant_id
original_text
base_compressed_text
base_kept_tokens
base_dropped_tokens
deterministic_required_spans
deterministic_boilerplate_candidates
```

Teacher output contract:

```json
{
  "compression_quality": "pass",
  "critical_keep_spans": [
    {
      "text": "retry_count above 3",
      "reason": "numeric safety constraint"
    }
  ],
  "mistaken_drops": [
    {
      "text": "not",
      "reason": "negation changed meaning"
    }
  ],
  "safe_drop_phrases": [
    {
      "text": "Please carefully review the following context",
      "reason": "generic wrapper"
    }
  ],
  "uncertain_spans": [],
  "rejection_reasons": []
}
```

Allowed `compression_quality` values:

```text
pass
fail
uncertain
```

Hard rules for the teacher prompt:

- Return only JSON.
- Only reference exact spans from `original_text`.
- Do not create replacement wording.
- Do not summarize.
- Prefer `uncertain` when the span cannot be judged safely.
- Treat negations, numbers, dates, IDs, URLs, emails, money, schema keys, and
  explicit output requirements as high-risk.
- Treat repeated greetings, wrapper phrases, redundant preambles, and duplicated
  descriptions as possible safe drops.

The audit script should validate the teacher output before using it:

- Every referenced span must exist in the original text.
- Mistaken drops must overlap a token the base model dropped.
- Safe drops must overlap tokens the base model kept, or be explicitly selected
  for a more aggressive corrected label.
- Critical keep spans must survive in the corrected compressed text.
- JSON/code/protected blocks must remain valid or unchanged according to the
  existing preprocessor rules.

## Corrected Label Builder

Create a deterministic label builder after the teacher audit.

Inputs:

```text
original text
base LLMLingua-2 token labels
protected spans
teacher mistaken drops
teacher safe drops
tenant force-keep terms
tenant boilerplate terms
```

Output:

```text
token text
base_label
corrected_label
correction_reason
confidence
```

Correction priority:

1. Protected spans always become KEEP.
2. Teacher mistaken drops become KEEP.
3. Tenant force-keep tokens become KEEP.
4. Teacher safe drops become DROP.
5. Tenant boilerplate drops become DROP.
6. Everything else keeps the base LLMLingua-2 label.

Reject the sample if rules conflict on an important span, for example a date or
negation appears in both safe-drop and force-keep lists. This is the main quality
control that keeps the training set from becoming noisy.

Use the corrected labels for two outputs:

- Tenant profile rules, especially force-keep tokens and safe boilerplate drops.
- Fine-tuning examples for a tenant-specific classifier.

## Tenant Profile Rules

Implement profile rules before model training.

Add a profile object used by `PromptCompressionService`:

```text
TenantCompressionProfile
  tenant_id
  profile_id
  default_aggressiveness
  min_rate
  force_keep_tokens
  force_drop_phrases
  model_name_override
```

Runtime behavior:

- Merge tenant `force_keep_tokens` into the existing `force_tokens` list.
- Apply tenant `min_rate` when mapping aggressiveness to target rate.
- Optionally remove exact boilerplate phrases before LLMLingua-2 if they are
  known safe to drop.
- Include `profile_id` in logs and responses.

This alone should improve customer-specific compression because LLMLingua-2 will
stop dropping important local terms and the service can be more aggressive with
known low-value text.

## Fine-Tuning And Adapter Plan

Once rules are stable, add `scripts/train_tenant_model.py`.

Inputs:

- `tenant_id`
- active base model name
- accepted corrected-label training examples
- holdout examples
- output artifact directory

Training shape:

1. Load the current LLMLingua-2 token classification model and tokenizer.
2. Load only `accepted` corrected-label examples from the audit pipeline.
3. Train a small adapter or LoRA-style delta when supported by the model stack.
4. Save adapter artifacts under a tenant/version or group/version directory.
5. Evaluate candidate model against the holdout set.
6. Write a `tenant_profiles` candidate row with metrics and artifact URI.

Keep training small:

- Cap samples per run, such as 2,000-10,000 text segments.
- Prefer fewer high-confidence labels over many noisy labels.
- Cap max sequence length.
- Prefer tenant profile rules before adapters.
- Prefer adapters over full model checkpoints.
- Prefer grouped adapters by request style when tenant-specific data is sparse.
- Create a full tenant checkpoint only as an explicit exception for a large
  tenant where adapter quality is not good enough.

Useful adapter group examples:

```text
support_ticket_context
legal_contract_review
rag_document_context
code_and_debug_logs
sales_call_transcripts
structured_financial_data
```

## How LoRA Adapters Work

LoRA means Low-Rank Adaptation. Instead of copying and retraining every weight in
the base LLMLingua-2 token classifier, freeze the base model and train small
extra matrices attached to selected transformer layers.

Conceptually:

```text
base layer output = W x
LoRA layer output = W x + scale * B(Ax)
```

Where:

- `W` is the frozen base model weight matrix.
- `A` and `B` are small trainable low-rank matrices.
- `rank` controls adapter size and capacity.
- `scale` is usually `lora_alpha / rank`.

For tenant adaptation, the training job learns only the small `A` and `B`
matrices from accepted corrected KEEP/DROP labels. The base model stays
unchanged. At runtime, the API loads the shared base model once, then applies the
tenant or grouped adapter when available.

Recommended first LoRA target:

```text
base LLMLingua-2 token classifier
  -> freeze base weights
  -> attach LoRA to attention projection layers
  -> train on accepted corrected labels
  -> save adapter artifact only
```

Practical caveat:

The current runtime uses `llmlingua.PromptCompressor`, which hides some model
loading details. Adapter support may require either:

- Loading the underlying Hugging Face token-classification model directly with
  `transformers` plus `peft`.
- Extending the compressor wrapper so it can load an adapter.
- Exporting/merging an adapter into a candidate checkpoint only for evaluation
  or exceptional dedicated deployments.

The MVP should prove rules-only tenant profiles first, then test whether LoRA can
be integrated cleanly with the current LLMLingua-2 wrapper.

## Hosting Model Artifacts

The multi-tenant hosting target is:

```text
one shared base LLMLingua-2 model
  + tenant profile rules
  + optional small adapter/delta
```

Avoid this as the default:

```text
tenant_1 full model checkpoint
tenant_2 full model checkpoint
tenant_3 full model checkpoint
```

Full per-tenant checkpoints multiply memory, disk, cold-start, and deployment
costs. They also make rollbacks and concurrent traffic routing harder. Use them
only when a tenant is large enough to justify dedicated capacity.

Runtime routing:

1. Load the shared base model once per worker.
2. Load tenant profile rules from a lightweight store.
3. If the active profile has an adapter artifact, load it through a bounded LRU
   cache.
4. If the adapter is unavailable or memory pressure is high, fall back to base
   model plus tenant rules.
5. Include `profile_id`, `profile_type`, and `artifact_uri` or adapter version
   in logs for debuggability.

Concurrency rule:

Do not mutate one shared model's active adapter in the middle of parallel
requests unless the model library supports concurrency-safe adapter selection.
If adapter switching is not safe, use one of these safer patterns:

- Route adapter tenants to dedicated workers.
- Keep separate model instances only for a small number of hot adapters.
- Use a request lock around adapter switching, accepting lower throughput.
- Use rules-only profiles for low-volume tenants.

Suggested cache policy:

```text
rules-only profiles: cache hundreds or thousands
adapter artifacts: cache 1-3 per worker at first
full checkpoints: no shared multi-tenant cache; dedicate capacity
```

## Evaluation And Promotion

Each candidate profile must beat the current active profile before promotion.

Minimum gates:

- No failed preservation checks on holdout examples.
- Output is never larger than input.
- Required entities are preserved at least as well as the active profile.
- Token savings improve by a minimum margin, such as 3-5%, or quality improves at
  equal savings.
- Latency stays within an acceptable range.

Suggested holdout checks:

- Exact preservation of IDs, dates, URLs, emails, numbers, money, and negations.
- JSON remains valid when it should remain JSON.
- Code, HTML, schemas, tool calls, and no-compress spans remain protected.
- Existing `data/eval_cases.json` still passes.
- Tenant-specific sampled holdout cases pass generated entity-preservation
  checks.
- Teacher audit on holdout examples does not find new mistaken drops.
- Candidate model does not increase the count of high-risk dropped spans.

Promotion flow:

```text
candidate profile created
  -> run base vs candidate eval
  -> write metrics_json
  -> promote to active if gates pass
  -> keep previous active profile for rollback
```

Add a manual override so a bad profile can be disabled quickly.

## Files To Touch

Likely first implementation pass:

- `app/schemas.py`
  - Add optional `tenant_id` and `tenant_profile` fields.
  - Add response fields for profile metadata.
- `app/main.py`
  - Read `tenant_id` from body or `X-Tenant-ID`.
  - Pass tenant context into compression.
  - Exclude compressor-only tenant controls from compatible downstream payloads.
- `app/compressor.py`
  - Accept an optional tenant profile.
  - Merge tenant force-keep tokens with existing forced tokens.
  - Apply request-supplied `min_rate`.
  - Drop exact request-supplied boilerplate phrases only in compressible text.
  - Include profile metadata in `CompressionResult`.
- `app/message_compression.py`
  - Preserve tenant context through role-aware compression.
  - Use the same request-scoped profile for user text parts.
- New `app/tenant_profiles.py`
  - Normalize request-supplied tenant profile values.
  - Provide fallback default profile.
- Future `app/telemetry.py`
  - Define an external `CompressionEventSink` after the storage backend is
    chosen.
- New `app/training_labels.py`
  - Validate teacher audit JSON.
  - Align exact spans to tokenizer tokens.
  - Build corrected KEEP/DROP labels.
  - Reject ambiguous or conflicting samples.
- New `scripts/build_tenant_profile.py`
  - Mine force-keep tokens and boilerplate drop hints.
- New `scripts/audit_training_samples.py`
  - Run base compression on sampled text.
  - Call the light teacher LLM offline.
  - Persist accepted/rejected audit results.
- New `scripts/benchmark_compressors.py`
  - Compare LLMLingua-2 BERT-base, LLMLingua-2 XLM-RoBERTa-large, and future
    compressor candidates against the same eval/holdout set.
  - Report quality failures, mistaken drops, token savings, latency, and
    break-even thresholds.
- New `scripts/train_tenant_model.py`
  - Fine-tune/adapt model later.
- `app/research_ui.py`
  - In-app bibliography for compression research, model checkpoints, and
    implementation repositories.
- New tests
  - Tenant ID accepted by endpoints.
  - Missing tenant falls back to default and does not train.
  - Token events are recorded.
  - Tenant force-keep tokens are passed into compression.
  - Teacher audit output must reference exact original spans.
  - Corrected labels keep mistaken drops and drop safe boilerplate.
  - Conflicting corrections reject the sample.
  - Candidate profile promotion requires eval gates.

## Rollout Phases

### Phase 1: API-Supplied Tenant Rules

Add `tenant_id`, request-scoped `tenant_profile`, and response profile metadata.
No local persistence, raw prompt storage, event logging, or rollups yet.

Done when:

- Every compression endpoint accepts tenant identity from the API.
- The compressor applies request-supplied force-keep tokens, exact boilerplate
  drops, default aggressiveness, and min retention rate.
- Responses include model/profile metadata.
- Missing tenant IDs do not break existing clients.

### Phase 2: Tenant Token Accounting

Add external event logging and rollups after a storage backend is chosen.

Done when:

- Every endpoint can associate token counts with a tenant.
- Metrics include model/profile version.
- No raw prompt text is stored in token accounting events.

### Phase 3: Training Sample Collection

Add opt-in raw sample storage for user text parts.

Done when:

- Sampling is configurable per tenant.
- Raw text is encrypted or stored only in a trusted local/dev store.
- Samples have TTL metadata.
- Training eligibility can be computed from rollups.

### Phase 4: Teacher-Audited Labels

Add the offline label-quality loop.

Done when:

- Base LLMLingua-2 labels are stored for sampled text.
- Deterministic required spans are extracted.
- A light teacher LLM identifies mistaken drops and safe drops.
- Teacher output is validated against exact original spans.
- Corrected labels are generated only for accepted samples.
- Uncertain or conflicting samples are rejected.

### Phase 5: Rules-Only Tenant Profiles

Build lightweight profiles from sampled data.

Done when:

- A script can create force-keep tokens and boilerplate drop hints.
- Profile rules are based only on accepted audits and deterministic rollups.
- Runtime compression uses the active tenant profile.
- Eval shows improvement for at least one tenant sample set.

### Phase 6: Tenant Adapters

Train a small adapter or grouped adapter for tenants with enough accepted data.

Done when:

- Training is offline and repeatable.
- Training uses accepted corrected labels, not raw base labels.
- Candidate artifacts are versioned.
- Candidate profile promotion is gated by eval.
- Runtime can route a tenant to active adapter artifacts.
- Adapter fallback to base model plus tenant rules is reliable.

### Phase 7: Production Controls

Add safety and operations controls.

Done when:

- Profiles can be disabled or rolled back.
- Model cache has a memory limit.
- Logs expose tenant, profile, savings, and latency.
- Raw sample retention can be audited.

## Open Decisions

- Storage backend for hosted deployment: choose an explicit external store such
  as Cloud SQL, Firestore, or Cloud Storage JSONL; do not make the API depend on
  an implicit local database.
- Whether tenant adaptation is allowed by default or requires explicit customer
  opt-in.
- Which light teacher LLM is acceptable from a privacy, cost, latency, and data
  residency standpoint.
- Whether teacher audit should run on all eligible samples or only the most
  valuable/high-risk samples.
- Which adapter mechanism is practical with the current LLMLingua-2 model stack.
- Whether grouped adapters beat tenant-specific adapters for sparse tenants.
- How many adapter artifacts can be loaded per API instance before memory
  becomes a problem.

## Recommended MVP

Build this in the following order:

1. Add `tenant_id`, `tenant_profile`, and API-supplied tenant rules with no
   local database.
2. Add external per-tenant token accounting after choosing the storage/event
   backend.
3. Add opt-in sample collection for user text only.
4. Store base LLMLingua-2 token labels for sampled text.
5. Add deterministic required-span extraction.
6. Add the base-compressor benchmark command, starting with LLMLingua-2
   BERT-base vs LLMLingua-2 XLM-RoBERTa-large.
7. Add the offline light-LLM audit for mistaken drops and safe drops.
8. Build corrected KEEP/DROP labels and reject uncertain examples.
9. Build rules-only tenant profiles from 50+ accepted requests or 1M sampled
   tokens.
10. Route compression through active tenant profile rules.
11. Add promotion checks against tenant holdout samples.
12. Train adapters only after rules prove the data collection and eval loop are
   useful.
13. Reserve full per-tenant checkpoints for exceptional high-volume tenants.

This gets useful tenant-specific compression without making the request path
heavy or turning model training into a production dependency.
