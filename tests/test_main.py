from concurrent.futures import ThreadPoolExecutor
from threading import Event

from fastapi.testclient import TestClient
import pytest

from app import main
from app.compressor import (
    CompressionDiagnostics,
    CompressionOutputSection,
    CompressionResult,
    CompressionTiming,
    CompressionToken,
    build_token_savings,
)
from app.demo_access import DemoSessionManager
from app.eval_suite import EvalCase
from app.schemas import (
    CompressRequest,
    EvaluationConstraints,
    EvalRunRequest,
    TenantCompressionSettings,
    TokenEstimateRequest,
    V1CompressRequest,
    V1CompressionSettings,
    V1MessagesCompressRequest,
)
from app.tenant_profiles import TenantCompressionProfile
from app.token_estimator import TokenEstimate
from app.usagetap_authorization import UsageTapAuthorization
from app.usagetap_metering import UsageTapMeteringError


class FakeUsageTapAuthorizationClient:
    def validate_incoming_credential(self, authorization_header: str | None) -> str:
        assert authorization_header == "Bearer cmp-test-key"
        return authorization_header

    def authorize(self, authorization_header: str | None) -> UsageTapAuthorization:
        assert authorization_header == "Bearer cmp-test-key"
        return UsageTapAuthorization(
            organization_id="org_test",
            customer_id="customer_test",
        )


class FakeUsageTapMeteringClient:
    def __init__(self) -> None:
        self.calls = []

    def record_compression_savings(self, **kwargs):
        self.calls.append(kwargs)
        return None


@pytest.fixture(autouse=True)
def use_fake_usagetap_authorization(monkeypatch):
    main.compression_response_cache.clear()
    metering_client = FakeUsageTapMeteringClient()
    monkeypatch.setattr(
        main,
        "usage_tap_authorization_client",
        FakeUsageTapAuthorizationClient(),
    )
    monkeypatch.setattr(main, "usage_tap_metering_client", metering_client)
    yield metering_client
    main.compression_response_cache.clear()


class FakeCompressionService:
    model_name = "fake-model"
    is_loaded = True

    def __init__(self) -> None:
        self.calls: list[tuple[str, float, bool]] = []
        self.tenant_profiles: list[TenantCompressionProfile | None] = []

    def compress(
        self,
        text: str,
        aggressiveness: float,
        include_sections: bool = True,
        tenant_profile: TenantCompressionProfile | None = None,
        mode: str | None = None,
        latency_budget_ms: float | None = None,
        allow_cpu_model_auto: bool | None = None,
        min_model_candidate_tokens: int | None = None,
        model_chunk_chars: int | None = None,
        collect_diagnostics: bool = True,
        collect_detailed_analytics: bool = True,
        evaluate_disabled_transforms: bool = False,
        evaluation_constraints: dict[str, list[str]] | None = None,
        request_id: str | None = None,
        allow_inline_json_compression_paths: bool = False,
    ) -> CompressionResult:
        self.calls.append((text, aggressiveness, include_sections))
        self.tenant_profiles.append(tenant_profile)
        self.last_text = text
        self.last_aggressiveness = aggressiveness
        self.last_include_sections = include_sections
        self.last_tenant_profile = tenant_profile
        self.last_mode = mode
        self.last_latency_budget_ms = latency_budget_ms
        self.last_allow_cpu_model_auto = allow_cpu_model_auto
        self.last_min_model_candidate_tokens = min_model_candidate_tokens
        self.last_model_chunk_chars = model_chunk_chars
        self.last_collect_diagnostics = collect_diagnostics
        self.last_collect_detailed_analytics = collect_detailed_analytics
        self.last_evaluate_disabled_transforms = evaluate_disabled_transforms
        self.last_evaluation_constraints = evaluation_constraints
        self.last_request_id = request_id
        self.last_allow_inline_json_compression_paths = (
            allow_inline_json_compression_paths
        )
        labels = [
            CompressionToken(text="Prompts", kept=True),
            CompressionToken(text="are", kept=False),
            CompressionToken(text="code.", kept=True),
        ] if include_sections else []
        sections = [
            CompressionOutputSection(
                text="Prompts code.",
                kind="prose",
                compressed=True,
                protected=False,
                labeled_tokens=labels,
            )
        ] if include_sections else []
        return CompressionResult(
            compressed_text="Prompts code.",
            original_tokens=4,
            compressed_tokens=2,
            reduction=0.5,
            aggressiveness=aggressiveness,
            target_rate=0.75,
            model=self.model_name,
            elapsed_ms=12.5,
            labeled_tokens=labels,
            output_sections=sections,
            tenant_id="default" if tenant_profile is None else tenant_profile.tenant_id,
            compression_profile=(
                "default:base" if tenant_profile is None else tenant_profile.profile_id
            ),
            compression_profile_source=(
                "default" if tenant_profile is None else tenant_profile.source
            ),
            training_sample_recorded=False,
            diagnostics=CompressionDiagnostics(
                timings=CompressionTiming(
                    total_ms=12.5,
                    target_rate_ms=0.1,
                    preprocessing_ms=1.0,
                    force_drop_ms=0.1,
                    segment_selection_ms=2.0,
                    model_load_ms=0.0,
                    model_input_ms=0.2,
                    force_tokens_ms=0.1,
                    llmlingua_ms=8.0,
                    placeholder_validation_ms=0.1,
                    model_expand_ms=0.4,
                    uncompressed_expand_ms=0.0,
                    token_estimate_ms=0.4,
                    other_ms=0.1,
                ),
                input_chars=len(text),
                output_chars=len("Prompts code."),
                segment_count=1,
                compressible_segment_count=1,
                model_segment_count=1,
                skipped_segment_count=0,
                placeholder_count=0,
                model_input_chars=len(text),
                segment_kinds={"prose": 1},
                llmlingua_called=True,
                fallback_used=False,
                deterministic_original_tokens=4,
                deterministic_output_tokens=3,
                deterministic_tokens_saved=1,
                deterministic_reduction=0.25,
                model_incremental_tokens_saved=1,
                model_incremental_reduction=1 / 3,
            ),
            compression_mode=mode or "model_force",
            compression_path="deterministic_plus_model",
            token_savings=build_token_savings(
                original_tokens=4,
                after_deterministic_tokens=3,
                final_tokens=2,
                model_ran=True,
                fallback_used=False,
                token_estimator="regex:unicode-word-or-non-space",
            ),
        )


