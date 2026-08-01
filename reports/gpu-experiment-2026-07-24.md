# GPU Runtime Benchmark Report — 2026-07-24

## Executive Summary

The measured candidate is PyTorch FP16 on the existing 4 CPU / 16 GiB NVIDIA
L4 host. It reduced direct service p50 latency by 15–21% across the tested
4k–64k prompt sizes, completed 100/100 measured production-path requests, and
used about 0.65 GiB of GPU memory. Do not use the 8 CPU host or the current ONNX
CUDA implementation; neither improved latency.

The user subsequently approved production promotion. The 4k FP16 output
consistently removed three more ordinary prose tokens than FP32, while all
8k–64k outputs in the comparison were exact matches. Downstream quality
monitoring remains important after promotion.

The result is strong enough to nominate FP16, but it is not a perfectly isolated
dtype estimate. The current production baseline used an older image; the FP16
candidate used the new image and enabled the outer `torch.inference_mode()`.
Therefore the reported 15–21% is the improvement of that candidate bundle over
current production. The reproduction procedure later in this report corrects
this by building one image and testing same-image FP32 and FP16 controls.

Production traffic was not changed during the experiment. After review and
cleanup, a newly built FP16 image was validated on a zero-traffic revision and
promoted as `prompt-compression-fp16-production`; it now receives 100% of
traffic.

## Questions Tested

The experiment was designed to answer:

1. Is the L4 being used, and is host CPU the latency bottleneck?
2. Does FP16 reduce LLMLingua-2 latency without unacceptable output drift?
3. Does increasing the host from 4 CPU / 16 GiB to 8 CPU / 32 GiB help?
4. Is the first ONNX CUDA implementation faster than PyTorch?
5. How much overhead is introduced by benchmark diagnostics?

This experiment did not compare Python with Node. LLMLingua-2 inference runs in
PyTorch/CUDA; replacing the HTTP layer with Node would not replace tokenization,
model execution, reconstruction, or integrity work. The measured low CPU usage
also gives no evidence that the Python web layer is the primary bottleneck.

## Test Boundary And Controlled Workload

- Direct `/compress`; Cloudflare edge was not involved.
- Pure prose at 4k, 8k, 16k, 32k, and 64k synthetic tokens.
- JSON ratio `0`; HTML ratio `0`; protected-prose ratio `0`.
- `model_force`, aggressiveness `0.25`, deterministic transforms enabled.
- Concurrency `1`, fixed seed `1729`, warmups before measured requests.
- Production latency mode: diagnostics and sections disabled.
- Same LLMLingua-2 multilingual BERT checkpoint and L4 GPU.
- Model auto-gating was bypassed intentionally so every measured request tested
  model execution rather than the deterministic skip policy.

Each condition used the benchmark generator in
`scripts/benchmark_performance.py`. The fixed seed and cohort hash make the
synthetic prompt for a given target size repeatable. The five production-path
sizes shared cohort `cohort-5554eadddd152eb9`; the three-size ONNX smoke run
used `cohort-17773501615194d6`.

The latency metric in the main tables is `server_elapsed_ms`, measured inside
the service. `client_wall_ms` additionally includes network and client overhead.
Reported p50 values are medians of successful measured requests after warmup.
Warmup requests are excluded. Errors are counted separately and never converted
to successful latency samples.

Two benchmark modes were kept separate:

- `diagnostics=off`: representative production request timing. No phase
  instrumentation or sections payload.
- `diagnostics=basic`: phase attribution, including LLMLingua timing. This mode
  is deliberately not used for the headline service latency.

## Systems Under Test

