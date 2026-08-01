from scripts.collect_cloud_run_metrics import metric_values, percentile, point_value


def test_point_value_reads_distribution_and_scalar_values():
    assert point_value({"distributionValue": {"mean": 0.25}}) == 0.25
    assert point_value({"doubleValue": 3.5}) == 3.5
    assert point_value({"int64Value": "4"}) == 4.0
    assert point_value({}) is None


def test_metric_values_normalizes_percent_and_gpu_memory():
    payload = {
        "timeSeries": [
            {
                "points": [
                    {"value": {"distributionValue": {"mean": 0.25}}},
                ]
            }
        ]
    }
    assert metric_values("gpu_utilization_pct", payload) == [25.0]

    memory_payload = {
        "timeSeries": [
            {
                "points": [
                    {"value": {"distributionValue": {"mean": 2 * 1024**3}}},
                ]
            }
        ]
    }
    assert metric_values("gpu_memory_usage_gib", memory_payload) == [2.0]


def test_percentile_interpolates_sorted_values():
    assert percentile([], 0.5) is None
    assert percentile([7.0], 0.5) == 7.0
    assert percentile([1.0, 2.0, 3.0, 4.0], 0.5) == 2.5