def test_index_returns_prompt_compression_ui():
    response = main.index()
    body = response.body.decode()

    assert "Prompt Compression" in body
    assert "Eval Suite" in body
    assert 'href="/benchmark"' in body
    assert 'href="/experiments"' in body
    assert 'href="/research"' in body
    assert "Dropped Words Highlighted" in body
    assert "Diagnostic Logs" in body
    assert "JSON compressed to TOON" in body
    assert "Optional preserve controls" in body
    assert "Tenant Profile" in body
    assert 'id="tenantTestPreset"' in body
    assert 'id="compressionMode"' in body
    assert 'id="compressionApiKey"' in body
    assert 'type="password"' in body
    assert 'autocomplete="new-password"' in body
    assert "const COMPRESSION_CREDENTIAL_PATTERN" in body
    assert "demo-v1\\." in body
    assert '"Authorization": `Bearer ${compressionApiKey}`' in body
    assert 'id="startDemoButton"' in body
    assert 'fetch("/demo/session"' in body
    assert "Credentials stay in page memory only" in body
    assert "localStorage" not in body
    assert "sessionStorage" not in body
    assert 'id="loadTextJsonExampleButton"' in body
    assert 'id="loadHtmlExampleButton"' in body
    assert 'id="loadTranscriptExampleButton"' in body
    assert "Text + JSON" in body
    assert "Meeting Transcript" in body
    assert 'class="example-controls"' in body
    assert 'class="example-button" id="loadTextJsonExampleButton"' in body
    assert "#compressButton" in body
    assert "HTML page converted to Markdown" in body
    assert "Prompt Compression Guide" in body
    assert '<option value="model_force" selected>Model force</option>' in body
    assert 'id="latencyBudgetMs"' in body
    assert 'id="allowCpuModelAuto" type="checkbox">' in body
    assert 'id="includeDetailedAnalytics" type="checkbox" checked>' in body
    assert "tenant_rick_probe" in body
    assert 'id="tenantId"' in body
    assert 'id="tenantProfileId"' in body
    assert 'id="tenantForceKeepTokens"' in body
    assert 'id="tenantForceDropPhrases"' in body
    assert "buildTenantPayload" in body
    assert "&lt;nocompress&gt;...&lt;/nocompress&gt;" in body
    assert "&lt;compress-json paths=" in body
    assert "markdown fences are protected from compression" in body
    assert "requestPayload.include_sections = true" in body
    assert "requestPayload.include_diagnostics = true" in body
    assert (
        "requestPayload.include_detailed_analytics = "
        "includeDetailedAnalyticsInput.checked" in body
    )
    assert "requestPayload.allow_inline_json_compression_paths = true" in body
    assert "requestPayload.mode = compressionModeInput.value" in body
    assert "requestPayload.latency_budget_ms = latencyBudgetMs" in body
    assert "requestPayload.allow_cpu_model_auto = true" in body
    assert "renderDiagnostics" in body
    title_index = body.index("<h2>Original Prompt</h2>")
    input_index = body.index('textarea id="prompt"')
    button_index = body.index('id="compressButton"')
    settings_index = body.index("Compression Settings")
    tenant_index = body.index("Tenant Profile")
    docs_index = body.index("Optional preserve controls")
    assert title_index < input_index < button_index < settings_index
    assert settings_index < tenant_index < docs_index


def test_demo_session_authorizes_compression_without_customer_metering(
    monkeypatch,
    use_fake_usagetap_authorization,
):
    manager = DemoSessionManager(
        enabled=True,
        signing_key="demo-signing-key-with-at-least-thirty-two-bytes",
        session_ttl_seconds=600,
        max_operations=2,
        max_input_chars=100,
        max_input_chars_per_operation=80,
        clock=lambda: 1_000,
    )
    service = FakeCompressionService()
    monkeypatch.setattr(main, "demo_session_manager", manager)
    monkeypatch.setattr(main, "compression_service", service)
    client = TestClient(main.app)

    session_response = client.post("/demo/session")

    assert session_response.status_code == 200
    assert session_response.headers["cache-control"] == "no-store, max-age=0"
    session_payload = session_response.json()
    assert session_payload["maxOperations"] == 2
    assert session_payload["maxInputCharsPerOperation"] == 80
    assert session_payload["dailySessionsRemaining"] == 4
    assert session_payload["dailyOperationsRemaining"] == 25
    token = session_payload["token"]

    compression_response = client.post(
        "/compress",
        headers={"Authorization": f"Bearer {token}"},
        json={"text": "Prompts are code."},
    )

    assert compression_response.status_code == 200
    assert service.calls == [("Prompts are code.", main.DEFAULT_AGGRESSIVENESS, False)]
    assert use_fake_usagetap_authorization.calls == []


def test_demo_session_endpoint_rate_limits_trusted_cloud_run_client(
    monkeypatch,
):
    manager = DemoSessionManager(
        enabled=True,
        signing_key="demo-signing-key-with-at-least-thirty-two-bytes",
        rate_limit_sessions=1,
        rate_limit_window_seconds=60,
        clock=lambda: 1_000,
    )
    monkeypatch.setattr(main, "demo_session_manager", manager)
    monkeypatch.setenv("K_SERVICE", "prompt-compression")
    client = TestClient(main.app)
    first_headers = {"X-Forwarded-For": "198.51.100.8, 192.0.2.10"}

    assert client.post("/demo/session", headers=first_headers).status_code == 200
    limited = client.post("/demo/session", headers=first_headers)

    assert limited.status_code == 429
    assert limited.headers["retry-after"] == "20"
    assert client.post(
        "/demo/session",
        headers={"X-Forwarded-For": "198.51.100.9, 192.0.2.10"},
    ).status_code == 200


def test_demo_input_limit_is_enforced_before_compression(monkeypatch):
    manager = DemoSessionManager(
        enabled=True,
        signing_key="demo-signing-key-with-at-least-thirty-two-bytes",
        max_input_chars=10,
        max_input_chars_per_operation=5,
        clock=lambda: 1_000,
    )
    service = FakeCompressionService()
    monkeypatch.setattr(main, "demo_session_manager", manager)
    monkeypatch.setattr(main, "compression_service", service)
    client = TestClient(main.app)
    token = client.post("/demo/session").json()["token"]

    response = client.post(
        "/compress",
        headers={"Authorization": f"Bearer {token}"},
        json={"text": "123456"},
    )

    assert response.status_code == 413
    assert response.json() == {"detail": "Demo input is too large for one operation."}
    assert service.calls == []


