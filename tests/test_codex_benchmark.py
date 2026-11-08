"""Exercise the runner with an offline fake CLI; no subscription/model calls."""

import json
import sys
import zipfile
from pathlib import Path

import pytest

from scripts import benchmark_sources as sources
from scripts import codex_benchmark as bench
from scripts.benchmark_scoring import qa_f1, score_answer


def case(**changes):
    return {
        "id": "test/location",
        "dataset": "test/qa",
        "context": "A workshop takes place in London.",
        "question": "Which city?",
        "answers": ["London"],
        "metric": "qa_f1",
        **changes,
    }


def save_input(path, cases):
    path.write_text("".join(json.dumps(c) + "\n" for c in cases), encoding="utf-8")


@pytest.fixture
def prepared(tmp_path, monkeypatch):
    calls = []

    def compress(self, text, condition):
        calls.append((text, condition))
        return text.replace("A workshop takes place in", "Workshop:"), {
            "client_elapsed_ms": 3.0,
            "compression_path": "unchanged",
        }

    monkeypatch.setattr(bench.Compressor, "__call__", compress)
    path = tmp_path / "cases.jsonl"
    save_input(path, [case()])
    out = tmp_path / "prepared"
    args = bench.parser().parse_args(
        ["prepare", "--cases", str(path), "--out", str(out)]
    )
    bench.prepare(args)
    return out / "manifest.json", calls, args


def test_rendering_never_interpolates_reference_or_metadata():
    sample = case(answers=["SECRET_REFERENCE"], source={"answer": "SECRET_SOURCE"})
    prompt = bench.render_prompt(sample, sample["context"])
    assert "SECRET_REFERENCE" not in prompt
    assert "SECRET_SOURCE" not in prompt
    assert "Which city?" in prompt
    assert "Do not use tools" in prompt


@pytest.mark.parametrize(
    "template",
    [
        "{context} {answers}",
        "{context} {context}",
        "{context.__class__}",
        "{context!r}",
        "{context:10}",
    ],
)
def test_rejects_unsafe_or_ambiguous_templates(template):
    with pytest.raises(ValueError):
        sources.validate_cases([case(prompt_template=template)])


def test_preparation_is_frozen_and_resumes_without_recompressing(prepared):
    manifest_path, calls, args = prepared
    original = manifest_path.read_bytes()
    assert len(calls) == 2
    bench.prepare(args)
    assert len(calls) == 2
    assert manifest_path.read_bytes() == original
    args.aggressiveness = 0.3
    with pytest.raises(ValueError, match="changed"):
        bench.prepare(args)


def test_tampered_manifest_is_rejected(prepared):
    manifest_path, _, _ = prepared
    manifest = json.loads(manifest_path.read_text())
    manifest["cases"][0]["variants"][0]["prompt"] = "tampered"
    sources.write_json(manifest_path, manifest)
    with pytest.raises(ValueError, match="hash"):
        bench.load_manifest(manifest_path)


def test_json_has_minified_baseline_and_compressor_only_gets_authorizing_tag(
    tmp_path, monkeypatch
):
    observed = []

    def compress(self, text, condition):
        observed.append(text)
        return '{"owner":"Ada"}', {"client_elapsed_ms": 1.0}

    monkeypatch.setattr(bench.Compressor, "__call__", compress)
    path = tmp_path / "cases.jsonl"
    save_input(path, [case(context='{\n  "owner": "Ada"\n}', context_format="json")])
    out = tmp_path / "prepared"
    bench.prepare(
        bench.parser().parse_args(["prepare", "--cases", str(path), "--out", str(out)])
    )
    variants = bench.load_manifest(out / "manifest.json")["cases"][0]["variants"]
    assert [v["condition"] for v in variants] == list(bench.CONDITIONS)
    assert variants[1]["context"] == '{"owner":"Ada"}'
    assert all("<compress-json>" in text for text in observed)
    assert all("<compress-json>" not in variant["prompt"] for variant in variants)


def completed(usage=None):
    return {
        "type": "turn.completed",
        "usage": usage
        or {
            "input_tokens": 100,
            "cached_input_tokens": 20,
            "output_tokens": 3,
        },
    }


