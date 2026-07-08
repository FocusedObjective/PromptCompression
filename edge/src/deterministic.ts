import type { JsonObject, TenantProfile } from "./types";
import {
  REGEX_TOKEN_ESTIMATOR,
  compressionRatio,
  estimateRegexTokens,
  mergeTokenEstimators,
  reduction
} from "./tokenEstimator";
import { requiresOriginForJsonText, transformJsonSegmentsForEdge } from "./jsonTransform";

const EDGE_MODEL = "edge-deterministic";
const DEFAULT_AGGRESSIVENESS = 0.15;
const DEFAULT_ROLE_AGGRESSIVENESS: Record<string, number> = {
  system: 0,
  tool: 0,
  user: DEFAULT_AGGRESSIVENESS
};
const VALID_COMPRESSION_MODES = new Set(["deterministic", "model_auto", "model_force"]);
const MIN_MODEL_SEGMENT_CHARS = 160;
const MIN_MODEL_SEGMENT_TOKENS = 24;
const MIN_MODEL_AUTO_CANDIDATE_TOKENS = 20000;
const MIN_MODEL_INCREMENTAL_SAVINGS_TOKENS = 2000;
const MIN_MODEL_INCREMENTAL_REDUCTION = 0.05;
const MAX_PROTECTED_DENSITY = 0.20;
const MAX_STRUCTURED_DENSITY = 0.35;
const SKIP_MODEL_IF_DETERMINISTIC_REDUCTION_GTE = 0.12;
const COLD_MODEL_TIGHT_LATENCY_BUDGET_MS = 1000;

export interface EdgeOriginGate {
  useOrigin: boolean;
  reason: string | null;
}

export function resolveTenantProfile(body: JsonObject, headerTenantId: string | null): TenantProfile {
  const tenantProfile = isObject(body.tenant_profile) ? body.tenant_profile : {};
  const bodyTenantId = typeof body.tenant_id === "string" && body.tenant_id.trim()
    ? body.tenant_id.trim()
    : null;
  const tenantId = bodyTenantId ?? headerTenantId ?? "default";
  const profileId = typeof tenantProfile.profile_id === "string" && tenantProfile.profile_id.trim()
    ? tenantProfile.profile_id.trim()
    : `${tenantId}:base`;
  const minRate = typeof tenantProfile.min_rate === "number" ? tenantProfile.min_rate : null;
  const forceDropPhrases = Array.isArray(tenantProfile.force_drop_phrases)
    ? tenantProfile.force_drop_phrases.filter((value): value is string => {
      return typeof value === "string" && value.length > 0;
    })
    : [];

  return {
    tenantId,
    profileId,
    source: Object.keys(tenantProfile).length > 0 ? "api" : "default",
    minRate,
    forceDropPhrases
  };
}

export function resolveCompressMode(body: JsonObject, route: string): string {
  if (route === "/compress") {
    return typeof body.mode === "string" ? body.mode : "model_force";
  }
  const settings = isObject(body.compression_settings) ? body.compression_settings : {};
  return typeof settings.mode === "string" ? settings.mode : "deterministic";
}

export function resolveAggressiveness(body: JsonObject, route: string): number {
  if (route === "/compress") {
    return typeof body.aggressiveness === "number" ? body.aggressiveness : DEFAULT_AGGRESSIVENESS;
  }
  const settings = isObject(body.compression_settings) ? body.compression_settings : {};
  if (typeof settings.aggressiveness === "number") {
    return settings.aggressiveness;
  }
  if (isRoleAggressiveness(settings.aggressiveness)) {
    const values = Object.values(resolveRoleAggressiveness(body));
    return values.length > 0 ? Math.max(...values) : DEFAULT_AGGRESSIVENESS;
  }
  return DEFAULT_AGGRESSIVENESS;
}

export function shouldUseOrigin(body: JsonObject, route: string): boolean {
  const mode = resolveCompressMode(body, route);
  return mode === "model_auto" || mode === "model_force";
}