def test_compress_request_rejects_unknown_experiment_profile():
    response = TestClient(main.app).post(
        "/compress",
        headers={"Authorization": "Bearer cmp-test-key"},
        json={"text": "hello", "experiment_profile": "not-allowlisted"},
    )

    assert response.status_code == 422


def test_index_http_allows_iframe_embedding():
    client = TestClient(main.app)

    response = client.get("/")

    assert response.status_code == 200
    assert response.headers["content-security-policy"] == "frame-ancestors *"
    assert "x-frame-options" not in response.headers


def test_embed_returns_streamlined_iframe_ui():
    client = TestClient(main.app)

    response = client.get("/embed")
    body = response.text

    assert response.status_code == 200
    assert response.headers["content-security-policy"] == "frame-ancestors *"
    assert "Prompt Compression" in body
    assert 'id="prompt"' in body
    assert 'id="aggressiveness"' in body
    assert 'id="aggressiveness" type="range" min="0" max="1" step="0.05" value="0.30"' in body
    assert 'id="compressButton"' in body
    assert 'id="copyButton"' in body
    assert 'id="startDemoButton"' in body
    assert "Start 10-minute demo" in body
    assert 'fetch("/demo/session"' in body
    assert '"Authorization":demoAuthorization' in body
    assert "Start a demo session first" in body
    assert "localStorage" not in body
    assert "sessionStorage" not in body
    assert 'id="elapsed"' not in body
    assert ">Elapsed<" not in body
    assert "Eval Suite" not in body
    assert "Benchmark" not in body
    assert "Compression Settings" not in body
    assert "Tenant Profile" not in body
    assert 'id="loadJsonExampleButton"' in body
    assert 'id="loadHtmlExampleButton"' in body
    assert 'id="loadTranscriptExampleButton"' in body
    assert "HTML Page" in body
    assert "Meeting Transcript" in body
    assert "Text + JSON" in body
    assert "promptInput.value = JSON_EXAMPLE" in body
    assert "include_diagnostics:false" in body
    assert "include_detailed_analytics:false" in body
    assert "tenant_profile" not in body


def test_health_includes_deployment_version():
    service = FakeCompressionService()
    original_service = main.compression_service
    main.compression_service = service
    try:
        response = main.health()
    finally:
        main.compression_service = original_service

    assert response.status == "ok"
    assert response.deployment_version == main.DEPLOYMENT_VERSION
    assert response.deployment_timestamp == main.DEPLOYMENT_TIMESTAMP
    assert response.model == service.model_name
    assert response.model_loaded is True


def test_eval_index_returns_eval_ui():
    response = main.eval_index()
    body = response.body.decode()

    assert "Prompt Compression Eval" in body
    assert "Run Selected" in body
    assert 'href="/benchmark"' in body
    assert 'href="/experiments"' in body
    assert 'href="/research"' in body
    assert "/eval/run" in body


def test_benchmark_index_returns_benchmark_page():
    response = main.benchmark_index()
    body = response.body.decode()

    assert "Performance Benchmark" in body
    assert 'href="/eval"' in body
    assert 'href="/experiments"' in body
    assert "include_diagnostics" in body
    assert "Download Raw JSONL" in body
    assert "LLMLingua p50" in body
    assert "Edge routing is not included." in body
    assert "Browser p50" in body
    assert "Model requests" in body
    assert "LLMLingua chunks" in body
    assert "Gate skips" in body
    assert "Top gate" in body
    assert "model_gate_reason_top" in body
    assert 'id="htmlRatiosInput"' in body
    assert "HTML ratios" in body
    assert "html_markdown" in body
    assert 'id="compressionModeInput"' in body
    assert 'id="compressionApiKey"' in body
    assert 'type="password"' in body
    assert 'autocomplete="new-password"' in body
    assert "const COMPRESSION_CREDENTIAL_PATTERN" in body
    assert "demo-v1\\." in body
    assert '"Authorization": `Bearer ${compressionApiKey}`' in body
    assert 'id="startDemoButton"' in body
    assert 'fetch("/demo/session"' in body
    assert "Credentials stay in page memory and out of downloads" in body
    assert "localStorage" not in body
    assert "sessionStorage" not in body
    assert '<option value="model_auto" selected>Model auto</option>' in body
    assert 'id="latencyBudgetInput"' in body
    assert 'id="allowCpuModelAutoInput" type="checkbox" checked' in body
    assert 'id="minModelCandidateTokensInput" type="range"' in body
    assert 'id="modelChunkCharsInput" type="range"' in body
    assert 'id="protectedProseRatioInput" type="range"' in body
    assert 'id="diagnosticsModeInput"' in body
    assert '<option value="off" selected>Production latency</option>' in body
    assert '<option value="basic">Phase profile</option>' in body
    assert '<option value="detailed">Deep analytics</option>' in body
    assert "mode: compressionModeInput.value" in body
    assert "payload.latency_budget_ms" in body
    assert "payload.allow_cpu_model_auto = true" in body
    assert "min_model_candidate_tokens: selectedModelCandidateFloor()" in body
    assert "model_chunk_chars: selectedModelChunkChars()" in body
    assert 'placeholder="blank = service max"' in body
    assert 'include_diagnostics: diagnosticsModeInput.value !== "off"' in body
    assert 'evaluate_disabled_transforms: diagnosticsModeInput.value === "detailed"' in body
    assert 'include_detailed_analytics: diagnosticsModeInput.value === "detailed"' in body
    assert "Diagnostics p50" in body
    assert "_protected${formatRatio(protectedProseRatio)}" in body
    assert "DIAGNOSTICS" in body
    assert "diagnosticLogFromResponse" in body


