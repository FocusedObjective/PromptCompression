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
const VALID_COMPRESSION_MODES = new Set(["deterministic", "model_auto", "model_force"]);

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
  return typeof settings.aggressiveness === "number" ? settings.aggressiveness : DEFAULT_AGGRESSIVENESS;
}

export function shouldUseOrigin(body: JsonObject, route: string): boolean {
  const mode = resolveCompressMode(body, route);
  return mode === "model_auto" || mode === "model_force";
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
  let userInputTokens = 0;
  let userOutputTokens = 0;
  let nonUserTokens = 0;

  body.messages.forEach((raw, index) => {
    if (!isObject(raw)) {
      throw new RequestShapeError("each message must be an object");
    }

    const role = typeof raw.role === "string" ? raw.role : "";
    const originalMessage = raw as JsonObject;
    const originalContent = originalMessage.content;
    const originalEstimate = estimateContentTokens(originalContent);

    if (role !== "user") {
      outputMessages.push(cloneObject(originalMessage));
      nonUserTokens += originalEstimate.count;
      stats.push(messageStat(index, role, originalEstimate.count, originalEstimate.count, false, false, 0, 0, "role_preserved"));
      return;
    }

    if (typeof originalContent === "string") {
      if (originalContent.length === 0 && compactEmpty) {
        stats.push(messageStat(index, role, 0, 0, false, false, 0, 0, "empty_user_message_dropped"));
        return;
      }
      if (compactDuplicate && seenUserTexts.has(originalContent)) {
        stats.push(messageStat(index, role, originalEstimate.count, 0, false, false, 1, 0, "duplicate_user_text_dropped"));
        return;
      }
      seenUserTexts.add(originalContent);
      const compressedText = deterministicText(originalContent, tenant);
      const compressedEstimate = estimateRegexTokens(compressedText);
      userInputTokens += originalEstimate.count;
      userOutputTokens += compressedEstimate.count;
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
        if (compactDuplicate && seenUserTexts.has(part.text)) {
          droppedDuplicate = true;
          continue;
        }
        seenUserTexts.add(part.text);
        const compressedText = deterministicText(part.text, tenant);
        const originalPartTokens = estimateRegexTokens(part.text).count;
        const compressedPartTokens = estimateRegexTokens(compressedText).count;
        userInputTokens += originalPartTokens;
        userOutputTokens += compressedPartTokens;
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
  const inputTokens = userInputTokens + nonUserTokens + topLevelPreserved.count;
  const outputTokens = userOutputTokens + nonUserTokens + topLevelPreserved.count;
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
  validateNumberRange(value.aggressiveness, "compression_settings.aggressiveness", 0, 1);
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
    if (!isObject(message) || message.role !== "user") {
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

function cloneObject(value: JsonObject): JsonObject {
  return { ...value };
}