export function evaluateEdgeOriginGate(
  body: JsonObject,
  route: string,
  headerTenantId: string | null
): EdgeOriginGate {
  const mode = resolveCompressMode(body, route);
  if (mode === "deterministic") {
    return skip("edge_skipped_mode_deterministic");
  }
  if (mode !== "model_auto" && mode !== "model_force") {
    return skip("edge_skipped_no_origin_mode");
  }

  const tenant = resolveTenantProfile(body, headerTenantId);
  const target = targetRate(resolveAggressiveness(body, route), tenant);
  if (target >= 1.0) {
    return skip("edge_skipped_aggressiveness_zero");
  }

  const texts = compressibleTexts(body, route);
  if (texts.length === 0) {
    return skip("edge_skipped_no_candidate_prose");
  }
  if (texts.some(requestRequiresExactOutput)) {
    return skip("edge_skipped_exact_output_context");
  }

  const candidates = texts
    .map((text) => {
      return { text, tokens: estimateRegexTokens(text).count };
    })
    .filter((candidate) => {
      return candidate.text.trim().length >= MIN_MODEL_SEGMENT_CHARS
        && candidate.tokens >= MIN_MODEL_SEGMENT_TOKENS;
    });

  if (candidates.length === 0) {
    return skip("edge_skipped_no_candidate_prose");
  }

  if (mode === "model_force") {
    return run();
  }

  const candidateTokens = candidates.reduce((sum, candidate) => sum + candidate.tokens, 0);
  if (candidateTokens < MIN_MODEL_AUTO_CANDIDATE_TOKENS) {
    return skip("edge_skipped_low_candidate_tokens");
  }

  const deterministicReduction = deterministicReductionForTexts(candidates.map((candidate) => candidate.text), tenant);
  if (deterministicReduction >= SKIP_MODEL_IF_DETERMINISTIC_REDUCTION_GTE) {
    return skip("edge_skipped_deterministic_savings_sufficient");
  }

  const candidateText = candidates.map((candidate) => candidate.text).join("\n");
  const protectedDensityValue = protectedDensityForText(candidateText);
  if (protectedDensityValue > MAX_PROTECTED_DENSITY) {
    return skip("edge_skipped_high_protected_density");
  }

  const structuredDensity = structuredDensityForTexts(texts);
  if (structuredDensity > MAX_STRUCTURED_DENSITY) {
    return skip("edge_skipped_high_structured_density");
  }

  const latencyBudgetMs = resolveLatencyBudgetMs(body, route);
  if (latencyBudgetMs !== null && latencyBudgetMs <= COLD_MODEL_TIGHT_LATENCY_BUDGET_MS) {
    return skip("edge_skipped_tight_latency_budget");
  }

  const identifierDensity = identifierDensityForText(candidateText);
  const averageSegmentTokens = candidateTokens / candidates.length;
  const expectedIncrementalReduction = expectedModelReduction(
    deterministicReduction,
    protectedDensityValue,
    identifierDensity,
    structuredDensity,
    averageSegmentTokens
  );
  const expectedIncrementalSavingsTokens = Math.floor(candidateTokens * expectedIncrementalReduction);
  if (
    expectedIncrementalSavingsTokens < MIN_MODEL_INCREMENTAL_SAVINGS_TOKENS
    || expectedIncrementalReduction < MIN_MODEL_INCREMENTAL_REDUCTION
  ) {
    return skip("edge_skipped_low_expected_incremental_savings");
  }

  return run();
}

