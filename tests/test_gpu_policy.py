from app.gpu_policy import GPU_COMPRESSION_POLICY


def test_production_gpu_policy_has_expected_roi_floors():
    assert GPU_COMPRESSION_POLICY.schema_version == "gpu-compression-policy-v1"
    assert GPU_COMPRESSION_POLICY.min_model_candidate_tokens == 2_000
    assert GPU_COMPRESSION_POLICY.min_model_incremental_savings_tokens == 200
    assert GPU_COMPRESSION_POLICY.min_model_incremental_reduction == 0.05
