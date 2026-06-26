# PromptCompression Implementation Plan

## Goal

Build a lightweight prompt-compression service that uses a fast token-classification model at runtime, not an LLM. The service should score text, delete lower-value tokens, preserve important structure, and return compressed prompts through a local API and testing UI.

The first milestone is already in progress: this repo runs the base LLMLingua-2 model behind a FastAPI service with a browser test app. Python has been upgraded to 3.14. Docker exists as a file target, but the Docker image has not been built or validated yet.

## Core Product Shape

The compressor should work like this:

```text
input text
  -> tokenizer / LLMLingua-2 model
  -> per-token keep/drop decision
  -> deterministic aggressiveness control
  -> protected token hints
  -> compressed text + stats + token labels
```

This is intentionally extractive. The model should not summarize, rewrite, or generate new text. It should only remove tokens while preserving original order and wording.

## Current Repo State

Implemented:

- `app/main.py`: FastAPI application with `/`, `/health`, and `/compress`.
- `app/compressor.py`: wrapper around the LLMLingua-2 MeetingBank model.
- `app/protected_spans.py`: basic forced-token hints for punctuation, negations, URLs, emails, numbers, money, inline code, and uppercase IDs.
- `app/schemas.py`: request/response models, including labeled token output.
- `scripts/smoke_test.py`: local API smoke test.
- `tests/`: unit tests for threshold mapping, protected tokens, and API response shape.
- `README.md`: setup, usage, API examples, Docker notes, and Cloud Run shape.
- `Dockerfile`: Python 3.14 container definition, not yet built.
- `.vscode/`: local VS Code tasks, launch config, settings, and extension recommendations.

Current runtime model:

```text
microsoft/llmlingua-2-bert-base-multilingual-cased-meetingbank
```

The service defaults to CPU. It can use CUDA only if a compatible CUDA PyTorch install is available and `COMPRESSOR_DEVICE=cuda` is set.

## Phase 1: Local MVP

Status: mostly complete.

Objective: prove compression works locally without training a model.

Steps:

1. Use Python 3.14 virtual environment.
2. Install dependencies from `requirements-dev.txt`.
3. Start the API:

```powershell
uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

4. Open the testing UI:

```text
http://127.0.0.1:8000/
```

5. Test API docs:

```text
http://127.0.0.1:8000/docs
```

6. Run the smoke test:

```powershell
python scripts\smoke_test.py
```

Expected behavior:

- First compression request may be slow because the Hugging Face model downloads.
- Later requests should reuse the loaded model.
- Response includes compressed text, token counts, reduction percentage, elapsed time, model name, target retention rate, and kept/dropped word labels.

## Phase 2: Harden The Local Service

Objective: make the local MVP predictable enough to evaluate.

Steps:

1. Verify that the current LLMLingua method call is correct for the installed `llmlingua==0.2.2`.
2. Run the full unit test suite:

```powershell
pytest -q
```

3. Run a manual compression test through the browser UI.
4. Try three aggressiveness values: `0.10`, `0.25`, and `0.60`.
5. Record example outputs and check whether critical content is preserved.
6. Expand `protected_spans.py` only where real examples show risky deletions.

Protected content to watch closely:

- Numbers, dates, IDs, and money.
- URLs and email addresses.
- Negations such as `not`, `never`, `without`, and `unless`.
- Required/must/shall constraints.
- Code-like fragments and inline backtick content.
- JSON, XML, SQL, and config text. These should eventually get stricter handling than plain prose.

## Phase 3: Evaluation Dataset

Status: initial local eval suite implemented.

Objective: determine whether compression helps the actual downstream workflow and
catch prompt-safety regressions before changing compression policy.

Implemented:

- `data/eval_cases.json`: curated first-party eval cases for stable system
  policy, user data that can become TOON, exact JSON, tool exchanges,
  code/HTML, and numeric/negation constraints.
- `app/eval_suite.py`: reusable quality checks for required substrings,
  forbidden substrings, expected protected section types, token-growth failures,
  target reduction warnings, and latency warnings.
- `GET /eval`: browser page for running cases and comparing original vs
  compressed prompts.
- `GET /eval/cases`: returns the built-in eval cases.
- `POST /eval/run`: runs all or selected cases with optional aggressiveness
  override. This endpoint is also the shape to reuse for production sampling.

Next objective: determine whether compression helps the actual downstream
workflow, not only whether text-level invariants pass.

Create a small local eval set before training anything custom:

```text
data/eval/
  prompt_001.txt
  prompt_002.txt
  ...