def test_research_index_returns_research_page():
    response = main.research_index()
    body = response.body.decode()

    assert "Prompt Compression Research" in body
    assert 'href="/benchmark"' in body
    assert 'href="/experiments"' in body
    assert "LLMLingua-2 BERT-base" in body
    assert "PCToolkit Assessment" in body
    assert "not as a production runtime dependency" in body
    assert "SCOPE: A Generative Approach" in body
    assert "Toolkit for Prompt Compression" in body
    assert "Hugging Face PEFT" in body


def test_experiments_index_returns_evidence_ledger():
    response = main.experiments_index()
    body = response.body.decode()

    assert "Compression experiments, with receipts." in body
    assert "Current phases" in body
    assert "Fixed safety corpus" in body
    assert "Delivery Tower prompt slice" in body
    assert "Tenant 1" in body
    assert "Tenant 2" in body
    assert "47 unsafe model candidates" in body
    assert "earlier 39-token tenant observation" in body
    assert "completed evidence cohorts" in body
    assert "Final integrity validation &amp; rollback" in body
    assert "Critical-clause shielding" in body
    assert "Shielding on/off guardrail ablation" in body
    assert "Resolved and verified: skipped JSON is input-neutral" in body
    assert "Safety default and final transform decisions" in body
    assert "624 versus 486 accepted tokens saved" in body
    assert "categorized relationship, negation, permission" in body
    assert "json_minify_safe" in body
    assert "safe_stack_v1" in body
    assert "15.9%" in body
    assert 'href="/benchmark"' in body
    assert 'aria-current="page"' in body


def test_experiments_http_allows_iframe_embedding():
    response = TestClient(main.app).get("/experiments")

    assert response.status_code == 200
    assert response.headers["content-security-policy"] == "frame-ancestors *"


def test_eval_cases_endpoint_returns_fixture_cases():
    response = main.list_eval_cases()

    assert len(response) >= 6
    assert response[0].text
    assert response[0].required_substrings


def test_eval_run_uses_fake_service_and_quality_checks(monkeypatch):
    service = FakeCompressionService()
    monkeypatch.setattr(main, "compression_service", service)
    monkeypatch.setattr(
        main,
        "eval_cases",
        [
            EvalCase(
                id="sample",
                title="Sample",
                category="test",
                description="Sample eval.",
                text="Prompts are code.",
                default_aggressiveness=0.25,
                required_substrings=["Prompts code."],
                expected_section_kinds=["prose"],
                target_min_reduction=0.25,
            )
        ],
    )

    response = main.run_eval(EvalRunRequest(case_ids=["sample"], aggressiveness=0.4))

    assert service.last_text == "Prompts are code."
    assert service.last_aggressiveness == 0.4
    assert service.last_include_sections is True
    assert response.passed is True
    assert response.total_cases == 1
    assert response.passed_cases == 1
    assert response.results[0].compressed_text == "Prompts code."


def test_eval_run_unknown_case_returns_404(monkeypatch):
    monkeypatch.setattr(main, "eval_cases", [])

    try:
        main.run_eval(EvalRunRequest(case_ids=["missing"]))
    except main.HTTPException as exc:
        assert exc.status_code == 404
        assert "missing" in str(exc.detail)
    else:
        raise AssertionError("Expected HTTPException")


def test_api_allows_sandboxed_iframe_fetches():
    client = TestClient(main.app)

    response = client.options(
        "/compress",
        headers={
            "Origin": "null",
            "Access-Control-Request-Method": "POST",
        },
    )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "*"


def test_compress_response_omits_sections_by_default(monkeypatch):
    service = FakeCompressionService()
    monkeypatch.setattr(main, "compression_service", service)

    response = main.compress(
        CompressRequest(text="Prompts are code.", aggressiveness=0.25)
    )

    assert service.last_include_sections is False
    assert service.last_mode == "model_force"
    assert service.last_collect_diagnostics is False
    assert response.tenant_id == "default"
    assert response.compression_profile == "default:base"
    assert response.compression_profile_source == "default"
    assert response.training_sample_recorded is False
    assert [token.model_dump() for token in response.labeled_tokens] == []
    assert response.output_sections == []
    assert response.diagnostics is None
    assert response.token_savings.model_dump() == {
        "original_tokens": 4,
        "after_deterministic_tokens": 3,
        "final_tokens": 2,
        "deterministic_tokens_saved": 1,
        "model_incremental_tokens_saved": 1,
        "total_tokens_saved": 2,
        "deterministic_reduction": 0.25,
        "model_incremental_reduction": 1 / 3,
        "total_reduction": 0.5,
        "model_stage": "llmlingua2",
        "model_ran": True,
        "fallback_used": False,
        "attribution_residual_tokens": 0,
        "token_estimator": "regex:unicode-word-or-non-space",
    }
    assert "NaN" not in response.model_dump_json()
    assert "Infinity" not in response.model_dump_json()


def test_compress_response_includes_diagnostics_when_requested(monkeypatch):
    service = FakeCompressionService()
    monkeypatch.setattr(main, "compression_service", service)

    response = main.compress(
        CompressRequest(
            text="Prompts are code.",
            aggressiveness=0.25,
            include_diagnostics=True,
        )
    )

    assert response.diagnostics is not None
    assert response.diagnostics.timings.llmlingua_ms == 8.0
    assert response.diagnostics.model_segment_count == 1
    assert (
        response.token_savings.deterministic_tokens_saved
        == response.diagnostics.deterministic_tokens_saved
    )
    assert (
        response.token_savings.model_incremental_tokens_saved
        == response.diagnostics.model_incremental_tokens_saved
    )
    assert service.last_collect_diagnostics is True


def test_compress_passes_benchmark_only_analytics_controls(monkeypatch):
    service = FakeCompressionService()
    monkeypatch.setattr(main, "compression_service", service)

    main.compress(
        CompressRequest(
            text="Keep UT-1042.",
            include_diagnostics=True,
            evaluate_disabled_transforms=True,
            evaluation_constraints=EvaluationConstraints(
                required_substrings=["UT-1042"],
                required_json_keys=["ticket_id"],
            ),
        ),
        x_request_id="benchmark-request-42",
    )

    assert service.last_evaluate_disabled_transforms is True
    assert service.last_evaluation_constraints == {
        "required_substrings": ["UT-1042"],
        "required_whitespace_insensitive_substrings": [],
        "forbidden_substrings": [],
        "required_json_keys": ["ticket_id"],
    }
    assert service.last_request_id == "benchmark-request-42"


