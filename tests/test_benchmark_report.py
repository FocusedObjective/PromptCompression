import json
from html import unescape
from pathlib import Path

import pytest

from scripts.benchmark_costs import (
    api_cost,
    compression_meter,
    load_pricing,
    load_projection_prices,
    paired_costs,
    project_ledger,
)
from scripts.render_benchmark_report import pack_bytes_as_text, render_report, text_diff


PRICING_PATH = Path(__file__).resolve().parents[1] / "data/benchmark_pricing_terra.json"
PROJECTION_PATH = PRICING_PATH.with_name("benchmark_pricing_projections.json")


def test_projection_reprices_frozen_counts_and_preserves_service_meter():
    import copy

    catalog = load_projection_prices(PROJECTION_PATH)
    original = [{"case_id": "prose", "condition": "full", "repeat": 1,
                 "before_prompt_tokens": 4343, "after_prompt_tokens": 3238,
                 "before_output_tokens": 30, "after_output_tokens": 5,
                 "compression_meter_saved_tokens": 1308}]
    frozen = copy.deepcopy(original)
    rows = project_ledger(original, catalog["models"], 0.3)
    assert original == frozen
    assert len(rows) == 3
    for row, factor in zip(rows, (2, 10, 4)):
        assert row["before_input_usd"] == pytest.approx(4343 * factor / 1e6)
        assert row["before_output_usd"] == pytest.approx(30 * 5 * factor / 1e6)
        assert row["after_api_usd"] == pytest.approx((3238 + 25) * factor / 1e6)
        assert row["compression_usd"] == pytest.approx(1308 * 0.3 / 1e6)
        assert row["net_saving_usd"] == pytest.approx((4493 - 3263) * factor / 1e6 - 0.0003924)
    original[0]["compression_meter_saved_tokens"] = None
    assert all(row["after_including_compression_usd"] is None for row in project_ledger(original, catalog["models"], 0.3))


@pytest.mark.parametrize("mutation", ["negative", "nan", "duplicate", "undated", "unsourced"])
def test_invalid_projection_catalog_rejected(tmp_path, mutation):
    catalog = json.loads(PROJECTION_PATH.read_text())
    if mutation == "negative":
        catalog["models"][0]["input_per_million"] = -1
    elif mutation == "nan":
        catalog["models"][0]["output_per_million"] = float("nan")
    elif mutation == "duplicate":
        catalog["models"].append(catalog["models"][0])
    elif mutation == "undated":
        del catalog["as_of"]
    else:
        catalog["models"][0]["sources"] = []
    path = tmp_path / "projection-prices.json"
    path.write_text(json.dumps(catalog))
    with pytest.raises(ValueError):
        load_projection_prices(path)


def test_projection_csv_exports_every_pair_and_full_cost_components(tmp_path):
    data = {"cases": [], "experiment": {}, "ledger": [], "projection_ledger": project_ledger(
        [{"case_id": "json", "condition": "full", "repeat": 1,
          "before_prompt_tokens": 1000, "after_prompt_tokens": 500,
          "before_output_tokens": 10, "after_output_tokens": 10,
          "compression_meter_saved_tokens": 600}], load_projection_prices(PROJECTION_PATH)["models"], .3)}
    render_report(data, tmp_path / "report.html")
    import csv
    with (tmp_path / "report-model-projections.csv").open() as handle:
        rows = list(csv.DictReader(handle))
    assert len(rows) == 3
    assert rows[0]["model"] == "gpt-6.1-sol"
    assert float(rows[0]["before_total_usd"]) == pytest.approx(.0021)
    assert float(rows[0]["after_including_compression_usd"]) == pytest.approx(.00128)


@pytest.fixture
def prices():
    return load_pricing(PRICING_PATH, "gpt-6.1-terra")


def test_api_input_categories_are_disjoint_and_cache_write_is_not_additive(prices):
    cost = api_cost(
        12000,
        1000,
        prices,
        cached_tokens=4000,
        write_30m_tokens=3000,
        write_12h_tokens=4000,
    )
    assert cost["ordinary_input"] == pytest.approx(1000 / 1e6)
    assert cost["cached_input"] == pytest.approx(4000 * 0.05 / 1e6)
    assert cost["cache_write_30m"] == pytest.approx(3000 * 1.25 / 1e6)
    assert cost["cache_write_12h"] == pytest.approx(4000 * 2.5 / 1e6)
    assert cost["total"] == pytest.approx((1000 + 200 + 3750 + 10000 + 5000) / 1e6)