```

Start with 50-100 examples from real usage:

- Long system prompts.
- RAG context chunks.
- Chat history.
- Documentation passages.
- Support or meeting transcript text.
- Agent instructions.

For each example, save:

- Original prompt.
- Compressed prompt at safe/balanced/aggressive settings.
- Token reduction.
- Whether downstream LLM answer quality changed.
- Any critical deletion failures.

Useful MVP metrics:

- Token reduction percentage.
- Compression latency.
- Downstream answer pass/fail.
- Count of dangerous deletions.
- User-visible readability of compressed prompt.
- Cacheability class: stable system instructions, reusable policy blocks,
  request-specific user context, and structured payloads should be measured
  separately because stable cached prompt sections do not provide the same
  downstream cost benefit when compressed.
- Protection mechanism: record whether savings came from LLMLingua deletion,
  TOON conversion, protected placeholders, or skipping stable/protected content.

Do not rely only on semantic similarity. The real test is whether the target LLM still answers correctly.

Near-term eval improvements:

1. Add a production sampler that stores a small percentage of compression
   attempts with input hash, model version, aggressiveness, reduction, latency,
   validation checks, and cacheability class. Avoid storing raw prompts unless
   explicit retention policy allows it.
2. Add downstream task checks for a small golden set: expected answer contains
   required IDs/dates/constraints, exact JSON remains valid, and tool-call
   semantics are unchanged.
3. Split adaptive policy by section class:
   - Stable cached instructions: usually skip or use very light compression.
   - Request-specific prose/RAG context: compress according to budget.
   - Structured user data: prefer TOON when safe.
   - Tool calls, exact schemas, code, HTML, and no-compress blocks: preserve.
4. Promote savings targets only after quality checks are stable; correctness
   failures should block, while reduction and latency misses should warn.

## Phase 4: Docker

Status: pending. The Dockerfile exists but has not been built.

Objective: prove the service runs from a clean container.

Steps:

1. Build the image:

```powershell
docker build -t prompt-compression .
```

2. Run it locally:

```powershell
docker run --rm -p 8080:8080 prompt-compression
```

3. Open:

```text
http://127.0.0.1:8080/
```

4. Run a POST test against port `8080`.
5. Confirm model download works inside the container.
6. Confirm memory use is acceptable.

Risks to verify:

- Python 3.14 compatibility with `torch`, `transformers`, and `llmlingua`.
- Availability of `python:3.14-slim` and binary wheels for all dependencies.
- Cold-start time caused by downloading the model at first request.
- Container memory pressure during model load.

If dependency wheels are not ready for Python 3.14, the pragmatic fallback is to keep local dev on Python 3.14 but use a supported Python version in Docker until the ecosystem catches up.

## Phase 5: Hosting MVP

Objective: deploy the Dockerized API with minimal infrastructure.

Recommended hosting path:

```text
FastAPI
  -> Docker image
  -> Cloud Run
  -> HTTP clients call POST /compress
```

Initial Cloud Run settings:

- CPU: 1-2 vCPU.
- Memory: 1-2 GB.
- Concurrency: start around 10.
- Minimum instances: `0` for cheapest testing, `1` if cold starts are unacceptable.
- Model file: initially downloaded at startup or first request; later baked into the image.

Deployment steps:

1. Build Docker image.
2. Push image to a registry.
3. Deploy to Cloud Run.
4. Set environment variables:

```text
COMPRESSOR_MODEL=microsoft/llmlingua-2-bert-base-multilingual-cased-meetingbank
COMPRESSOR_MIN_RATE=0.45
COMPRESSOR_DEVICE=cpu
```

5. Test `/health`.
6. Test `/compress`.
7. Watch logs for latency, memory, and first-request model loading errors.

## Phase 6: Custom Training Later

Objective: replace or fine-tune the base LLMLingua-2 behavior with domain-specific examples.

Do this after the local and Docker MVPs are stable.

Training data plan:

1. Collect 500-2,000 representative examples.
2. Use an LLM offline to create extractive compressed versions.
3. Reject examples where the compressed output cannot be aligned back to original text.
4. Convert original/compressed pairs into KEEP/DROP token labels.
5. Fine-tune a small token classifier.
6. Evaluate against the Phase 3 eval set.
7. Promote only if it beats or matches the base LLMLingua-2 model on quality and speed.

Potential bootstrap data:

- `microsoft/MeetingBank-LLMCompressed`

Important licensing note: that dataset is listed as non-commercial/share-alike, so treat it as research/bootstrap material unless licensing is reviewed for the intended use.

## Phase 7: Production Optimization

Do these after the product behavior is proven:

1. Export the model to ONNX.
2. Quantize to INT8.
3. Load the model during service startup instead of first request.
4. Bake model artifacts into the Docker image.
5. Add structured logs:

```text
request_id
model
aggressiveness
target_rate
original_tokens
compressed_tokens
reduction
elapsed_ms
error
```

6. Add a regression eval command.
7. Add stricter span preservation for structured text.
8. Add max input size and clear error handling.

## Near-Term Checklist

- [ ] Run `pytest -q` on the current Python 3.14 environment.
- [ ] Start local API and confirm `http://127.0.0.1:8000/` works.
- [ ] Run `python scripts\smoke_test.py`.
- [ ] Test several real prompts and save results.
- [ ] Build Docker image.
- [ ] Run Docker container locally on port `8080`.
- [ ] Decide whether Docker stays on Python 3.14 or temporarily falls back for dependency compatibility.
- [ ] Deploy to Cloud Run only after Docker is verified locally.

## Definition Of Done For This MVP

The first hosted MVP is done when:

- Local UI compresses text reliably.
- `/compress` returns useful stats and labeled tokens.
- Unit tests pass.
- Docker image builds.
- Docker container runs locally.
- Cloud Run deployment responds to `/health` and `/compress`.
- A small eval set shows acceptable token savings without obvious critical deletions.