def test_compress_can_request_lightweight_diagnostics(monkeypatch):
    service = FakeCompressionService()
    monkeypatch.setattr(main, "compression_service", service)

    main.compress(
        CompressRequest(
            text="Prompts are code.",
            include_diagnostics=True,
            include_detailed_analytics=False,
        )
    )

    assert service.last_collect_diagnostics is True
    assert service.last_collect_detailed_analytics is False


def test_compress_passes_cpu_model_auto_override(monkeypatch):
    service = FakeCompressionService()
    monkeypatch.setattr(main, "compression_service", service)

    main.compress(
        CompressRequest(
            text="Prompts are code.",
            aggressiveness=0.25,
            mode="model_auto",
            latency_budget_ms=500.0,
            allow_cpu_model_auto=True,
            min_model_candidate_tokens=2_000,
            model_chunk_chars=48_000,
        )
    )

    assert service.last_mode == "model_auto"
    assert service.last_latency_budget_ms == 500.0
    assert service.last_allow_cpu_model_auto is True
    assert service.last_min_model_candidate_tokens == 2_000
    assert service.last_model_chunk_chars == 48_000


def test_compress_response_includes_sections_when_requested(monkeypatch):
    service = FakeCompressionService()
    monkeypatch.setattr(main, "compression_service", service)

    response = main.compress(
        CompressRequest(
            text="Prompts are code.",
            aggressiveness=0.25,
            include_sections=True,
        )
    )

    assert service.last_include_sections is True
    assert [token.model_dump() for token in response.labeled_tokens] == [
        {"text": "Prompts", "kept": True},
        {"text": "are", "kept": False},
        {"text": "code.", "kept": True},
    ]
    assert [section.model_dump() for section in response.output_sections] == [
        {
            "text": "Prompts code.",
            "kind": "prose",
            "compressed": True,
            "protected": False,
            "labeled_tokens": [
                {"text": "Prompts", "kept": True},
                {"text": "are", "kept": False},
                {"text": "code.", "kept": True},
            ],
        }
    ]


def test_compress_uses_request_supplied_tenant_profile(monkeypatch):
    service = FakeCompressionService()
    monkeypatch.setattr(main, "compression_service", service)

    response = main.compress(
        CompressRequest(
            tenant_id="tenant_123",
            tenant_profile=TenantCompressionSettings(
                profile_id="tenant_123:v1",
                default_aggressiveness=0.42,
                min_rate=0.6,
                force_keep_tokens=["AcmeTerm", "AcmeTerm", "  SKU-77  "],
                force_drop_phrases=["Reusable preamble", ""],
                json_compression_policy_id="issue-v1",
                json_value_compression_paths=[
                    "$.description",
                    "$.comments[*].body",
                ],
                json_value_min_tokens=120,
                json_value_max_reduction=0.2,
                json_value_max_values=4,
            ),
            text="Prompts are code.",
        )
    )

    profile = service.last_tenant_profile
    assert profile is not None
    assert service.last_aggressiveness == 0.42
    assert profile.tenant_id == "tenant_123"
    assert profile.profile_id == "tenant_123:v1"
    assert profile.source == "api"
    assert profile.min_rate == 0.6
    assert profile.force_keep_tokens == ("AcmeTerm", "SKU-77")
    assert profile.force_drop_phrases == ("Reusable preamble",)
    assert profile.json_compression_policy_id == "issue-v1"
    assert profile.json_value_compression_paths == (
        "$.description",
        "$.comments[*].body",
    )
    assert profile.json_value_min_tokens == 120
    assert profile.json_value_max_reduction == 0.2
    assert profile.json_value_max_values == 4
    assert response.tenant_id == "tenant_123"
    assert response.compression_profile == "tenant_123:v1"
    assert response.compression_profile_source == "api"
    assert response.training_sample_recorded is False


def test_compress_profiler_can_enable_inline_json_paths(monkeypatch):
    service = FakeCompressionService()
    monkeypatch.setattr(main, "compression_service", service)

    main.compress(
        CompressRequest(
            text=(
                '<compress-json paths="$.description">'
                '{"description":"Long narrative"}'
                "</compress-json>"
            ),
            allow_inline_json_compression_paths=True,
        )
    )

    assert service.last_allow_inline_json_compression_paths is True


def test_v1_compress_returns_compatible_shape(monkeypatch):
    service = FakeCompressionService()
    monkeypatch.setattr(main, "compression_service", service)

    response = main.compress_v1(
        V1CompressRequest(
            model="bear-2",
            input="Prompts are code.",
            compression_settings=V1CompressionSettings(
                aggressiveness=0.6
            ),
        )
    )

    assert service.last_text == "Prompts are code."
    assert service.last_aggressiveness == 0.6
    assert service.last_include_sections is False
    assert service.last_mode == "deterministic"
    assert response.model_dump() == {
        "output": "Prompts code.",
        "output_tokens": 2,
        "input_tokens": 4,
        "original_input_tokens": 4,
        "tokens_saved": 2,
        "compression_ratio": 2.0,
        "token_estimator": "regex:unicode-word-or-non-space",
        "downstream_estimated_input_tokens": 4,
        "downstream_estimated_output_tokens": 3,
        "downstream_token_estimator": "regex:unicode-word-or-non-space",
        "compression_time": 12.5,
        "tenant_id": "default",
        "compression_profile": "default:base",
        "compression_profile_source": "default",
        "training_sample_recorded": False,
        "warnings": [],
    }


def test_v1_compress_defaults_aggressiveness(monkeypatch):
    service = FakeCompressionService()
    monkeypatch.setattr(main, "compression_service", service)

    main.compress_v1(
        V1CompressRequest(
            model="bear-2",
            input="Prompts are code.",
        )
    )

    assert service.last_aggressiveness == main.DEFAULT_AGGRESSIVENESS
    assert service.last_mode == "deterministic"


