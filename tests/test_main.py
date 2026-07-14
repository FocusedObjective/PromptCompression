from fastapi.testclient import TestClient

from app import main
from app.compressor import (
    CompressionDiagnostics,
    CompressionOutputSection,
    CompressionResult,
    CompressionTiming,
    CompressionToken,
    build_token_savings,
)
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
        collect_diagnostics: bool = True,
        evaluate_disabled_transforms: bool = False,
        evaluation_constraints: dict[str, list[str]] | None = None,
        request_id: str | None = None,
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
        self.last_collect_diagnostics = collect_diagnostics
        self.last_evaluate_disabled_transforms = evaluate_disabled_transforms
        self.last_evaluation_constraints = evaluation_constraints
        self.last_request_id = request_id
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
    assert 'href="/research"' in body
    assert "Dropped Words Highlighted" in body
    assert "Diagnostic Logs" in body
    assert "JSON compressed to TOON" in body
    assert "Optional preserve controls" in body
    assert "Tenant Profile" in body
    assert 'id="tenantTestPreset"' in body
    assert 'id="compressionMode"' in body
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
    assert "tenant_rick_probe" in body
    assert 'id="tenantId"' in body
    assert 'id="tenantProfileId"' in body
    assert 'id="tenantForceKeepTokens"' in body
    assert 'id="tenantForceDropPhrases"' in body
    assert "buildTenantPayload" in body
    assert "&lt;nocompress&gt;...&lt;/nocompress&gt;" in body
    assert "markdown fences are protected from compression" in body
    assert "requestPayload.include_sections = true" in body
    assert "requestPayload.include_diagnostics = true" in body
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


def test_compress_request_rejects_unknown_experiment_profile():
    response = TestClient(main.app).post(
        "/compress",
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
    assert 'href="/research"' in body
    assert "/eval/run" in body


def test_benchmark_index_returns_benchmark_page():
    response = main.benchmark_index()
    body = response.body.decode()

    assert "Performance Benchmark" in body
    assert 'href="/eval"' in body
    assert "include_diagnostics" in body
    assert "Download Raw JSONL" in body
    assert "LLMLingua p50" in body
    assert "Model calls" in body
    assert "Gate skips" in body
    assert 'id="htmlRatiosInput"' in body
    assert "HTML ratios" in body
    assert "html_markdown" in body
    assert 'id="compressionModeInput"' in body
    assert '<option value="model_auto" selected>Model auto</option>' in body
    assert 'id="latencyBudgetInput"' in body
    assert 'id="allowCpuModelAutoInput" type="checkbox" checked' in body
    assert "mode: compressionModeInput.value" in body
    assert "payload.latency_budget_ms" in body
    assert "payload.allow_cpu_model_auto = true" in body
    assert "DIAGNOSTICS" in body
    assert "diagnosticLogFromResponse" in body


def test_research_index_returns_research_page():
    response = main.research_index()
    body = response.body.decode()

    assert "Prompt Compression Research" in body
    assert 'href="/benchmark"' in body
    assert "LLMLingua-2 BERT-base" in body
    assert "PCToolkit Assessment" in body
    assert "not as a production runtime dependency" in body
    assert "SCOPE: A Generative Approach" in body
    assert "Toolkit for Prompt Compression" in body
    assert "Hugging Face PEFT" in body


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
        )
    )

    assert service.last_mode == "model_auto"
    assert service.last_latency_budget_ms == 500.0
    assert service.last_allow_cpu_model_auto is True


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
    assert response.tenant_id == "tenant_123"
    assert response.compression_profile == "tenant_123:v1"
    assert response.compression_profile_source == "api"
    assert response.training_sample_recorded is False


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
        headers={"Authorization": "Bearer test-key"},
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
            "Authorization": "Bearer test-key",
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
        headers={"Authorization": "Bearer test-key"},
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
        headers={"Authorization": "Bearer test-key"},
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
