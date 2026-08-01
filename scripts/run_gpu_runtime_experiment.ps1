[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$ProjectId,

    [string]$Region = "us-central1",
    [string]$Service = "prompt-compression",
    [string]$RunId = (Get-Date -Format "yyyyMMdd-HHmmss"),
    [int]$Repeats = 20,
    [int]$Warmup = 3,
    [switch]$IncludeOnnx,
    [switch]$AllowDirty
)

$ErrorActionPreference = "Stop"
$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$OutputRoot = Join-Path $RepoRoot "data\benchmarks\gpu-runtime-$RunId"
$ImageBase = "$Region-docker.pkg.dev/$ProjectId/prompt-compression/prompt-compression-gpu"
$TorchImage = "$ImageBase`:$RunId-torch"
$OnnxImage = "$ImageBase`:$RunId-onnx"

if ($Service -ne "prompt-compression") {
    throw "Production service must remain prompt-compression. Image names are not service names."
}
if (-not (Get-Command gcloud -ErrorAction SilentlyContinue)) {
    throw "gcloud is required."
}
if (-not (Test-Path (Join-Path $RepoRoot ".venv\Scripts\python.exe"))) {
    throw "Create the repository .venv before running this experiment."
}

$Python = Join-Path $RepoRoot ".venv\Scripts\python.exe"
$GitArgs = @("-c", "safe.directory=C:/Users/troym/Git/PromptCompression")
$Commit = (& git @GitArgs rev-parse HEAD).Trim()
$Dirty = @(& git @GitArgs status --short)
if ($Dirty.Count -gt 0 -and -not $AllowDirty) {
    throw "The worktree is dirty. Commit the experiment code or rerun with -AllowDirty."
}

New-Item -ItemType Directory -Force -Path $OutputRoot | Out-Null

function Invoke-Checked {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Command,
        [Parameter(ValueFromRemainingArguments = $true)]
        [string[]]$Arguments
    )
    & $Command @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "$Command failed with exit code $LASTEXITCODE"
    }
}

function Get-ServiceState {
    $raw = & gcloud run services describe $Service `
        --project $ProjectId `
        --region $Region `
        --format=json
    if ($LASTEXITCODE -ne 0) {
        throw "Unable to describe Cloud Run service $Service."
    }
    return $raw | ConvertFrom-Json
}

function Get-WeightedTraffic {
    param([object]$State)
    return @(
        $State.spec.traffic |
            Where-Object { $null -ne $_.percent -and [int]$_.percent -gt 0 } |
            ForEach-Object {
                [pscustomobject]@{
                    revision = $_.revisionName
                    percent = [int]$_.percent
                }
            }
    )
}

function Assert-TrafficUnchanged {
    param(
        [object[]]$Expected,
        [object]$ActualState
    )
    $actual = Get-WeightedTraffic -State $ActualState
    $expectedJson = $Expected | ConvertTo-Json -Compress
    $actualJson = $actual | ConvertTo-Json -Compress
    if ($expectedJson -ne $actualJson) {
        throw "Weighted production traffic changed. Expected $expectedJson; found $actualJson."
    }
}

function Wait-RevisionReady {
    param([string]$Revision)
    $deadline = (Get-Date).AddMinutes(10)
    do {
        $ready = (& gcloud run revisions describe $Revision `
            --project $ProjectId `
            --region $Region `
            --format="value(status.conditions[0].status)").Trim()
        if ($LASTEXITCODE -ne 0) {
            throw "Unable to read revision $Revision."
        }
        if ($ready -eq "True") {
            return
        }
        if ($ready -eq "False") {
            throw "Revision $Revision failed to become ready."
        }
        Start-Sleep -Seconds 10
    } while ((Get-Date) -lt $deadline)
    throw "Revision $Revision did not become ready within ten minutes."
}

function Get-TagUrl {
    param([string]$Tag)
    $state = Get-ServiceState
    $target = $state.status.traffic | Where-Object { $_.tag -eq $Tag } | Select-Object -First 1
    if (-not $target.url) {
        throw "No URL was found for Cloud Run tag $Tag."
    }
    return [string]$target.url
}