def test_v1_compress_http_accepts_compatible_request(monkeypatch):
    service = FakeCompressionService()
    monkeypatch.setattr(main, "compression_service", service)
    client = TestClient(main.app)

    response = client.post(
        "/v1/compress",
        headers={"Authorization": "Bearer cmp-test-key"},
        json={
            "model": "bear-2",
            "input": "Prompts are code.",
            "compression_settings": {"aggressiveness": 0.4},
        },
    )

    assert response.status_code == 200
    assert response.json() == {
        "output": "Prompts code.",
        "output_tokens": 2,
        "input_tokens": 4,
        "original_input_tokens": 4,
        "tokens_saved": 2,
        "compression_ratio": 2.0,
        "token_estimator": "regex:unicode-word-or-non-space",
        "downstream_estimated_input_tokens": 4,
        "downstream_estimated_output_tokens": 3,
        "downstream_token_estimator": "regex:unicode-word-or-non-space",
        "compression_time": 12.5,
        "tenant_id": "default",
        "compression_profile": "default:base",
        "compression_profile_source": "default",
        "training_sample_recorded": False,
        "warnings": [],
    }
    assert service.last_aggressiveness == 0.4


@pytest.mark.parametrize(
    ("path", "payload"),
    [
        (
            "/compress",
            {
                "text": "Prompts are code.",
                "customerId": "customer_attacker",
            },
        ),
        (
            "/v1/compress",
            {
                "input": "Prompts are code.",
                "customerId": "customer_attacker",
            },
        ),
        (
            "/v1/messages/compress",
            {
                "messages": [{"role": "user", "content": "Prompts are code."}],
                "customerId": "customer_attacker",
            },
        ),
    ],
)
def test_http_compression_routes_meter_only_verified_customer(
    monkeypatch,
    use_fake_usagetap_authorization,
    path,
    payload,
):
    service = FakeCompressionService()
    monkeypatch.setattr(main, "compression_service", service)

    response = TestClient(main.app).post(
        path,
        headers={"Authorization": "Bearer cmp-test-key"},
        json=payload,
    )

    assert response.status_code == 200
    assert len(use_fake_usagetap_authorization.calls) == 1
    metering_call = use_fake_usagetap_authorization.calls[0]
    assert metering_call["customer_id"] == "customer_test"
    assert metering_call["customer_id"] != "customer_attacker"
    assert len(metering_call["operation_id"]) == 32
    assert metering_call["input_tokens"] >= metering_call["output_tokens"]


def test_remote_authorization_runs_concurrently_with_compression(monkeypatch):
    compression_started = Event()
    authorization_release = Event()

    class BlockingAuthorizationClient(FakeUsageTapAuthorizationClient):
        def authorize(
            self,
            authorization_header: str | None,
        ) -> UsageTapAuthorization:
            assert compression_started.wait(timeout=2)
            assert authorization_release.wait(timeout=2)
            return super().authorize(authorization_header)

    class SignalingCompressionService(FakeCompressionService):
        def compress(self, *args, **kwargs):
            compression_started.set()
            return super().compress(*args, **kwargs)

    monkeypatch.setattr(main, "compression_service", SignalingCompressionService())
    monkeypatch.setattr(
        main,
        "usage_tap_authorization_client",
        BlockingAuthorizationClient(),
    )

    def send_request():
        return TestClient(main.app).post(
            "/compress",
            headers={"Authorization": "Bearer cmp-test-key"},
            json={"text": "Prompts are code."},
        )

    with ThreadPoolExecutor(max_workers=1) as executor:
        response_future = executor.submit(send_request)
        assert compression_started.wait(timeout=2)
        assert response_future.done() is False
        authorization_release.set()
        response = response_future.result(timeout=2)

    assert response.status_code == 200


def test_metering_failure_withholds_compression_response(monkeypatch):
    class FailingMeteringClient:
        def record_compression_savings(self, **kwargs):
            raise UsageTapMeteringError()

    service = FakeCompressionService()
    monkeypatch.setattr(main, "compression_service", service)
    monkeypatch.setattr(main, "usage_tap_metering_client", FailingMeteringClient())

    response = TestClient(main.app).post(
        "/compress",
        headers={"Authorization": "Bearer cmp-test-key"},
        json={"text": "Prompts are code."},
    )

    assert response.status_code == 503
    assert service.calls == [("Prompts are code.", main.DEFAULT_AGGRESSIVENESS, False)]
    assert "meter-platform-secret" not in response.text


def test_local_response_cache_reuses_identical_http_request_and_meters_each_hit(
    monkeypatch,
    use_fake_usagetap_authorization,
):
    service = FakeCompressionService()
    monkeypatch.setattr(main, "compression_service", service)
    client = TestClient(main.app)
    payload = {
        "text": "Prompts are code.",
        "aggressiveness": 0.3,
        "mode": "model_force",
        "include_sections": True,
        "include_diagnostics": False,
        "include_detailed_analytics": False,
    }

    first = client.post(
        "/compress",
        headers={
            "Authorization": "Bearer cmp-test-key",
            "X-Request-ID": "first-request",
        },
        json=payload,
    )
    second = client.post(
        "/compress",
        headers={
            "Authorization": "Bearer cmp-test-key",
            "X-Request-ID": "second-request",
        },
        json=payload,
    )

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.headers["x-compression-cache"] == "store"
    assert second.headers["x-compression-cache"] == "hit"
    assert service.calls == [("Prompts are code.", 0.3, True)]
    assert len(use_fake_usagetap_authorization.calls) == 2


def test_local_response_cache_separates_output_affecting_settings(monkeypatch):
    service = FakeCompressionService()
    monkeypatch.setattr(main, "compression_service", service)
    client = TestClient(main.app)
    headers = {"Authorization": "Bearer cmp-test-key"}
    variants = [
        {"aggressiveness": 0.2, "mode": "model_force"},
        {"aggressiveness": 0.3, "mode": "model_force"},
        {
            "aggressiveness": 0.3,
            "mode": "model_auto",
            "latency_budget_ms": 500,
        },
        {
            "aggressiveness": 0.3,
            "mode": "model_auto",
            "latency_budget_ms": 750,
        },
    ]

    for variant in variants:
        response = client.post(
            "/compress",
            headers=headers,
            json={"text": "Prompts are code.", **variant},
        )
        assert response.status_code == 200
        assert response.headers["x-compression-cache"] == "store"

    assert len(service.calls) == len(variants)


