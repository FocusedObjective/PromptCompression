import rawPolicy from "../../app/gpu_compression_policy.json";

function positiveNumber(name: keyof typeof rawPolicy): number {
  const value = rawPolicy[name];
  if (typeof value !== "number" || !Number.isFinite(value) || value <= 0) {
    throw new Error(`GPU compression policy ${name} must be a positive number`);
  }
  return value;
}

function boundedRatio(name: keyof typeof rawPolicy): number {
  const value = rawPolicy[name];
  if (typeof value !== "number" || !Number.isFinite(value) || value < 0 || value > 1) {
    throw new Error(`GPU compression policy ${name} must be between 0 and 1`);
  }
  return value;
}

if (typeof rawPolicy.schema_version !== "string" || rawPolicy.schema_version.length === 0) {
  throw new Error("GPU compression policy schema_version is required");
}

export const GPU_POLICY = Object.freeze({
  schemaVersion: rawPolicy.schema_version,
  minModelSegmentChars: positiveNumber("min_model_segment_chars"),
  minModelSegmentTokens: positiveNumber("min_model_segment_tokens"),
  minModelCandidateTokens: positiveNumber("min_model_candidate_tokens"),
  minModelIncrementalSavingsTokens: positiveNumber("min_model_incremental_savings_tokens"),
  minModelIncrementalReduction: boundedRatio("min_model_incremental_reduction"),
  maxModelProjectedLatencyMs: positiveNumber("max_model_projected_latency_ms"),
  maxModelAutoPlaceholders: positiveNumber("max_model_auto_placeholders"),
  coldModelTightLatencyBudgetMs: positiveNumber("cold_model_tight_latency_budget_ms"),
  maxProtectedDensity: boundedRatio("max_protected_density"),
  maxStructuredDensity: boundedRatio("max_structured_density"),
  skipModelIfDeterministicReductionGte: boundedRatio(
    "skip_model_if_deterministic_reduction_gte"
  )
});

export type GpuCompressionPolicy = typeof GPU_POLICY;

export function buildGpuPolicyCacheIdentity(
  policy: GpuCompressionPolicy = GPU_POLICY
): Record<string, string | number> {
  return { ...policy };
}