def events(*items):
    return "\n".join(json.dumps(item) for item in items)


def test_event_usage_and_no_false_rate_limit_from_answer():
    result = bench.parse_events(
        events(
            {
                "type": "item.completed",
                "item": {"type": "agent_message", "text": "Rate limit"},
            },
            completed(),
        ),
        "",
        0,
    )
    assert result["status"] == "ok"
    assert result["usage"]["cached_input_tokens"] == 20


def test_cli_item_error_is_not_misclassified_as_a_tool_call():
    result = bench.parse_events(
        events(
            {
                "type": "item.completed",
                "item": {"type": "error", "message": "Model unavailable"},
            },
            {"type": "turn.failed", "error": {"message": "Model unavailable"}},
        ),
        "",
        1,
    )
    assert result["status"] == "invalid"
    assert result["tool_types"] == []
    assert "Model unavailable" in result["errors"]


def test_cache_write_usage_is_retained_without_being_added_to_total_input():
    usage = {
        "input_tokens": 100,
        "cached_input_tokens": 20,
        "output_tokens": 5,
        "cache_write_input_tokens": 30,
    }
    result = bench.parse_events(events(completed(usage)), "", 0)
    assert result["status"] == "ok"
    assert result["usage"]["input_tokens"] == 100
    assert result["usage"]["cache_write_input_tokens"] == 30


@pytest.mark.parametrize(
    "item_type",
    [
        "command_execution",
        "web_search",
        "mcp_tool_call",
        "file_change",
        "context_compaction",
        "future_tool",
    ],
)
def test_tool_or_compaction_invalidates_answer(item_type):
    result = bench.parse_events(
        events(
            {"type": "item.started", "item": {"type": item_type}},
            completed(),
        ),
        "",
        0,
    )
    assert result["status"] == "invalid"
    assert result["tool_types"] == [item_type]


def test_rate_limit_event_pauses():
    result = bench.parse_events(
        events({"type": "error", "message": "You hit your usage limit"}), "", 1
    )
    assert result["status"] == "rate_limited"


@pytest.mark.parametrize(
    "output",
    [
        "invalid-json",
        events(completed(), completed()),
        events({"type": "unrecognized"}, completed()),
        events(completed({"input_tokens": 100})),
        events(
            completed({"input_tokens": 1, "cached_input_tokens": 2, "output_tokens": 1})
        ),
    ],
)
def test_malformed_or_incomplete_stream_is_rejected(output):
    assert bench.parse_events(output, "", 0)["status"] == "invalid"


def test_child_does_not_receive_api_credentials(monkeypatch):
    for name in bench.SECRET_ENV:
        monkeypatch.setenv(name, "do-not-forward")
    monkeypatch.setenv("BENCHMARK_VISIBLE", "yes")
    monkeypatch.setenv("COMPRESSION_API_KEY", "compression-secret")
    env = bench.child_environment()
    assert not bench.SECRET_ENV.intersection(env)
    assert "COMPRESSION_API_KEY" not in env
    assert env["BENCHMARK_VISIBLE"] == "yes"


def test_official_style_scoring_multiple_references_and_rouge():
    assert qa_f1("The London!", "London") == 1
    assert qa_f1("Paris", "London") == 0
    assert score_answer(case(answers=["Paris", "London"]), "London") == 1
    assert score_answer(
        case(metric="rouge_l", answers=["alpha beta gamma"]), "alpha beta gamma"
    ) == pytest.approx(1)
    assert score_answer(case(metric="rouge_l"), "") == 0