def test_local_response_cache_bypasses_diagnostics(monkeypatch):
    service = FakeCompressionService()
    monkeypatch.setattr(main, "compression_service", service)
    client = TestClient(main.app)
    payload = {
        "text": "Prompts are code.",
        "include_diagnostics": True,
        "include_detailed_analytics": False,
    }

    first = client.post(
        "/compress",
        headers={"Authorization": "Bearer cmp-test-key"},
        json=payload,
    )
    second = client.post(
        "/compress",
        headers={"Authorization": "Bearer cmp-test-key"},
        json=payload,
    )

    assert first.headers["x-compression-cache"] == "bypass"
    assert second.headers["x-compression-cache"] == "bypass"
    assert len(service.calls) == 2


def test_metering_failure_does_not_commit_cache_entry(monkeypatch):
    class FailingMeteringClient:
        def record_compression_savings(self, **kwargs):
            raise UsageTapMeteringError()

    service = FakeCompressionService()
    monkeypatch.setattr(main, "compression_service", service)
    monkeypatch.setattr(main, "usage_tap_metering_client", FailingMeteringClient())
    client = TestClient(main.app)
    request_args = {
        "headers": {"Authorization": "Bearer cmp-test-key"},
        "json": {"text": "Prompts are code."},
    }

    first = client.post("/compress", **request_args)
    monkeypatch.setattr(main, "usage_tap_metering_client", FakeUsageTapMeteringClient())
    second = client.post("/compress", **request_args)

    assert first.status_code == 503
    assert second.status_code == 200
    assert second.headers["x-compression-cache"] == "store"
    assert len(service.calls) == 2


def test_token_estimate_endpoint_uses_compression_service_estimator(monkeypatch):
    class EstimatingCompressionService(FakeCompressionService):
        def estimate_compression_tokens(
            self,
            text: str,
            tenant_profile: TenantCompressionProfile | None = None,
        ) -> TokenEstimate:
            return TokenEstimate(
                count=len(text) + 1,
                estimator="fake-tokenizer",
                tokenizer_backed=True,
            )

    monkeypatch.setattr(main, "compression_service", EstimatingCompressionService())

    response = main.estimate_tokens(TokenEstimateRequest(text="abc"))

    assert response.tokens == 4
    assert response.token_estimator == "fake-tokenizer"
    assert response.tokenizer_backed is True


def test_v1_compress_accepts_tenant_id_header_and_profile_body(monkeypatch):
    service = FakeCompressionService()
    monkeypatch.setattr(main, "compression_service", service)
    client = TestClient(main.app)

    response = client.post(
        "/v1/compress",
        headers={
            "Authorization": "Bearer cmp-test-key",
            "X-Tenant-ID": "tenant_from_header",
        },
        json={
            "model": "bear-2",
            "input": "Prompts are code.",
            "tenant_profile": {
                "profile_id": "tenant_from_header:v2",
                "default_aggressiveness": 0.33,
                "force_keep_tokens": ["AcmeTerm"],
            },
        },
    )

    assert response.status_code == 200
    body = response.json()
    profile = service.last_tenant_profile
    assert profile is not None
    assert service.last_aggressiveness == 0.33
    assert profile.tenant_id == "tenant_from_header"
    assert profile.profile_id == "tenant_from_header:v2"
    assert profile.force_keep_tokens == ("AcmeTerm",)
    assert body["tenant_id"] == "tenant_from_header"
    assert body["compression_profile"] == "tenant_from_header:v2"
    assert body["compression_profile_source"] == "api"
    assert body["training_sample_recorded"] is False


def test_v1_messages_compress_only_compresses_user_text(monkeypatch):
    service = FakeCompressionService()
    monkeypatch.setattr(main, "compression_service", service)

    response = main.compress_v1_messages(
        V1MessagesCompressRequest(
            model="gpt-test",
            system="System stays.",
            temperature=0.2,
            messages=[
                {"role": "system", "content": "System stays."},
                {"role": "user", "content": "Prompts are code."},
                {"role": "tool", "content": "Tool stays."},
                {"role": "assistant", "content": "Assistant stays."},
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "Prompts are code."},
                        {"type": "image", "source": {"media_type": "image/png"}},
                    ],
                },
            ],
            compression_settings=V1CompressionSettings(aggressiveness=0.35),
        )
    )

    assert service.calls == [
        ("Prompts are code.", 0.35, False),
        ("Prompts are code.", 0.35, False),
    ]
    assert service.last_mode == "deterministic"
    assert response.messages == [
        {"role": "system", "content": "System stays."},
        {"role": "user", "content": "Prompts code."},
        {"role": "tool", "content": "Tool stays."},
        {"role": "assistant", "content": "Assistant stays."},
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "Prompts code."},
                {"type": "image", "source": {"media_type": "image/png"}},
            ],
        },
    ]
    assert response.compressed_request["system"] == "System stays."
    assert response.compressed_request["temperature"] == 0.2
    assert "compression_settings" not in response.compressed_request
    assert response.tenant_id == "default"
    assert response.compression_profile == "default:base"
    assert response.compression_profile_source == "default"
    assert response.training_sample_recorded is False
    assert response.input_tokens == 20
    assert response.output_tokens == 18
    assert response.tokens_saved == 2
    assert response.user_input_tokens == 8
    assert response.user_output_tokens == 6
    assert response.user_tokens_saved == 2
    assert response.non_user_tokens_preserved == 12
    assert response.message_stats[0].skipped_reason == "aggressiveness_zero"
    assert response.message_stats[1].compression_applied is True
    assert response.message_stats[1].compressed is True
    assert response.message_stats[2].skipped_reason == "aggressiveness_zero"
    assert response.message_stats[4].text_parts == 1


def test_v1_messages_compress_accepts_per_role_aggressiveness(monkeypatch):
    service = FakeCompressionService()
    monkeypatch.setattr(main, "compression_service", service)

    response = main.compress_v1_messages(
        V1MessagesCompressRequest(
            model="gpt-test",
            messages=[
                {"role": "system", "content": "System prompt text."},
                {"role": "user", "content": "Prompts are code."},
                {"role": "tool", "content": "Tool result text."},
                {"role": "assistant", "content": "Assistant stays."},
            ],
            compression_settings=V1CompressionSettings(
                aggressiveness={"system": 0.2, "user": 0.5, "tool": 0.8},
            ),
        )
    )

    assert service.calls == [
        ("System prompt text.", 0.2, False),
        ("Prompts are code.", 0.5, False),
        ("Tool result text.", 0.8, False),
    ]
    assert response.messages == [
        {"role": "system", "content": "Prompts code."},
        {"role": "user", "content": "Prompts code."},
        {"role": "tool", "content": "Prompts code."},
        {"role": "assistant", "content": "Assistant stays."},
    ]
    assert response.user_input_tokens == 4
    assert response.user_output_tokens == 3
    assert response.non_user_tokens_preserved == 3
    assert response.message_stats[0].compression_applied is True
    assert response.message_stats[2].compression_applied is True
    assert response.message_stats[3].skipped_reason == "role_preserved"