function Deploy-Variant {
    param(
        [string]$Revision,
        [string]$Tag,
        [string]$Image,
        [int]$Cpu,
        [string]$Memory,
        [string]$Dtype,
        [string]$Runtime,
        [bool]$InferenceMode
    )
    $envVars = @(
        "COMPRESSOR_DEVICE=cuda"
        "COMPRESSOR_MIN_RATE=0.45"
        "COMPRESSOR_PRELOAD_SLOTS=base"
        "COMPRESSOR_GPU_P50_FIXED_OVERHEAD_MS=150"
        "COMPRESSOR_GPU_P50_LLMLINGUA_CHUNK_MS=120"
        "COMPRESSOR_GPU_P50_TOKEN_ESTIMATE_MS=80"
        "COMPRESSOR_MODEL_DTYPE=$Dtype"
        "COMPRESSOR_MODEL_RUNTIME=$Runtime"
        "COMPRESSOR_TORCH_INFERENCE_MODE=$($InferenceMode.ToString().ToLowerInvariant())"
    ) -join ","

    Invoke-Checked gcloud run deploy $Service `
        --project $ProjectId `
        --image $Image `
        --region $Region `
        --platform managed `
        --allow-unauthenticated `
        --port 8080 `
        --cpu $Cpu `
        --memory $Memory `
        --gpu 1 `
        --gpu-type nvidia-l4 `
        --no-cpu-throttling `
        --no-gpu-zonal-redundancy `
        --min-instances 0 `
        --max-instances 1 `
        --concurrency 1 `
        --timeout 300s `
        --no-traffic `
        --tag $Tag `
        --revision-suffix $Revision `
        --set-env-vars $envVars | Out-Host

    $fullRevision = "$Service-$Revision"
    Wait-RevisionReady -Revision $fullRevision
    Assert-TrafficUnchanged -Expected $script:OriginalTraffic -ActualState (Get-ServiceState)
    return [pscustomobject]@{
        id = $Revision
        revision = $fullRevision
        tag = $Tag
        url = Get-TagUrl -Tag $Tag
        image = $Image
        cpu = $Cpu
        memory = $Memory
        dtype = $Dtype
        runtime = $Runtime
        inference_mode = $InferenceMode
    }
}

function Invoke-Benchmark {
    param(
        [object]$Variant,
        [string]$Diagnostics,
        [string]$Sizes,
        [int]$MeasuredRepeats,
        [int]$WarmupRequests,
        [string]$Name,
        [switch]$Shuffle
    )
    $out = Join-Path $OutputRoot $Name
    $arguments = @(
        "scripts\benchmark_performance.py"
        "--url", "$($Variant.url)/compress"
        "--out-dir", $out
        "--sizes", $Sizes
        "--json-ratios", "0"
        "--html-ratios", "0"
        "--repeats", [string]$MeasuredRepeats
        "--warmup", [string]$WarmupRequests
        "--concurrency", "1"
        "--compression-mode", "model_force"
        "--aggressiveness", "0.25"
        "--diagnostics", $Diagnostics
        "--seed", "1729"
        "--label", "variant=$($Variant.id)"
        "--label", "revision=$($Variant.revision)"
    )
    if ($Shuffle) {
        $arguments += "--shuffle"
    }
    Invoke-Checked $Python @arguments | Out-Host
    return $out
}

function Collect-Metrics {
    param(
        [object]$Variant,
        [string]$BenchmarkDirectory
    )
    $metadata = Get-Content (Join-Path $BenchmarkDirectory "metadata.json") -Raw |
        ConvertFrom-Json
    Invoke-Checked $Python scripts\collect_cloud_run_metrics.py `
        --project $ProjectId `
        --region $Region `
        --service $Service `
        --revision $Variant.revision `
        --start $metadata.started_at `
        --end $metadata.finished_at `
        --padding-seconds 30 `
        --out-dir $BenchmarkDirectory | Out-Host
}