export function validateRequestBody(body: JsonObject, route: string): void {
  if (route === "/compress") {
    requireString(body.text, "text");
    validateMode(body.mode);
    validateNumberRange(body.aggressiveness, "aggressiveness", 0, 1);
    validateNumberRange(body.latency_budget_ms, "latency_budget_ms", 0, Number.POSITIVE_INFINITY);
    validateTenantProfile(body.tenant_profile);
    return;
  }

  if (route === "/v1/compress") {
    requireString(body.input, "input");
    validateTenantProfile(body.tenant_profile);
    validateCompressionSettings(body.compression_settings);
    return;
  }

  if (route === "/v1/messages/compress") {
    if (!Array.isArray(body.messages) || body.messages.length === 0) {
      throw new RequestShapeError("messages must be a non-empty array");
    }
    for (const message of body.messages) {
      if (!isObject(message)) {
        throw new RequestShapeError("each message must be an object");
      }
      requireString(message.role, "message.role");
    }
    validateTenantProfile(body.tenant_profile);
    validateCompressionSettings(body.compression_settings);
    return;
  }

  if (route === "/tokens/estimate") {
    if (body.text !== undefined && typeof body.text !== "string") {
      throw new RequestShapeError("text must be a string");
    }
    if (body.model !== undefined && typeof body.model !== "string") {
      throw new RequestShapeError("model must be a string");
    }
  }
}

export function needsOriginForDeterministic(body: JsonObject, route: string): boolean {
  if (hasTenantProfileOverrides(body)) {
    return true;
  }

  if (route === "/compress" && (body.include_sections === true || body.include_diagnostics === true)) {
    return true;
  }

  return compressibleTexts(body, route).some(looksTooComplexForEdgeSubset);
}

export function buildCompressResponse(
  body: JsonObject,
  tenant: TenantProfile,
  elapsedMs: number,
  warnings: string[] = []
): JsonObject {
  const input = requireString(body.text, "text");
  const output = deterministicText(input, tenant);
  const original = estimateRegexTokens(input);
  const compressed = estimateRegexTokens(output);
  const includeSections = body.include_sections === true;
  const aggressiveness = resolveAggressiveness(body, "/compress");

  return {
    compressed_text: output,
    original_tokens: original.count,
    compressed_tokens: compressed.count,
    reduction: reduction(original.count, compressed.count),
    aggressiveness,
    target_rate: targetRate(aggressiveness, tenant),
    model: EDGE_MODEL,
    tenant_id: tenant.tenantId,
    compression_profile: tenant.profileId,
    compression_profile_source: tenant.source,
    training_sample_recorded: false,
    token_estimator: REGEX_TOKEN_ESTIMATOR,
    compression_mode: "deterministic",
    compression_path: output === input ? "unchanged" : "deterministic_only",
    warnings: ["edge_deterministic_response", ...warnings],
    elapsed_ms: elapsedMs,
    labeled_tokens: [],
    output_sections: includeSections
      ? [{
        text: output,
        kind: "prose",
        compressed: output !== input,
        protected: false,
        labeled_tokens: []
      }]
      : []
  };
}

export function buildV1CompressResponse(
  body: JsonObject,
  tenant: TenantProfile,
  elapsedMs: number,
  warnings: string[] = []
): JsonObject {
  const input = requireString(body.input, "input");
  const output = deterministicText(input, tenant);
  const original = estimateRegexTokens(input);
  const compressed = estimateRegexTokens(output);
  const tokensSaved = Math.max(0, original.count - compressed.count);

  return {
    output,
    output_tokens: compressed.count,
    input_tokens: original.count,
    original_input_tokens: original.count,
    tokens_saved: tokensSaved,
    compression_ratio: compressionRatio(original.count, compressed.count),
    token_estimator: REGEX_TOKEN_ESTIMATOR,
    downstream_estimated_input_tokens: original.count,
    downstream_estimated_output_tokens: compressed.count,
    downstream_token_estimator: REGEX_TOKEN_ESTIMATOR,
    compression_time: elapsedMs,
    tenant_id: tenant.tenantId,
    compression_profile: tenant.profileId,
    compression_profile_source: tenant.source,
    training_sample_recorded: false,
    warnings: ["edge_deterministic_response", ...warnings]
  };
}