def test_v1_messages_compress_http_rejects_invalid_role_aggressiveness(monkeypatch):
    service = FakeCompressionService()
    monkeypatch.setattr(main, "compression_service", service)
    client = TestClient(main.app)

    response = client.post(
        "/v1/messages/compress",
        headers={"Authorization": "Bearer cmp-test-key"},
        json={
            "model": "gpt-test",
            "messages": [{"role": "user", "content": "Prompts are code."}],
            "compression_settings": {"aggressiveness": {"user": 1.2}},
        },
    )

    assert response.status_code == 422
    assert service.calls == []


def test_v1_messages_compress_skips_user_messages_without_text(monkeypatch):
    service = FakeCompressionService()
    monkeypatch.setattr(main, "compression_service", service)

    response = main.compress_v1_messages(
        V1MessagesCompressRequest(
            model="gpt-test",
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "image", "source": {"media_type": "image/png"}},
                    ],
                }
            ],
        )
    )

    assert service.calls == []
    assert response.messages == [
        {
            "role": "user",
            "content": [
                {"type": "image", "source": {"media_type": "image/png"}},
            ],
        }
    ]
    assert response.message_stats[0].skipped_reason == "no_text_content"


def test_v1_messages_compacts_empty_user_messages_when_enabled(monkeypatch):
    service = FakeCompressionService()
    monkeypatch.setattr(main, "compression_service", service)

    response = main.compress_v1_messages(
        V1MessagesCompressRequest(
            model="gpt-test",
            messages=[
                {"role": "user", "content": ""},
                {"role": "user", "content": "Prompts are code."},
            ],
            compression_settings=V1CompressionSettings(
                compact_empty_user_messages=True,
            ),
        )
    )

    assert service.calls == [("Prompts are code.", 0.15, False)]
    assert response.messages == [
        {"role": "user", "content": "Prompts code."},
    ]
    assert response.message_stats[0].skipped_reason == "empty_user_message_dropped"
    assert response.message_stats[1].compression_applied is True


def test_v1_messages_compacts_duplicate_user_text_parts_when_enabled(monkeypatch):
    service = FakeCompressionService()
    monkeypatch.setattr(main, "compression_service", service)

    response = main.compress_v1_messages(
        V1MessagesCompressRequest(
            model="gpt-test",
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "Prompts are code."},
                        {"type": "text", "text": "Prompts are code."},
                        {"type": "image", "source": {"media_type": "image/png"}},
                    ],
                },
                {"role": "user", "content": "Prompts are code."},
            ],
            compression_settings=V1CompressionSettings(
                compact_duplicate_user_text_parts=True,
            ),
        )
    )

    assert service.calls == [("Prompts are code.", 0.15, False)]
    assert response.messages == [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "Prompts code."},
                {"type": "image", "source": {"media_type": "image/png"}},
            ],
        },
    ]
    assert response.message_stats[0].skipped_reason == (
        "duplicate_user_text_part_dropped"
    )
    assert response.message_stats[1].skipped_reason == "duplicate_user_text_dropped"


def test_v1_messages_preserves_empty_and_duplicate_user_content_by_default(monkeypatch):
    service = FakeCompressionService()
    monkeypatch.setattr(main, "compression_service", service)

    response = main.compress_v1_messages(
        V1MessagesCompressRequest(
            model="gpt-test",
            messages=[
                {"role": "user", "content": ""},
                {"role": "user", "content": "Prompts are code."},
                {"role": "user", "content": "Prompts are code."},
            ],
        )
    )

    assert service.calls == [
        ("Prompts are code.", 0.15, False),
        ("Prompts are code.", 0.15, False),
    ]
    assert response.messages == [
        {"role": "user", "content": ""},
        {"role": "user", "content": "Prompts code."},
        {"role": "user", "content": "Prompts code."},
    ]


def test_v1_messages_compress_applies_tenant_profile_without_forwarding_it(monkeypatch):
    service = FakeCompressionService()
    monkeypatch.setattr(main, "compression_service", service)

    response = main.compress_v1_messages(
        V1MessagesCompressRequest(
            tenant_id="tenant_body",
            tenant_profile=TenantCompressionSettings(
                profile_id="tenant_body:v1",
                default_aggressiveness=0.28,
                force_keep_tokens=["ContractTerm"],
            ),
            model="gpt-test",
            messages=[
                {"role": "user", "content": "Prompts are code."},
            ],
        )
    )

    profile = service.last_tenant_profile
    assert profile is not None
    assert service.last_aggressiveness == 0.28
    assert profile.tenant_id == "tenant_body"
    assert profile.force_keep_tokens == ("ContractTerm",)
    assert response.tenant_id == "tenant_body"
    assert response.compression_profile == "tenant_body:v1"
    assert response.compression_profile_source == "api"
    assert response.training_sample_recorded is False
    assert "tenant_id" not in response.compressed_request
    assert "tenant_profile" not in response.compressed_request
    assert "compression_settings" not in response.compressed_request


def test_v1_messages_compress_http_accepts_vendor_style_request(monkeypatch):
    service = FakeCompressionService()
    monkeypatch.setattr(main, "compression_service", service)
    client = TestClient(main.app)

    response = client.post(
        "/v1/messages/compress",
        headers={"Authorization": "Bearer cmp-test-key"},
        json={
            "model": "gpt-test",
            "messages": [
                {"role": "developer", "content": "Developer stays."},
                {"role": "user", "content": "Prompts are code."},
            ],
            "compression_settings": {"aggressiveness": 0.4},
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["messages"] == [
        {"role": "developer", "content": "Developer stays."},
        {"role": "user", "content": "Prompts code."},
    ]
    assert body["user_tokens_saved"] == 1
    assert service.calls == [("Prompts are code.", 0.4, False)]