def test_long_context_threshold_applies_to_entire_request(prices):
    normal = api_cost(272000, 100, prices)
    long = api_cost(272001, 100, prices)
    assert normal["ordinary_input"] == pytest.approx(0.272)
    assert long["ordinary_input"] == pytest.approx(0.544002)
    assert long["output"] == pytest.approx(0.00075)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"cached_tokens": 101},
        {"cached_tokens": 60, "write_30m_tokens": 50},
        {"output_tokens": -1},
    ],
)
def test_invalid_usage_is_rejected(prices, kwargs):
    values = {"input_tokens": 100, "output_tokens": 0, **kwargs}
    with pytest.raises(ValueError):
        api_cost(**values, pricing=prices)


def test_measured_saved_token_meter_sets_fee_not_proxy_reduction(prices):
    cost = paired_costs(4343, 3238, 18, 5, 1308, prices)
    assert cost["compression"] == pytest.approx(1308 * 0.3 / 1e6)
    assert cost["before"]["total"] == pytest.approx(0.004433)
    assert cost["after_total"] == pytest.approx(0.003263 + 0.0003924)
    assert cost["net_saving"] == pytest.approx(0.004433 - 0.0036554)


def test_free_allowance_and_reused_artifacts_are_separate_scenarios(prices):
    cold = paired_costs(1000, 500, 10, 10, 600, prices)
    included = paired_costs(1000, 500, 10, 10, 600, prices, allowance=True)
    reused = paired_costs(1000, 500, 10, 10, 600, prices, reuse_count=3)
    assert included["compression"] == 0
    assert reused["compression"] == pytest.approx(cold["compression"] / 3)
    assert included["after"]["total"] == cold["after"]["total"]


def test_unknown_meter_does_not_become_a_free_compression_claim(prices):
    cost = paired_costs(1000, 500, 10, 10, None, prices)
    assert cost["compression"] is None
    assert cost["after_total"] is None
    assert cost["net_saving"] is None


def test_wrong_model_or_invalid_price_is_rejected(tmp_path):
    with pytest.raises(ValueError, match="match"):
        load_pricing(PRICING_PATH, "gpt-6-luna")
    data = json.loads(PRICING_PATH.read_text())
    data["input_per_million"] = -1
    path = tmp_path / "bad-pricing.json"
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="Invalid price"):
        load_pricing(path, "gpt-6.1-terra")


def test_meter_respects_returned_service_counts():
    variant = {
        "condition": "full",
        "compression": {"token_savings": {"total_tokens_saved": 1308}},
    }
    assert compression_meter(variant) == 1308
    assert compression_meter({"condition": "json_minified"}) == 0
    assert compression_meter({"condition": "full", "compression": {}}) is None


@pytest.mark.parametrize(
    "before,after",
    [
        ("Read  this\nparagraph.", "Read paragraph."),
        (
            '{"value": "<script>alert(1)</script>"}',
            'value: "<script>alert(1)</script>"',
        ),
        ("a b", "a NEW b"),
        ("", "new"),
        ("deleted", ""),
        ("Repeated text " * 500, "Repeated " * 200),
    ],
)
def test_diff_preserves_both_texts_exactly_and_escapes_markup(before, after):
    import re

    left, right = text_diff(before, after)
    assert unescape(re.sub(r"</?(?:del|ins)\b[^>]*>", "", left)) == before
    assert unescape(re.sub(r"</?(?:del|ins)\b[^>]*>", "", right)) == after
    assert "<script>" not in left + right


def test_json_payload_cannot_terminate_the_data_script(tmp_path):
    data = {
        "cases": [],
        "experiment": {"attempts": 0},
        "ledger": [],
        "example": '</script><script>alert("unsafe")</script>',
    }
    out = tmp_path / "report.html"
    render_report(data, out)
    result = out.read_text(encoding="utf-8")
    assert "__BENCHMARK_DATA__" not in result
    assert "\\u003c/script>" in result
    assert '</script><script>alert("unsafe")' not in result