Push-Location $RepoRoot
try {
    $InitialState = Get-ServiceState
    $script:OriginalTraffic = Get-WeightedTraffic -State $InitialState

    Invoke-Checked gcloud builds submit `
        --project $ProjectId `
        --config cloudbuild.gpu.yaml `
        --substitutions "_REGION=$Region,_REPO=prompt-compression,_IMAGE_NAME=prompt-compression-gpu,_IMAGE_TAG=$RunId-torch,_ENABLE_ONNX_RUNTIME=false" `
        .

    if ($IncludeOnnx) {
        Invoke-Checked gcloud builds submit `
            --project $ProjectId `
            --config cloudbuild.gpu.yaml `
            --substitutions "_REGION=$Region,_REPO=prompt-compression,_IMAGE_NAME=prompt-compression-gpu,_IMAGE_TAG=$RunId-onnx,_ENABLE_ONNX_RUNTIME=true" `
            .
    }

    $variants = @(
        Deploy-Variant -Revision "$RunId-fp32-4" -Tag "$RunId-fp32-4" `
            -Image $TorchImage -Cpu 4 -Memory "16Gi" -Dtype "float32" `
            -Runtime "torch" -InferenceMode $false
        Deploy-Variant -Revision "$RunId-fp16-4" -Tag "$RunId-fp16-4" `
            -Image $TorchImage -Cpu 4 -Memory "16Gi" -Dtype "float16" `
            -Runtime "torch" -InferenceMode $true
        Deploy-Variant -Revision "$RunId-fp32-8" -Tag "$RunId-fp32-8" `
            -Image $TorchImage -Cpu 8 -Memory "32Gi" -Dtype "float32" `
            -Runtime "torch" -InferenceMode $false
        Deploy-Variant -Revision "$RunId-fp16-8" -Tag "$RunId-fp16-8" `
            -Image $TorchImage -Cpu 8 -Memory "32Gi" -Dtype "float16" `
            -Runtime "torch" -InferenceMode $true
    )
    if ($IncludeOnnx) {
        $variants += Deploy-Variant -Revision "$RunId-onnx-fp16-4" `
            -Tag "$RunId-onnx-fp16-4" -Image $OnnxImage -Cpu 4 -Memory "16Gi" `
            -Dtype "float16" -Runtime "onnx" -InferenceMode $true
    }

    $manifest = [ordered]@{
        schema = "gpu-runtime-experiment.v1"
        run_id = $RunId
        project = $ProjectId
        region = $Region
        service = $Service
        git_commit = $Commit
        dirty_worktree = $Dirty
        original_weighted_traffic = $script:OriginalTraffic
        variants = $variants
        benchmark = [ordered]@{
            sizes = @(4000, 8000, 16000, 32000, 64000)
            repeats = $Repeats
            warmup = $Warmup
            concurrency = 1
            mode = "model_force"
            aggressiveness = 0.25
            diagnostics = "off"
            seed = 1729
            json_ratios = @(0)
            html_ratios = @(0)
        }
    }
    $manifest | ConvertTo-Json -Depth 8 |
        Set-Content -LiteralPath (Join-Path $OutputRoot "experiment-manifest.json")

    $productionRuns = @{}
    foreach ($variant in $variants) {
        $productionRuns[$variant.id] = Invoke-Benchmark -Variant $variant `
            -Diagnostics "off" -Sizes "4000,8000,16000,32000,64000" `
            -MeasuredRepeats $Repeats -WarmupRequests $Warmup `
            -Name $variant.id -Shuffle
    }

    foreach ($variant in $variants) {
        Invoke-Benchmark -Variant $variant -Diagnostics "basic" `
            -Sizes "4000,16000,64000" -MeasuredRepeats 5 -WarmupRequests 2 `
            -Name "$($variant.id)-phase" | Out-Null
    }

    Write-Host "Waiting 130 seconds for delayed Cloud Monitoring samples..."
    Start-Sleep -Seconds 130
    foreach ($variant in $variants) {
        Collect-Metrics -Variant $variant `
            -BenchmarkDirectory $productionRuns[$variant.id]
    }

    $comparisonArgs = @(
        "scripts\compare_benchmark_outputs.py"
        "--baseline", (Join-Path $productionRuns[$variants[0].id] "raw.jsonl")
        "--out", (Join-Path $OutputRoot "parity-summary.json")
    )
    foreach ($variant in $variants | Select-Object -Skip 1) {
        $candidateRaw = Join-Path $productionRuns[$variant.id] "raw.jsonl"
        $comparisonArgs += @("--candidate", "$($variant.id)=$candidateRaw")
    }
    Invoke-Checked $Python @comparisonArgs | Out-Host

    Assert-TrafficUnchanged -Expected $script:OriginalTraffic -ActualState (Get-ServiceState)
    Write-Host "Experiment complete: $OutputRoot"
}
finally {
    Pop-Location
}