def fake_cli(tmp_path, monkeypatch):
    script = tmp_path / "fake_cli.py"
    script.write_text(
        "import json, pathlib, sys\n"
        "prompt=sys.stdin.read()\n"
        "target=pathlib.Path(sys.argv[1])\n"
        "target.write_text('London', encoding='utf-8')\n"
        "target.with_name('probe.json').write_text(json.dumps({"
        "'cwd':str(pathlib.Path.cwd()),'files':list(map(str,pathlib.Path.cwd().iterdir())),"
        "'prompt':prompt}))\n"
        "print(json.dumps({'type':'turn.completed','usage':{"
        "'input_tokens':100,'cached_input_tokens':20,'output_tokens':3}}))\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(bench, "resolve_codex", lambda name: sys.executable)
    monkeypatch.setattr(bench, "preflight", lambda executable, env: "fake-cli 1")
    monkeypatch.setattr(
        bench,
        "codex_command",
        lambda executable, args, answer: [sys.executable, str(script), str(answer)],
    )


def run_args(manifest, out, *extra):
    return bench.parser().parse_args(
        [
            "run",
            "--manifest",
            str(manifest),
            "--out",
            str(out),
            "--model",
            "test-model",
            *extra,
        ]
    )


def test_subprocess_execution_resume_and_complete_paired_report(
    prepared, tmp_path, monkeypatch
):
    manifest, _, _ = prepared
    fake_cli(tmp_path, monkeypatch)
    out = tmp_path / "run"
    bench.run(run_args(manifest, out, "--max-calls", "1"))
    assert len(bench.load_attempts(out / "attempts.jsonl")) == 1
    bench.run(run_args(manifest, out))
    attempts = bench.load_attempts(out / "attempts.jsonl")
    assert len(attempts) == 3
    bench.run(run_args(manifest, out))
    assert len(bench.load_attempts(out / "attempts.jsonl")) == 3
    summary = json.loads((out / "summary.json").read_text())
    assert summary["status_counts"] == {"ok": 3}
    assert len(summary["by_dataset_condition"]) == 3
    assert all(
        row["score_delta_points"] == 0 for row in summary["by_dataset_condition"]
    )
    assert summary["all_attempt_usage"]["input_tokens"] == 300
    for probe in (out / "calls").glob("*/attempt-*/probe.json"):
        observed = json.loads(probe.read_text())
        assert observed["files"] == []
        assert not Path(observed["cwd"]).exists()
    with pytest.raises(ValueError, match="changed"):
        bench.run(run_args(manifest, out, "--reasoning-effort", "high"))


def test_failed_call_requires_explicit_retry(prepared, tmp_path, monkeypatch):
    manifest, _, _ = prepared
    fake_cli(tmp_path, monkeypatch)
    out = tmp_path / "run"
    original = bench.execute_job
    monkeypatch.setattr(
        bench,
        "execute_job",
        lambda *a: {
            "status": "rate_limited",
            "score": None,
            "usage": {},
        },
    )
    with pytest.raises(ValueError, match="paused"):
        bench.run(run_args(manifest, out))
    monkeypatch.setattr(bench, "execute_job", original)
    bench.run(run_args(manifest, out))
    assert json.loads((out / "summary.json").read_text())["status_counts"] == {
        "ok": 2,
        "rate_limited": 1,
    }
    bench.run(run_args(manifest, out, "--retry-failed"))
    attempts = bench.load_attempts(out / "attempts.jsonl")
    assert len(attempts) == 4
    assert json.loads((out / "summary.json").read_text())["status_counts"] == {"ok": 3}


def test_token_limit_rejects_before_any_cli_call(prepared, tmp_path, monkeypatch):
    manifest, _, _ = prepared
    monkeypatch.setattr(
        bench, "preflight", lambda *args: pytest.fail("must not reach CLI")
    )
    with pytest.raises(ValueError, match="exceeds"):
        bench.run(run_args(manifest, tmp_path / "run", "--max-prompt-tokens", "1"))


def test_recover_completed_or_interrupted_calls_without_replaying(tmp_path):
    log = tmp_path / "attempts.jsonl"
    for index in (1, 2):
        artifact = tmp_path / "calls" / f"job{index}" / "attempt-1"
        job = {"job_id": f"job{index}", "attempt": 1}
        sources.write_json(artifact / "job.json", job)
        if index == 1:
            sources.write_json(
                artifact / "result.json", {**job, "status": "ok", "score": 1}
            )
    attempts = []
    bench.recover_attempts(tmp_path, attempts)
    assert [item["status"] for item in attempts] == ["ok", "interrupted"]
    bench.recover_attempts(tmp_path, attempts)
    assert len(bench.load_attempts(log)) == 2


def test_partial_final_log_line_recovery_and_middle_corruption(tmp_path):
    path = tmp_path / "attempts.jsonl"
    path.write_bytes(b'{"job_id":"ok"}\n{"job_')
    assert bench.load_attempts(path) == [{"job_id": "ok"}]
    assert path.read_bytes().endswith(b"\n")
    path.write_bytes(b'{"job_id":"ok"}')
    bench.load_attempts(path)
    assert path.read_bytes().endswith(b"\n")
    path.write_bytes(b'invalid\n{"job_id":"ok"}\n')
    with pytest.raises(ValueError, match="Corrupt"):
        bench.load_attempts(path)


def test_import_longbench_freezes_revision_templates_and_sample(tmp_path, monkeypatch):
    revision = "a" * 40
    archive_path = tmp_path / f"longbench-{revision}.zip"
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr(
            "data/qasper.jsonl",
            "\n".join(
                json.dumps(
                    {
                        "_id": str(i),
                        "context": "Article context",
                        "input": "Question?",
                        "answers": ["SECRET_REFERENCE"],
                    }
                )
                for i in range(4)
            ),
        )

    class Response:
        text = '{"qasper":"Article: {context} Question: {input}"}'

        def raise_for_status(self):
            pass

        def json(self):
            return (
                {"sha": revision} if self.kind == "revision" else json.loads(self.text)
            )

    def get(url, **kwargs):
        result = Response()
        result.kind = "revision" if "api/datasets" in url else "prompt"
        return result

    monkeypatch.setattr(sources.requests, "get", get)
    args = bench.parser().parse_args(
        [
            "import-longbench",
            "--datasets",
            "qasper",
            "--per-dataset",
            "2",
            "--cache-dir",
            str(tmp_path),
            "--out",
            str(tmp_path / "cases.jsonl"),
        ]
    )
    sources.import_longbench(args)
    cases = sources.read_jsonl(args.out)
    assert len(cases) == 2
    assert cases[0]["source"]["revision"] == revision
    assert cases[0]["metric"] == "qa_f1"
    assert "SECRET_REFERENCE" not in bench.render_prompt(cases[0], cases[0]["context"])
    sources.validate_cases(cases)


def test_sampling_is_independent_of_source_order():
    cases = [case(id=str(i), dataset=f"group{i % 2}") for i in range(12)]
    assert sources.sample_cases(cases, 2, 42) == sources.sample_cases(
        list(reversed(cases)), 2, 42
    )


def test_http_compression_fails_closed_without_leaking_credential(
    tmp_path, monkeypatch
):
    args = bench.parser().parse_args(
        [
            "prepare",
            "--cases",
            "unused",
            "--out",
            str(tmp_path),
            "--compress-url",
            "http://localhost:8000/compress",
        ]
    )
    monkeypatch.setenv("COMPRESSION_API_KEY", "secret-do-not-print")
    captured = {}

    def post(url, **kwargs):
        captured.update(kwargs)
        return type("Response", (), {"status_code": 402})()

    monkeypatch.setattr(sources.requests, "post", post)
    with pytest.raises(ValueError, match="HTTP 402") as exc:
        bench.Compressor(args)("context", "full")
    assert "secret-do-not-print" not in str(exc.value)
    assert captured["headers"]["Authorization"] == "Bearer secret-do-not-print"
    assert captured["json"]["mode"] == "model_auto"


def test_codex_command_forces_subscription_and_disables_tools(tmp_path):
    args = run_args(tmp_path / "manifest", tmp_path / "run")
    command = bench.codex_command("codex", args, tmp_path / "answer")
    assert 'forced_login_method="chatgpt"' in command
    assert "features.shell_tool=false" in command
    assert "--ignore-user-config" in command
    assert "--ephemeral" in command
    assert command[-1] == "-"


def test_arbitrary_embedded_toon_code_is_rejected():
    with pytest.raises(ValueError, match="official normalizer"):
        sources.validate_cases(
            [case(metric="toon", normalizer_source="malicious code")]
        )


def test_minified_baseline_preserves_numeric_precision_and_string_escapes():
    source = (
        ' { "amount": 0.123456789123456789, "large": 1e1000, "label": "a  b\\" c" } '
    )
    assert (
        bench.minify_json(source)
        == '{"amount":0.123456789123456789,"large":1e1000,"label":"a  b\\" c"}'
    )


@pytest.mark.parametrize(
    "source", ['{"id":1,"id":2}', '{"value":NaN}', '{"value":Infinity}']
)
def test_minified_baseline_rejects_ambiguous_or_nonfinite_json(source):
    with pytest.raises(ValueError):
        bench.minify_json(source)


@pytest.mark.parametrize(
    "event",
    [
        {"type": []},
        {"type": "item.completed", "item": []},
        {"type": "item.completed", "item": {"type": []}},
    ],
)
def test_invalid_event_shapes_are_reported_without_crashing(event):
    assert bench.parse_events(events(event, completed()), "", 0)["status"] == "invalid"


def test_timeout_stops_process_tree_and_records_failure(
    prepared, tmp_path, monkeypatch
):
    import subprocess

    manifest_path, _, _ = prepared
    record = bench.load_manifest(manifest_path)["cases"][0]
    variant = record["variants"][0]
    artifact = tmp_path / "timeout"
    artifact.mkdir()
    killed = []

    class Process:
        pid = 123
        returncode = -1

        def communicate(self, *args, **kwargs):
            raise subprocess.TimeoutExpired("fake codex", 1)

    monkeypatch.setattr(bench.subprocess, "Popen", lambda *args, **kwargs: Process())
    monkeypatch.setattr(
        bench, "terminate_process_tree", lambda process: killed.append(process.pid)
    )
    result = bench.execute_job(
        run_args(manifest_path, tmp_path / "run"),
        "fake-codex",
        {},
        record,
        variant,
        1,
        artifact,
    )
    assert killed == [123]
    assert result["status"] == "timeout"
    assert (artifact / "events.jsonl").exists()


def test_dataset_cannot_mix_metrics_under_one_label():
    with pytest.raises(ValueError, match="separate dataset labels"):
        sources.validate_cases([case(), case(id="test/summary", metric="rouge_l")])


def test_public_compressor_shortcut_and_endpoint_exclusion(tmp_path):
    args = bench.parser().parse_args(
        [
            "prepare",
            "--cases",
            "unused",
            "--out",
            str(tmp_path),
            "--public-compressor",
            "--demo-session",
        ]
    )
    assert args.compress_url == "https://compress.usagetap.com/compress"
    assert args.demo_session
    with pytest.raises(SystemExit):
        bench.parser().parse_args(
            [
                "prepare",
                "--cases",
                "unused",
                "--out",
                str(tmp_path),
                "--public-compressor",
                "--compress-url",
                "https://other.example/compress",
            ]
        )


def demo_body(**changes):
    import time

    return {
        "token": "demo-v1.private.signature",
        "expiresAt": int(time.time()) + 600,
        "maxOperations": 5,
        "maxInputChars": 50000,
        "maxInputCharsPerOperation": 20000,
        "dailyOperationsRemaining": 25,
        "dailyInputCharsRemaining": 100000,
        **changes,
    }


def test_demo_session_is_reused_and_credential_stays_out_of_results(
    tmp_path, monkeypatch
):
    args = bench.parser().parse_args(
        [
            "prepare",
            "--cases",
            "unused",
            "--out",
            str(tmp_path),
            "--public-compressor",
            "--demo-session",
        ]
    )
    requests = []

    def post(url, **kwargs):
        requests.append((url, kwargs))
        body = (
            demo_body()
            if url.endswith("/demo/session")
            else {"compressed_text": "small"}
        )
        return type("Response", (), {"status_code": 200, "json": lambda self: body})()

    monkeypatch.setattr(sources.requests, "post", post)
    compressor = bench.Compressor(args)
    compressor.start_demo(["a long context", "a long context"])
    for condition in ("deterministic_only", "full"):
        output, details = compressor("a long context", condition)
        assert output == "small"
        assert "demo-v1.private.signature" not in json.dumps(details)
    assert len(requests) == 3
    assert requests[0][0] == "https://compress.usagetap.com/demo/session"
    assert all(
        request[1]["headers"]["Authorization"] == "Bearer demo-v1.private.signature"
        for request in requests[1:]
    )
    assert all(request[1]["allow_redirects"] is False for request in requests)


@pytest.mark.parametrize(
    "limits,contexts",
    [
        ({"maxInputCharsPerOperation": 2}, ["long"]),
        ({"maxOperations": 1}, ["one", "two"]),
        ({"maxInputChars": 4}, ["one", "two"]),
        ({"dailyOperationsRemaining": 1}, ["one", "two"]),
        ({"dailyInputCharsRemaining": 4}, ["one", "two"]),
    ],
)
def test_demo_budget_rejected_before_compression(
    tmp_path, monkeypatch, limits, contexts
):
    args = bench.parser().parse_args(
        [
            "prepare",
            "--cases",
            "unused",
            "--out",
            str(tmp_path),
            "--public-compressor",
            "--demo-session",
        ]
    )
    seen = []

    def post(url, **kwargs):
        seen.append(url)
        return type(
            "Response",
            (),
            {"status_code": 200, "json": lambda self: demo_body(**limits)},
        )()

    monkeypatch.setattr(sources.requests, "post", post)
    with pytest.raises(ValueError, match="demo"):
        bench.Compressor(args).start_demo(contexts)
    assert seen == ["https://compress.usagetap.com/demo/session"]


def test_demo_exhaustion_does_not_acquire_another_session(tmp_path, monkeypatch):
    args = bench.parser().parse_args(
        [
            "prepare",
            "--cases",
            "unused",
            "--out",
            str(tmp_path),
            "--public-compressor",
            "--demo-session",
        ]
    )
    calls = []

    def post(url, **kwargs):
        calls.append(url)
        body = (
            demo_body(maxOperations=1)
            if url.endswith("/demo/session")
            else {"compressed_text": "small"}
        )
        return type("Response", (), {"status_code": 200, "json": lambda self: body})()

    monkeypatch.setattr(sources.requests, "post", post)
    compressor = bench.Compressor(args)
    compressor("context", "full")
    with pytest.raises(ValueError, match="allowance exhausted"):
        compressor("context", "full")
    assert len(calls) == 2


@pytest.mark.parametrize(
    "url",
    [
        "/compress",
        "ftp://host/compress",
        "https://user:secret@host/compress",
        "https://host/compress?key=secret",
        "https://host/compress#secret",
    ],
)
def test_compressor_url_rejects_credentials_and_unsupported_schemes(url):
    with pytest.raises(ValueError):
        bench.validate_compress_url(url)


@pytest.mark.skipif(sys.platform != "win32", reason="Windows process-tree integration")
def test_timeout_terminates_real_windows_child_tree(prepared, tmp_path, monkeypatch):
    import subprocess

    manifest_path, _, _ = prepared
    record = bench.load_manifest(manifest_path)["cases"][0]
    artifact = tmp_path / "real-timeout"
    artifact.mkdir()
    marker = artifact / "child.pid"
    script = tmp_path / "sleeping_parent.py"
    script.write_text(
        "import pathlib, subprocess, sys, time\n"
        "p=subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)'])\n"
        "pathlib.Path(sys.argv[1]).write_text(str(p.pid))\n"
        "time.sleep(60)\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        bench, "codex_command", lambda *args: [sys.executable, str(script), str(marker)]
    )
    args = run_args(manifest_path, tmp_path / "run", "--timeout", "1")
    result = bench.execute_job(
        args,
        "fake",
        bench.child_environment(),
        record,
        record["variants"][0],
        1,
        artifact,
    )
    assert result["status"] == "timeout"
    child_pid = int(marker.read_text())
    listing = subprocess.run(
        ["tasklist", "/FI", f"PID eq {child_pid}", "/FO", "CSV", "/NH"],
        capture_output=True,
        text=True,
        check=True,
    )
    assert f'"{child_pid}"' not in listing.stdout