def test_large_report_compression_preserves_unicode_and_complete_sidecar(tmp_path):
    import base64
    import gzip
    import re

    original = "Résumé <script> & 日本語\n" * 20000
    data = {
        "cases": [{"id": "long", "variants": [{"before_html": original,
                    "after_html": "annotated after", "after_text": "compressed after"}]}],
        "ledger": [],
    }
    out = tmp_path / "report.html"
    render_report(data, out)
    rendered = out.read_text(encoding="utf-8")
    encoded = re.search(r'data-encoding="gzip-base64"[^>]*>([^<]+)</script>', rendered).group(1)
    inline = json.loads(gzip.decompress(base64.b64decode(encoded)))
    assert inline["cases"][0]["variants"][0] == {"before_html": original}
    assert json.loads(out.with_suffix(".json").read_text(encoding="utf-8")) == data
    assert len(rendered) < 512000


def test_browser_byte_decoder_recovers_all_values_and_padding_lengths(tmp_path):
    import shutil
    import subprocess

    if not shutil.which("node"):
        pytest.skip("Node not installed")
    values = [bytes(range(256)) * 3 + bytes(range(length)) for length in range(16)]
    encoded = [{"text": pack_bytes_as_text(value), "length": len(value)} for value in values]
    path = tmp_path / "packed.json"
    path.write_text(json.dumps(encoded), encoding="utf-8")
    template = (PRICING_PATH.parents[1] / "scripts/benchmark_report.html").read_text(encoding="utf-8")
    decoder = template.split("function decodeCompressedPayload", 1)[1].split("\nwindow.__benchmarkReportReady", 1)[0]
    script = "const fs=require('fs');\nfunction decodeCompressedPayload" + decoder
    script += "\nconst data=JSON.parse(fs.readFileSync(" + json.dumps(str(path)) + ",'utf8'));"
    script += "\nconsole.log(JSON.stringify(data.map(x=>Buffer.from(decodeCompressedPayload(x.text,'gzip-utf15',x.length)).toString('hex'))));"
    code = tmp_path / "decode.js"
    code.write_text(script, encoding="utf-8")
    recovered = json.loads(subprocess.run(["node", str(code)], capture_output=True, text=True, check=True).stdout)
    assert recovered == [value.hex() for value in values]


def test_diff_keeps_frequent_words_visible_instead_of_striking_whole_paragraphs():
    before = "The project uses the model and the dataset. " * 40
    after = "The project uses model and dataset. " * 40
    left, right = text_diff(before, after)
    assert '<del class="token drop">The project' not in left
    assert '<ins class="token added">The project' not in right
    assert left.count('<del class="token drop">') >= 40


def test_frontend_cost_calculator_matches_python_for_cache_writes_and_long_context(
    prices,
):
    import shutil
    import subprocess

    if not shutil.which("node"):
        pytest.skip("Node not installed")
    template = (
        Path(__file__).resolve().parents[1] / "scripts/benchmark_report.html"
    ).read_text(encoding="utf-8")
    function = template.split("function price(", 1)[1].split("\nfunction pairs", 1)[0]
    script = "const P=" + json.dumps(prices) + ";\nfunction price(" + function
    scenarios = [
        {"input": 12000, "output": 1000, "cache": 0.25, "ws": 0.5, "retention": "30m"},
        {"input": 300000, "output": 500, "cache": 0.4, "ws": 0.5, "retention": "12h"},
        {"input": 4343, "output": 30, "cache": 0, "ws": 0, "retention": "30m"},
    ]
    script += "\nconst S=" + json.dumps(scenarios) + ";\n"
    script += "console.log(JSON.stringify(S.map(x=>price(x.input,x.output,x.cache,{input:1,cached:.05,output:5,fee:.3,ws:x.ws,retention:x.retention}))));"
    results = json.loads(
        subprocess.run(
            ["node", "-e", script], capture_output=True, text=True, check=True
        ).stdout
    )
    for scenario, observed in zip(scenarios, results):
        expected = api_cost(
            scenario["input"],
            scenario["output"],
            prices,
            cached_tokens=scenario["input"] * scenario["cache"],
            write_30m_tokens=scenario["input"] * scenario["ws"]
            if scenario["retention"] == "30m"
            else 0,
            write_12h_tokens=scenario["input"] * scenario["ws"]
            if scenario["retention"] == "12h"
            else 0,
        )
        assert observed["total"] == pytest.approx(expected["total"])
        assert observed["input"] == pytest.approx(expected["input_total"])