export function buildMessagesResponse(
  body: JsonObject,
  tenant: TenantProfile,
  elapsedMs: number,
  warnings: string[] = []
): JsonObject {
  if (!Array.isArray(body.messages) || body.messages.length === 0) {
    throw new RequestShapeError("messages must be a non-empty array");
  }

  const settings = isObject(body.compression_settings) ? body.compression_settings : {};
  const compactEmpty = settings.compact_empty_user_messages === true;
  const compactDuplicate = settings.compact_duplicate_user_text_parts === true;
  const seenUserTexts = new Set<string>();
  const outputMessages: JsonObject[] = [];
  const stats: JsonObject[] = [];
  let compressedRoleInputTokens = 0;
  let compressedRoleOutputTokens = 0;
  let userInputTokens = 0;
  let userOutputTokens = 0;
  let nonUserTokens = 0;

  body.messages.forEach((raw, index) => {
    if (!isObject(raw)) {
      throw new RequestShapeError("each message must be an object");
    }

    const role = typeof raw.role === "string" ? raw.role : "";
    const normalizedRole = role.toLowerCase();
    const originalMessage = raw as JsonObject;
    const originalContent = originalMessage.content;
    const originalEstimate = estimateContentTokens(originalContent);

    const roleAggressiveness = resolveRoleAggressiveness(body)[normalizedRole];
    if (roleAggressiveness === undefined || roleAggressiveness <= 0) {
      outputMessages.push(cloneObject(originalMessage));
      nonUserTokens += originalEstimate.count;
      stats.push(messageStat(
        index,
        role,
        originalEstimate.count,
        originalEstimate.count,
        false,
        false,
        0,
        0,
        roleAggressiveness === undefined ? "role_preserved" : "aggressiveness_zero"
      ));
      return;
    }

    if (typeof originalContent === "string") {
      if (normalizedRole === "user" && originalContent.length === 0 && compactEmpty) {
        stats.push(messageStat(index, role, 0, 0, false, false, 0, 0, "empty_user_message_dropped"));
        return;
      }
      if (normalizedRole === "user" && compactDuplicate && seenUserTexts.has(originalContent)) {
        stats.push(messageStat(index, role, originalEstimate.count, 0, false, false, 1, 0, "duplicate_user_text_dropped"));
        return;
      }
      if (normalizedRole === "user") {
        seenUserTexts.add(originalContent);
      }
      const compressedText = deterministicText(originalContent, tenant);
      const compressedEstimate = estimateRegexTokens(compressedText);
      compressedRoleInputTokens += originalEstimate.count;
      compressedRoleOutputTokens += compressedEstimate.count;
      if (normalizedRole === "user") {
        userInputTokens += originalEstimate.count;
        userOutputTokens += compressedEstimate.count;
      }
      outputMessages.push({ ...cloneObject(originalMessage), content: compressedText });
      stats.push(messageStat(index, role, originalEstimate.count, compressedEstimate.count, true, compressedText !== originalContent, 1, 1));
      return;
    }

    if (Array.isArray(originalContent)) {
      let textParts = 0;
      let compressedTextParts = 0;
      let droppedDuplicate = false;
      const newParts: unknown[] = [];

      for (const part of originalContent) {
        if (!isObject(part) || typeof part.text !== "string") {
          newParts.push(part);
          continue;
        }
        textParts += 1;
        if (normalizedRole === "user" && compactDuplicate && seenUserTexts.has(part.text)) {
          droppedDuplicate = true;
          continue;
        }
        if (normalizedRole === "user") {
          seenUserTexts.add(part.text);
        }
        const compressedText = deterministicText(part.text, tenant);
        const originalPartTokens = estimateRegexTokens(part.text).count;
        const compressedPartTokens = estimateRegexTokens(compressedText).count;
        compressedRoleInputTokens += originalPartTokens;
        compressedRoleOutputTokens += compressedPartTokens;
        if (normalizedRole === "user") {
          userInputTokens += originalPartTokens;
          userOutputTokens += compressedPartTokens;
        }
        compressedTextParts += 1;
        newParts.push({ ...cloneObject(part), text: compressedText });
      }

      const newContentEstimate = estimateContentTokens(newParts);
      outputMessages.push({ ...cloneObject(originalMessage), content: newParts });
      stats.push(messageStat(
        index,
        role,
        originalEstimate.count,
        newContentEstimate.count,
        compressedTextParts > 0,
        newContentEstimate.count !== originalEstimate.count,
        textParts,
        compressedTextParts,
        droppedDuplicate ? "duplicate_user_text_part_dropped" : undefined
      ));
      return;
    }

    outputMessages.push(cloneObject(originalMessage));
    nonUserTokens += originalEstimate.count;
    stats.push(messageStat(index, role, originalEstimate.count, originalEstimate.count, false, false, 0, 0, "no_text_content"));
  });

  const topLevelPreserved = estimateTopLevelPreservedTokens(body);
  const inputTokens = compressedRoleInputTokens + nonUserTokens + topLevelPreserved.count;
  const outputTokens = compressedRoleOutputTokens + nonUserTokens + topLevelPreserved.count;
  const compressedRequest = cloneObject(body);
  delete compressedRequest.compression_settings;
  delete compressedRequest.tenant_id;
  delete compressedRequest.tenant_profile;
  compressedRequest.messages = outputMessages;

  return {
    compressed_request: compressedRequest,
    messages: outputMessages,
    input_tokens: inputTokens,
    output_tokens: outputTokens,
    original_input_tokens: inputTokens,
    tokens_saved: Math.max(0, inputTokens - outputTokens),
    compression_ratio: compressionRatio(inputTokens, outputTokens),
    compression_time: elapsedMs,
    user_input_tokens: userInputTokens,
    user_output_tokens: userOutputTokens,
    user_tokens_saved: Math.max(0, userInputTokens - userOutputTokens),
    non_user_tokens_preserved: nonUserTokens + topLevelPreserved.count,
    token_estimator: mergeTokenEstimators([REGEX_TOKEN_ESTIMATOR, topLevelPreserved.estimator]),
    downstream_estimated_input_tokens: inputTokens,
    downstream_estimated_output_tokens: outputTokens,
    downstream_token_estimator: REGEX_TOKEN_ESTIMATOR,
    tenant_id: tenant.tenantId,
    compression_profile: tenant.profileId,
    compression_profile_source: tenant.source,
    training_sample_recorded: false,
    message_stats: stats,
    warnings: ["edge_deterministic_response", ...warnings]
  };
}

