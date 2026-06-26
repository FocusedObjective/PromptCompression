from fastapi.testclient import TestClient

from app import main
from app.compressor import CompressionOutputSection, CompressionResult, CompressionToken
from app.eval_suite import EvalCase
from app.schemas import (
    CompressRequest,
    EvalRunRequest,
    V1CompressRequest,
    V1CompressionSettings,
)


class FakeCompressionService:
    model_name = "fake-model"
    is_loaded = True

    def compress(
        self,
        text: str,
        aggressiveness: float,
        include_sections: bool = True,
    ) -> CompressionResult:
        self.last_text = text
        self.last_aggressiveness = aggressiveness
        self.last_include_sections = include_sections
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
        )


def test_index_returns_prompt_compression_ui():
    response = main.index()
    body = response.body.decode()

    assert "Prompt Compression" in body
    assert "Eval Suite" in body
    assert "Dropped Words Highlighted" in body
    assert "JSON compressed to TOON" in body
    assert "Optional preserve controls" in body
    assert "&lt;nocompress&gt;...&lt;/nocompress&gt;" in body
    assert "markdown fences are protected from compression" in body
    assert "include_sections: true" in body


def test_index_http_allows_iframe_embedding():
    client = TestClient(main.app)

    response = client.get("/")

    assert response.status_code == 200
    assert response.headers["content-security-policy"] == "frame-ancestors *"
    assert "x-frame-options" not in response.headers


def test_eval_index_returns_eval_ui():
    response = main.eval_index()
    body = response.body.decode()

    assert "Prompt Compression Eval" in body
    assert "Run Selected" in body
    assert "/eval/run" in body


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
    assert [token.model_dump() for token in response.labeled_tokens] == []
    assert response.output_sections == []


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
    assert response.model_dump() == {
        "output": "Prompts code.",
        "output_tokens": 2,
        "input_tokens": 4,
        "original_input_tokens": 4,
        "tokens_saved": 2,
        "compression_ratio": 2.0,
        "compression_time": 12.5,
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
        "compression_time": 12.5,
        "warnings": [],
    }
    assert service.last_aggressiveness == 0.4