| Variant | Revision | Image digest | Host | Runtime settings |
|---|---|---|---|---|
| Current baseline | `prompt-compression-00009-jup` | `sha256:da07dabe07b3e65e20f1e6dcc040d25413567dbc33afbc90115d9634789532f5` | 4 CPU, 16 GiB, L4 | effective FP32, existing production runtime |
| FP32 larger host | `prompt-compression-perf-fp32-8` | `sha256:b43880ea2269e589498371c47a82e36ae5fd9c913f9aca0bcf7302f709f32ca2` | 8 CPU, 32 GiB, L4 | Torch FP32, outer inference mode off |
| FP16 larger host | `prompt-compression-perf-fp16-8` | `sha256:b43880ea2269e589498371c47a82e36ae5fd9c913f9aca0bcf7302f709f32ca2` | 8 CPU, 32 GiB, L4 | Torch FP16, outer inference mode on |
| FP16 existing host | `prompt-compression-perf-fp16-4` | `sha256:b43880ea2269e589498371c47a82e36ae5fd9c913f9aca0bcf7302f709f32ca2` | 4 CPU, 16 GiB, L4 | Torch FP16, outer inference mode on |
| ONNX FP16 | `prompt-compression-perf-onnx-fp16-4` | `sha256:6aa708dff72460fcc63433c2be22f0a9d6047c80df17bd2e221f6a2d4c982d9f` | 4 CPU, 16 GiB, L4 | ONNX Runtime CUDA, FP16 |

All experiment revisions had one L4, CPU always allocated, concurrency `1`,
maximum instances `1`, minimum instances `0`, and no production traffic.
Tagged revision URLs were called directly. The tags were routes, not separate
Cloud Run services; the public service remained named `prompt-compression`.

The common service environment was:

```text
COMPRESSOR_DEVICE=cuda
COMPRESSOR_MIN_RATE=0.45
COMPRESSOR_PRELOAD_SLOTS=base
COMPRESSOR_GPU_P50_FIXED_OVERHEAD_MS=150
COMPRESSOR_GPU_P50_LLMLINGUA_CHUNK_MS=120
COMPRESSOR_GPU_P50_TOKEN_ESTIMATE_MS=80
```

The dtype/runtime variants changed only:

```text
COMPRESSOR_MODEL_DTYPE=float32|float16
COMPRESSOR_MODEL_RUNTIME=torch|onnx
COMPRESSOR_TORCH_INFERENCE_MODE=false|true
```

The deployed runtime is PyTorch `2.4.0+cu124` with CUDA `12.4`. This differs
from the Torch version listed in `requirements.txt` because `Dockerfile.gpu`
deliberately filters Torch from pip installation and inherits it from the
Deep Learning Container.

Runtime identity was verified through `/health`: CUDA was available, the device
was NVIDIA L4, and the expected runtime/dtype loaded. Torch reported about
0.661 GiB allocated for FP32 and 0.338 GiB for FP16 immediately after preload.

## Production-Path Latency

Server p50, milliseconds:

| Tokens | Current FP32 4 CPU | PyTorch FP16 4 CPU | Saved | Improvement |
|---:|---:|---:|---:|---:|
| 4,000 | 386.9 | 327.8 | 59.1 | 15.3% |
| 8,000 | 644.4 | 537.2 | 107.2 | 16.6% |
| 16,000 | 1,183.0 | 957.8 | 225.2 | 19.0% |
| 32,000 | 2,316.6 | 1,841.7 | 474.9 | 20.5% |
| 64,000 | 4,793.0 | 3,931.2 | 861.9 | 18.0% |

The 4 CPU FP16 candidate completed 100/100 measured requests without errors.
The separate five-minute 8 CPU FP16 validation completed 150/150 requests.

Increasing FP32 from 4 CPU / 16 GiB to 8 CPU / 32 GiB did not improve the
production path. Its p50 was 7–18% slower in that sustained run, while the
smaller phase run showed only noise-level changes. The additional host
allocation is not justified.

## Phase Profile

The comparable basic-diagnostics run showed that FP16 primarily improved the
LLMLingua portion:

| Tokens | FP32 LLMLingua | FP16 LLMLingua | Model improvement |
|---:|---:|---:|---:|
| 4,000 | 186.6 ms | 122.3 ms | 34.5% |
| 16,000 | 716.2 ms | 483.5 ms | 32.5% |
| 64,000 | 2,894.1 ms | 1,977.6 ms | 31.7% |

Diagnostics materially inflate the benchmark. At 64k, the current production
path measured 4,793 ms, while the basic-diagnostics request measured 6,275 ms.
Production latency and diagnostic profiling must remain separate modes.

## Instance Utilization

Cloud Monitoring samples from sustained runs:

| Metric | Current FP32 4 CPU | FP16 4 CPU | FP16 8 CPU long |
|---|---:|---:|---:|
| Mean CPU allocation | 13.0% | 14.2% | 8.1% |
| CPU p50 | 13.0% | 20.5% | 11.2% |
| Mean GPU utilization* | 22.5% | 13.0% | 12.5% |
| GPU memory | 1.03 GiB | 0.65 GiB | 0.65 GiB |
| GPU memory utilization | 4.58% | 2.78% | 2.87% |
| Max request concurrency | 1 | 1 | 1 |

`*` Cloud Run published fewer GPU-utilization points than CPU/memory points;
the GPU values are directional. The five-minute FP16 run produced six
CPU/memory samples but only two GPU-utilization samples.

The L4 is underutilized. The workload used less than one CPU core on average,
about 0.65 GiB of 24 GiB VRAM, and low minute-average GPU utilization. The next
throughput experiment should use real model batching and concurrency rather
than a larger host.

## Output And Integrity Parity

- FP32 on 8 CPU matched current FP32 exactly for all 60 compared outputs.
- PyTorch FP16 matched current FP32 for every 8k–64k output.
- The repeated 4k prompt differed consistently: FP16 removed three additional
  ordinary prose tokens. Reduction changed from `8.7427%` to `8.7995%`.
- All phase runs had zero errors, fallbacks, and integrity rollbacks.
- ONNX FP16 matched current FP32 exactly for all 15 smoke-test outputs.

Before promotion, run the normal downstream evaluation suite against FP16, with
special attention to short prompts near keep/drop probability thresholds.

## ONNX CUDA Result

The ONNX candidate used CUDA I/O binding and the already-resized LLMLingua
vocabulary. It completed 15/15 requests without errors and with exact current
FP32 output parity, but was slower than PyTorch FP16:

| Tokens | PyTorch FP16 4 CPU | ONNX FP16 4 CPU | ONNX delta |
|---:|---:|---:|---:|
| 4,000 | 327.8 ms | 331.7 ms | +1.2% |
| 16,000 | 957.8 ms | 993.2 ms | +3.7% |
| 64,000 | 3,931.2 ms | 4,085.3 ms | +3.9% |

ONNX Runtime reported 12 inserted memory-copy nodes. Its image was also about
317 MB larger. Reject this implementation unless a future export removes the
copy nodes or TensorRT produces a clear measured win.

## Cloud Monitoring Method

`scripts/collect_cloud_run_metrics.py` queried the Cloud Monitoring v3
`projects.timeSeries` endpoint. Every query filtered all four resource labels:

```text
resource.type="cloud_run_revision"
resource.labels.location="us-central1"
resource.labels.service_name="prompt-compression"
resource.labels.revision_name="<exact revision>"
```

The collector requested these metric types:

```text
run.googleapis.com/container/cpu/utilizations
run.googleapis.com/container/memory/utilizations
run.googleapis.com/container/gpu/utilizations
run.googleapis.com/container/gpu/memory_utilizations
run.googleapis.com/container/gpu/memory_usages
run.googleapis.com/container/max_request_concurrencies
run.googleapis.com/container/instance_count
```

The query interval was the benchmark's UTC `started_at` through `finished_at`,
expanded by 30 seconds on each end. Collection occurred after waiting 130
seconds for delayed points. Raw API responses, normalized summaries, sample
counts, mean, p50, p95, and maximum are retained with each sustained run.

Cloud Run container utilization metrics are sampled at minute-scale intervals,
and GPU points were sparse in these short runs. Consequently, they establish
that the GPU and VRAM were lightly utilized, but they are not a high-resolution
kernel profile. A future microbatch experiment should add application-level
CUDA timing and, if feasible, a longer steady-state window.

References:

- [Cloud Run monitoring](https://docs.cloud.google.com/run/docs/monitoring)
- [Google Cloud metrics list](https://docs.cloud.google.com/monitoring/api/metrics_gcp_p_z)
- [Cloud Run GPU configuration](https://docs.cloud.google.com/run/docs/configuring/services/gpu)

## Run Inventory

All timestamps below are UTC and come from each run's `metadata.json`.

| Artifact | Revision | Start | Finish | Repeats / warmup | Sizes | Diagnostics | Result |
|---|---|---|---|---:|---|---|---:|
| `baseline-4cpu-fp32` | `prompt-compression-00009-jup` | 22:18:32 | 22:20:46 | 12 / 3 | 4,8,16,32,64k | off | 60/60 |
| `fp32-8cpu` | `prompt-compression-perf-fp32-8` | 22:26:53 | 22:29:59 | 12 / 3 | 4,8,16,32,64k | off | 60/60 |
| `fp16-8cpu` | `prompt-compression-perf-fp16-8` | 22:30:51 | 22:32:50 | 12 / 3 | 4,8,16,32,64k | off | 60/60 |
| `phase-baseline` | current baseline | 22:34:51 | 22:36:09 | 5 / 2 | 4,16,64k | basic | 15/15 |
| `phase-fp32-8cpu` | FP32 8 CPU | 22:37:02 | 22:37:51 | 5 / 2 | 4,16,64k | basic | 15/15 |
| `phase-fp16-8cpu` | FP16 8 CPU | 22:38:46 | 22:39:28 | 5 / 2 | 4,16,64k | basic | 15/15 |
| `fp16-8cpu-long` | FP16 8 CPU | 22:41:54 | 22:46:41 | 30 / 3 | 4,8,16,32,64k | off | 150/150 |
| `fp16-4cpu` | FP16 4 CPU | 22:47:26 | 22:50:30 | 20 / 3 | 4,8,16,32,64k | off | 100/100 |
| `onnx-fp16-4cpu-smoke` | ONNX FP16 4 CPU | 23:06:26 | 23:07:01 | 5 / 3 | 4,16,64k | off | 15/15 |
| `phase-onnx-fp16-4cpu` | ONNX FP16 4 CPU | 23:08:10 | 23:08:51 | 5 / 2 | 4,16,64k | basic | 15/15 |

The primary production runs were shuffled. The ONNX smoke and phase runs were
not. Each revision was warmed before measurements, but minimum instances was
zero; the excluded warmups therefore also absorb a possible revision cold
start.

## Validity And Limitations

The following limitations must remain attached to the result:

1. **Candidate-bundle confounding.** Current FP32 came from the older production
   image. FP16 came from the new experiment image and also enabled outer
   inference mode. The headline comparison is operationally useful, but it
   cannot attribute every millisecond to FP16 alone.
2. **Unequal repeat counts.** The main current baseline used 12 repeats per
   size; the selected FP16 4 CPU run used 20. Medians remain comparable, but a
   clean rerun should balance repeats.
3. **Single synthetic content family.** The workload was pure prose with no
   JSON, HTML, or protected spans. It isolates model runtime but does not
   establish quality or latency for the full production distribution.
4. **One prompt per size.** Repeats measure runtime variation on the same seeded
   prompt, not variation across many documents of the same size.
5. **Sequential request load.** Concurrency `1` measures single-request latency,
   not maximum throughput, queuing behavior, or safe microbatching.
6. **Sparse infrastructure telemetry.** Cloud Monitoring samples are too coarse
   to attribute individual requests or kernels.
7. **Tagged public routes.** Client timing contains internet and Cloud Run
   routing noise. The headline uses server timing to reduce this effect.
8. **No edge path.** The planned Cloudflare deterministic/cache/tenant layer was
   explicitly outside the experiment. These numbers characterize
   `compress.usagetap.com`'s model service, not end-to-end future request
   latency.
9. **No causal Python-versus-Node comparison.** No alternate implementation was
   built, and current evidence points to model work rather than HTTP runtime as
   the dominant cost.

The clean reproduction runner below fixes items 1 and 2. The next experiment
must address items 3–6.

## Exact Reproduction — Preferred Clean Rerun

The repository now includes `scripts/run_gpu_runtime_experiment.ps1`. It builds
one Torch image, creates tagged revisions with zero production traffic, runs
balanced FP32/FP16 host controls, captures phase and Cloud Monitoring data,
hash-compares outputs, and verifies after every deployment and at completion
that weighted production traffic is unchanged.

### Prerequisites

1. Run from the repository root on Windows PowerShell 7.
2. Install and authenticate `gcloud`; select an account that can use Cloud
   Build, Artifact Registry, Cloud Run, GPU revisions, and Cloud Monitoring.
3. Create `.venv` with the repository benchmark dependencies installed.
4. Confirm the target project already has the Artifact Registry repository
   `prompt-compression` in `us-central1`.
5. Prefer a clean committed worktree. The runner records the commit and refuses
   a dirty tree unless `-AllowDirty` is explicitly supplied.
6. Confirm the existing public service is exactly `prompt-compression`. The
   runner refuses any alternate service name, preventing the former
   `prompt-compression-gpu` naming mistake.

Authenticate and verify the target:

```powershell
$ProjectId = "YOUR_GCP_PROJECT_ID"
$Region = "us-central1"

gcloud auth login
gcloud config set project $ProjectId
gcloud run services describe prompt-compression `
  --project $ProjectId `
  --region $Region `
  --format="yaml(metadata.name,status.traffic)"
```

Record the output. There should be no public service named
`prompt-compression-gpu`; `-gpu` is only part of an image repository/tag.

### Run the balanced Torch experiment

```powershell
.\scripts\run_gpu_runtime_experiment.ps1 `
  -ProjectId $ProjectId `
  -Region $Region `
  -Service prompt-compression `
  -RunId "gpu-runtime-YYYYMMDD-HHMMSS" `
  -Repeats 20 `
  -Warmup 3
```

Add `-IncludeOnnx` only when intentionally retesting ONNX:

```powershell
.\scripts\run_gpu_runtime_experiment.ps1 `
  -ProjectId $ProjectId `
  -Region $Region `
  -Service prompt-compression `
  -RunId "gpu-runtime-YYYYMMDD-HHMMSS" `
  -Repeats 20 `
  -Warmup 3 `
  -IncludeOnnx
```

The exact runner sequence is:

1. Capture Git commit, dirty status, and all weighted production traffic.
2. Build a Torch image from the current source using `cloudbuild.gpu.yaml`.
3. Optionally build a separate image with `_ENABLE_ONNX_RUNTIME=true`.
4. Deploy same-image, `--no-traffic`, tagged revisions:
   `fp32-4`, `fp16-4`, `fp32-8`, `fp16-8`, and optional `onnx-fp16-4`.
5. Set every revision to one L4, CPU always allocated, concurrency `1`,
   min instances `0`, max instances `1`, and timeout 300 seconds.
6. Assert that weighted production traffic is byte-for-byte unchanged.
7. Run diagnostics-off requests at 4k, 8k, 16k, 32k, and 64k with 20 measured
   repeats, three warmups, fixed seed `1729`, and shuffled order.
8. Run basic phase profiles at 4k, 16k, and 64k with five repeats and two
   warmups.
9. Wait 130 seconds, then query revision-scoped Cloud Monitoring metrics over
   each production benchmark interval with ±30 seconds padding.
10. Compare every candidate output with same-image FP32 4 CPU using SHA-256 of
    `final_text`, plus token, reduction, status, fallback, and rollback deltas.
11. Assert once more that weighted production traffic is unchanged.

The generated request condition is exactly:

```text
endpoint: tagged revision /compress
target tokens: 4000,8000,16000,32000,64000
JSON ratios: 0
HTML ratios: 0
repeats: 20
warmup: 3
concurrency: 1
compression mode: model_force
aggressiveness: 0.25
deterministic transforms: enabled
include sections: false
diagnostics: off
seed: 1729
order: shuffled
```

### Manual benchmark equivalent

For a single tagged revision, this is the exact production-path command the
runner constructs:

```powershell
.\.venv\Scripts\python.exe scripts\benchmark_performance.py `
  --url "https://TAG---prompt-compression-HASH-uc.a.run.app/compress" `
  --out-dir "data\benchmarks\MANUAL_RUN" `
  --sizes "4000,8000,16000,32000,64000" `
  --json-ratios "0" `
  --html-ratios "0" `
  --repeats 20 `
  --warmup 3 `
  --concurrency 1 `
  --compression-mode model_force `
  --aggressiveness 0.25 `
  --diagnostics off `
  --seed 1729 `
  --label "variant=fp16-4" `
  --label "revision=FULL_REVISION_NAME" `
  --shuffle
```

The exact phase command changes only the output, sizes, repeat counts, and
diagnostics mode:

```powershell
.\.venv\Scripts\python.exe scripts\benchmark_performance.py `
  --url "https://TAG---prompt-compression-HASH-uc.a.run.app/compress" `
  --out-dir "data\benchmarks\MANUAL_RUN-phase" `
  --sizes "4000,16000,64000" `
  --json-ratios "0" `
  --html-ratios "0" `
  --repeats 5 `
  --warmup 2 `
  --concurrency 1 `
  --compression-mode model_force `
  --aggressiveness 0.25 `
  --diagnostics basic `
  --seed 1729 `
  --label "variant=fp16-4" `
  --label "revision=FULL_REVISION_NAME"
```

### Manual metrics equivalent

Use timestamps directly from that run's `metadata.json`:

```powershell
$Metadata = Get-Content "data\benchmarks\MANUAL_RUN\metadata.json" -Raw |
  ConvertFrom-Json

Start-Sleep -Seconds 130

.\.venv\Scripts\python.exe scripts\collect_cloud_run_metrics.py `
  --project $ProjectId `
  --region $Region `
  --service prompt-compression `
  --revision FULL_REVISION_NAME `
  --start $Metadata.started_at `
  --end $Metadata.finished_at `
  --padding-seconds 30 `
  --out-dir "data\benchmarks\MANUAL_RUN"
```

### Verify production traffic after the run

```powershell
gcloud run services describe prompt-compression `
  --project $ProjectId `
  --region $Region `
  --format="yaml(status.traffic)"
```

Compare this with the pre-run record. Tagged zero-percent revisions can remain
at `min-instances=0` for auditability, or be deleted later as a separate,
explicit cleanup action. The runner itself never deletes revisions and never
changes production traffic.

## Output Validation And Acceptance Gates

`scripts/compare_benchmark_outputs.py` joins records by
`case_id + repeat + condition_id`. It does not copy prompt or response text into
its summary. It records:

- exact `final_text` SHA-256 matches;
- prompt-hash mismatches;
- compressed-token and reduction deltas;
- status and fallback mismatches, plus rollback mismatches when the selected
  diagnostics schema exposes a rollback reason;
- missing or extra records;
- totals and per-target-size summaries.

Use these gates before promotion:

1. 100% measured request success, with no fallback or integrity rollback.
2. No prompt-hash mismatch and no missing comparison records.
3. Downstream quality suite passes on the exact FP16 image.
4. Any output difference is inspected and accepted, especially short prompts
   near probability thresholds.
5. FP16 same-image 4 CPU improves service p50 and p95 at every material size.
6. No material p95 regression in client latency.
7. Production traffic is still unchanged after the experiment.

Do not promote solely because the median is lower.

## Post-Experiment Deployment And Cloud Cleanup

Cleanup was completed after the benchmark:

- Cloud Run now contains one service, `prompt-compression`, and one revision,
  `prompt-compression-fp16-production`.
- The revision receives 100% of traffic with no tagged revision routes.
- Scaling is minimum `0`, maximum `1`; request concurrency remains `1`.
- The active image is pinned to
  `sha256:64a97a806dc5cf48a84bb55cd5c10172da88315f1be67638d32c8027e8a4c2cf`
  and tagged `20260724-fp16-production`, `latest`, and `production`.
- The deployed environment explicitly selects `float16`, the Torch runtime,
  and outer Torch inference mode.
- `/health` reported CUDA `12.4`, PyTorch `2.4.0+cu124`, NVIDIA L4,
  `loaded_model_dtype=float16`, and `model_loaded=true`.
- Forced-model validation on the candidate reported `llmlingua_called=true` in
  2,380 ms. Its candidate output was safely rejected by the
  identifier-integrity check, proving both model execution and rollback.
- Eleven superseded, benchmark, rejected, or transient cleanup revisions were
  deleted during initial cleanup; the superseded FP32 production revision was
  deleted after the FP16 cutover.
- Artifact Registry retains the untagged FP32 digest
  `sha256:da07dabe...532f5` as an immediate rollback artifact. No Cloud Run
  revision points to it.
- The retired CPU image and six obsolete GPU image digests were deleted.
- Seven Cloud Build source archives associated with the GPU deployment and
  benchmark were deleted under the bucket's seven-day soft-delete policy.
- The disposable FP16 Cloud Build source archive was also deleted after the
  image was pushed successfully.
- The Cloud Build bucket was retained. Forty-three older, non-benchmark source
  archives totaling 344.71 MiB remain because they were outside the benchmark
  cleanup scope.

The production build ID is
`e9f0c83a-6b1f-402b-bc25-e39629e5064b`. It was built from the current working
tree based on Git commit `91dc7e6cd14bae44479572007b71694994dab82d`; that
working tree contained uncommitted application changes. The immutable image
digest above is therefore the authoritative identity of the deployed artifact.
The build context also included the local synthetic `data/benchmarks` directory;
its compressed image-layer impact was approximately 8.8 MB and it contains no
production tenant data. `.dockerignore` and `.gcloudignore` now exclude that
directory from future images and Cloud Build uploads.

The local benchmark evidence in this report remains available even though the
tagged Cloud Run revisions and obsolete container images no longer exist.

## Production Cost Effect

FP16 does not change the Cloud Run rate per active instance because the
allocation is unchanged: one non-zonal-redundant L4, 4 vCPU, and 16 GiB memory.
At current `us-central1` list prices:

| Resource | Rate per active second |
|---|---:|
| NVIDIA L4, no zonal redundancy | $0.0001867 |
| 4 vCPU | $0.0000720 |
| 16 GiB memory | $0.0000320 |
| **Approximate total** | **$0.0002907** |

That is approximately **$1.0465 per active instance-hour**, before network,
build, storage, discounts, or taxes. With minimum instances `0`, there is no
permanent idle GPU allocation after Cloud Run scales the service down. GPU,
CPU, and memory are billed throughout an instance's lifecycle, including
between requests before scale-down.

The benchmark's 15–21% latency improvement can reduce compute cost per completed
request by a similar percentage when instance lifetime is dominated by request
processing. It does not guarantee a 15–21% bill reduction because traffic
shape, cold starts, scale-down delay, integrity fallback, and utilization also
affect billed lifetime.

The retained FP32 rollback image adds Artifact Registry storage. Published
storage pricing is about $0.10 per GiB-month above the 0.5 GiB billing-account
free tier, but the two images share most layers, so their displayed full image
sizes cannot be added to estimate actual incremental storage.

- [Cloud Run pricing](https://cloud.google.com/run/pricing)
- [Cloud Run GPU billing behavior](https://docs.cloud.google.com/run/docs/configuring/services/gpu)
- [Artifact Registry pricing](https://cloud.google.com/artifact-registry/pricing)

## Artifacts

- `data/benchmarks/gpu-exp-20260724/baseline-4cpu-fp32`
- `data/benchmarks/gpu-exp-20260724/fp32-8cpu`
- `data/benchmarks/gpu-exp-20260724/fp16-8cpu-long`
- `data/benchmarks/gpu-exp-20260724/fp16-4cpu`
- `data/benchmarks/gpu-exp-20260724/onnx-fp16-4cpu-smoke`
- `data/benchmarks/gpu-exp-20260724/phase-baseline`
- `data/benchmarks/gpu-exp-20260724/phase-fp16-8cpu`
- `data/benchmarks/gpu-exp-20260724/phase-onnx-fp16-4cpu`

Each benchmark directory contains:

- `metadata.json`: condition, seed, cohort, timestamps, counts, labels, and URL;
- `raw.jsonl`: per-request input hash, output, token counts, timing, and status;
- `summary.csv`: aggregated latency and compression metrics;
- Cloud Monitoring raw/summary files where infrastructure telemetry was
  collected.

A clean rerun additionally creates at
`data/benchmarks/gpu-runtime-<RunId>`:

- `experiment-manifest.json`: Git identity, original traffic, images, revisions,
  host/runtime controls, and benchmark settings;
- one production and one phase directory per variant;
- `parity-summary.json`: text-hash and compression-output comparisons.

## Recommended Next Experiment

1. Monitor FP16 output drift and integrity rollback rates, especially for short
   prompts near keep/drop probability thresholds.
2. Run the downstream quality suite against the immutable production digest.
3. Keep concurrency `1` as the latency control.
4. Split LLMLingua timing into tokenize, host-to-device, forward,
   device-to-host, and reconstruction phases.
5. Add a bounded request/chunk microbatcher with a 2–8 ms collection window.
6. Test concurrency `1`, `2`, and `4` while recording p50, p95, throughput,
   GPU utilization, output drift, fallback rate, and rollback rate.
7. Add representative prose/JSON/HTML/protected-content cohorts before making a
   production capacity decision.