export function buildTokenEstimateResponse(body: JsonObject): JsonObject {
  const text = typeof body.text === "string" ? body.text : "";
  const estimate = estimateRegexTokens(text);
  return {
    tokens: estimate.count,
    token_estimator: estimate.estimator,
    tokenizer_backed: estimate.tokenizerBacked
  };
}

export function deterministicText(text: string, tenant: TenantProfile): string {
  let output = text.replace(/<\/?nocompress>/gi, "");
  for (const phrase of tenant.forceDropPhrases) {
    output = output.split(phrase).join("");
  }

  const hasProtectedBlock = /```|~~~|<(pre|code|script|style|template|svg)\b/i.test(output);
  if (hasProtectedBlock) {
    return output;
  }

  output = transformJsonSegmentsForEdge(output).text;

  return output
    .replace(/[ \t]+$/gm, "")
    .replace(/\n{3,}/g, "\n\n")
    .trim();
}

export class RequestShapeError extends Error {}

function validateCompressionSettings(value: unknown): void {
  if (value === undefined || value === null) {
    return;
  }
  if (!isObject(value)) {
    throw new RequestShapeError("compression_settings must be an object");
  }
  validateMode(value.mode);
  validateAggressiveness(value.aggressiveness, "compression_settings.aggressiveness");
  validateNumberRange(value.latency_budget_ms, "compression_settings.latency_budget_ms", 0, Number.POSITIVE_INFINITY);
}

function validateTenantProfile(value: unknown): void {
  if (value === undefined || value === null) {
    return;
  }
  if (!isObject(value)) {
    throw new RequestShapeError("tenant_profile must be an object");
  }
  validateNumberRange(value.default_aggressiveness, "tenant_profile.default_aggressiveness", 0, 1);
  validateNumberRange(value.min_rate, "tenant_profile.min_rate", 0.05, 1);
  validateStringArray(value.force_keep_tokens, "tenant_profile.force_keep_tokens");
  validateStringArray(value.force_drop_phrases, "tenant_profile.force_drop_phrases");
}

function validateMode(value: unknown): void {
  if (value === undefined || value === null) {
    return;
  }
  if (typeof value !== "string" || !VALID_COMPRESSION_MODES.has(value)) {
    throw new RequestShapeError("mode must be one of deterministic, model_auto, model_force");
  }
}

function validateAggressiveness(value: unknown, field: string): void {
  if (value === undefined || value === null) {
    return;
  }
  if (typeof value === "number") {
    validateNumberRange(value, field, 0, 1);
    return;
  }
  if (!isObject(value)) {
    throw new RequestShapeError(`${field} must be a number or per-role object`);
  }
  for (const [role, aggressiveness] of Object.entries(value)) {
    if (typeof role !== "string" || role.trim().length === 0) {
      throw new RequestShapeError(`${field} roles must be non-empty strings`);
    }
    validateNumberRange(aggressiveness, `${field}.${role}`, 0, 1);
  }
}

function validateNumberRange(
  value: unknown,
  field: string,
  min: number,
  max: number
): void {
  if (value === undefined || value === null) {
    return;
  }
  if (typeof value !== "number" || !Number.isFinite(value) || value < min || value > max) {
    throw new RequestShapeError(`${field} must be between ${min} and ${max}`);
  }
}

function validateStringArray(value: unknown, field: string): void {
  if (value === undefined || value === null) {
    return;
  }
  if (!Array.isArray(value) || value.some((item) => typeof item !== "string")) {
    throw new RequestShapeError(`${field} must be an array of strings`);
  }
}

function hasTenantProfileOverrides(body: JsonObject): boolean {
  return isObject(body.tenant_profile) && Object.keys(body.tenant_profile).length > 0;
}

function compressibleTexts(body: JsonObject, route: string): string[] {
  if (route === "/compress" && typeof body.text === "string") {
    return [body.text];
  }
  if (route === "/v1/compress" && typeof body.input === "string") {
    return [body.input];
  }
  if (route !== "/v1/messages/compress" || !Array.isArray(body.messages)) {
    return [];
  }

  const texts: string[] = [];
  for (const message of body.messages) {
    if (!isObject(message) || !shouldCompressRole(message.role, body)) {
      continue;
    }
    if (typeof message.content === "string") {
      texts.push(message.content);
      continue;
    }
    if (!Array.isArray(message.content)) {
      continue;
    }
    for (const part of message.content) {
      if (isObject(part) && typeof part.text === "string") {
        texts.push(part.text);
      }
    }
  }
  return texts;
}

function looksTooComplexForEdgeSubset(text: string): boolean {
  const trimmed = text.trim();
  if (!trimmed) {
    return false;
  }

  if (requiresOriginForJsonText(text)) {
    return true;
  }

  return (
    /```|~~~/.test(text)
    || /<(html|body|main|article|section|div|p|table|thead|tbody|tr|td|th|ul|ol|li|pre|code|script|style|template|svg)\b/i.test(text)
    || (/^[\[{]/.test(trimmed) && requiresOriginForJsonText(trimmed))
    || (!/^[\[{]/.test(trimmed) && /"\s*(tool_calls|function_call|messages|schema|properties)"\s*:/.test(text))
  );
}

function requestRequiresExactOutput(text: string): boolean {
  const lowered = text.toLowerCase();
  return [
    "byte-stable",
    "byte stable",
    "byte-exact",
    "byte exact",
    "return exactly as written",
    "return exactly as provided",
    "return the input exactly",
    "output the input exactly",
    "preserve formatting exactly",
    "preserve whitespace exactly",
    "verbatim output",
    "do not modify the text",
    "do not change the text"
  ].some((term) => lowered.includes(term));
}

function deterministicReductionForTexts(texts: string[], tenant: TenantProfile): number {
  const original = texts.reduce((sum, text) => sum + estimateRegexTokens(text).count, 0);
  if (original <= 0) {
    return 0;
  }
  const deterministic = texts.reduce((sum, text) => {
    return sum + estimateRegexTokens(deterministicText(text, tenant)).count;
  }, 0);
  return Math.max(0, 1 - deterministic / original);
}

function resolveLatencyBudgetMs(body: JsonObject, route: string): number | null {
  if (route === "/compress") {
    return typeof body.latency_budget_ms === "number" ? body.latency_budget_ms : null;
  }
  const settings = isObject(body.compression_settings) ? body.compression_settings : {};
  return typeof settings.latency_budget_ms === "number" ? settings.latency_budget_ms : null;
}

function protectedDensityForText(text: string): number {
  if (!text) {
    return 0;
  }
  const protectedChars = sumMatchLengths(text, [
    /https?:\/\/[^\s"'<>]+/gi,
    /\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b/gi,
    /```[\s\S]*?```|~~~[\s\S]*?~~~/g,
    /<(pre|code|script|style|template|svg)\b[\s\S]*?<\/\1>/gi,
    /<\/?nocompress>/gi
  ]);
  return protectedChars / text.length;
}

function identifierDensityForText(text: string): number {
  if (!text) {
    return 0;
  }
  const identifierChars = sumMatchLengths(text, [
    /https?:\/\/[^\s"'<>]+/gi,
    /\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b/gi,
    /\b[A-Z]{2,}[_-][A-Z0-9_-]{6,}\b/g,
    /\b[a-z][a-z0-9]*[_-][a-z0-9_-]{8,}\b/gi,
    /\b\d+(?:[.,:/-]\d+){1,}\b/g,
    /\$[0-9][0-9,]*(?:\.\d+)?/g
  ]);
  return identifierChars / text.length;
}

function structuredDensityForTexts(texts: string[]): number {
  const totalChars = texts.reduce((sum, text) => sum + text.length, 0);
  if (totalChars <= 0) {
    return 0;
  }
  const structuredChars = texts.reduce((sum, text) => {
    if (isStructuredText(text)) {
      return sum + text.length;
    }
    return sum;
  }, 0);
  return structuredChars / totalChars;
}

function isStructuredText(text: string): boolean {
  return requiresOriginForJsonText(text)
    || /```|~~~/.test(text)
    || /<(html|body|main|article|section|div|p|table|thead|tbody|tr|td|th|ul|ol|li|pre|code|script|style|template|svg)\b/i.test(text)
    || /"\s*(tool_calls|function_call|messages|schema|properties)"\s*:/.test(text);
}

function expectedModelReduction(
  deterministicReduction: number,
  protectedDensityValue: number,
  identifierDensityValue: number,
  structuredDensityValue: number,
  averageSegmentTokens: number
): number {
  let expected = structuredDensityValue < 0.10 ? 0.08 : 0.05;
  if (protectedDensityValue > 0.10) {
    expected -= 0.02;
  }
  if (identifierDensityValue > 0.10) {
    expected -= 0.02;
  }
  if (averageSegmentTokens > 0 && averageSegmentTokens < 120) {
    expected -= 0.02;
  }
  if (deterministicReduction >= 0.10) {
    expected -= 0.02;
  }
  return Math.max(0, expected);
}

function sumMatchLengths(text: string, patterns: RegExp[]): number {
  let total = 0;
  for (const pattern of patterns) {
    for (const match of text.matchAll(pattern)) {
      total += match[0].length;
    }
  }
  return Math.min(total, text.length);
}

function skip(reason: string): EdgeOriginGate {
  return { useOrigin: false, reason };
}

function run(): EdgeOriginGate {
  return { useOrigin: true, reason: null };
}

function targetRate(aggressiveness: number, tenant: TenantProfile): number {
  const floor = tenant.minRate ?? 0.45;
  return Math.max(floor, 1 - aggressiveness * (1 - floor));
}

function estimateContentTokens(content: unknown): { count: number; estimator: string } {
  if (typeof content === "string") {
    return estimateRegexTokens(content);
  }
  if (Array.isArray(content)) {
    const counts = content.map((part) => {
      if (isObject(part) && typeof part.text === "string") {
        return estimateRegexTokens(part.text);
      }
      return { count: 0, estimator: REGEX_TOKEN_ESTIMATOR, tokenizerBacked: false };
    });
    return {
      count: counts.reduce((sum, item) => sum + item.count, 0),
      estimator: mergeTokenEstimators(counts.map((item) => item.estimator))
    };
  }
  return { count: 0, estimator: REGEX_TOKEN_ESTIMATOR };
}

function estimateTopLevelPreservedTokens(body: JsonObject): { count: number; estimator: string } {
  const estimates = ["system", "instructions", "developer"].map((key) => {
    return estimateContentTokens(body[key]);
  });
  return {
    count: estimates.reduce((sum, estimate) => sum + estimate.count, 0),
    estimator: mergeTokenEstimators(estimates.map((estimate) => estimate.estimator))
  };
}

function messageStat(
  index: number,
  role: string,
  originalTokens: number,
  compressedTokens: number,
  compressionApplied: boolean,
  compressed: boolean,
  textParts: number,
  compressedTextParts: number,
  skippedReason?: string
): JsonObject {
  return {
    index,
    role,
    original_tokens: originalTokens,
    compressed_tokens: compressedTokens,
    tokens_saved: Math.max(0, originalTokens - compressedTokens),
    compression_applied: compressionApplied,
    compressed,
    text_parts: textParts,
    compressed_text_parts: compressedTextParts,
    ...(skippedReason ? { skipped_reason: skippedReason } : {})
  };
}

function requireString(value: unknown, field: string): string {
  if (typeof value !== "string" || value.length === 0) {
    throw new RequestShapeError(`${field} must be a non-empty string`);
  }
  return value;
}

function isObject(value: unknown): value is JsonObject {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isRoleAggressiveness(value: unknown): value is Record<string, number> {
  return isObject(value) && Object.values(value).every((item) => {
    return typeof item === "number" && Number.isFinite(item);
  });
}

function shouldCompressRole(role: unknown, body: JsonObject): boolean {
  const normalizedRole = typeof role === "string" ? role.toLowerCase() : "";
  const roleAggressiveness = resolveRoleAggressiveness(body)[normalizedRole];
  return roleAggressiveness !== undefined && roleAggressiveness > 0;
}

function resolveRoleAggressiveness(body: JsonObject): Record<string, number> {
  const settings = isObject(body.compression_settings) ? body.compression_settings : {};
  const defaults = { ...DEFAULT_ROLE_AGGRESSIVENESS };
  if (typeof settings.aggressiveness === "number") {
    defaults.user = settings.aggressiveness;
    return defaults;
  }
  if (!isRoleAggressiveness(settings.aggressiveness)) {
    return defaults;
  }
  for (const [role, aggressiveness] of Object.entries(settings.aggressiveness)) {
    defaults[role.toLowerCase()] = aggressiveness;
  }
  return defaults;
}

function cloneObject(value: JsonObject): JsonObject {
  return { ...value };
}
