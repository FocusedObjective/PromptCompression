import type { JsonObject } from "./types";

const MIN_JSON_CHARS = 300;
const MIN_JSON_LINES = 4;
const MIN_TOON_SAVINGS = 0.08;
const JSON_START_PATTERN = /[{\[]/g;

export interface JsonTransformResult {
  text: string;
  transformedCount: number;
}

interface JsonCandidateDecision {
  supported: boolean;
  output?: string;
}

type JsonValue = null | boolean | number | string | JsonValue[] | { [key: string]: JsonValue };

export function transformJsonSegmentsForEdge(text: string): JsonTransformResult {
  let output = "";
  let cursor = 0;
  let searchCursor = 0;
  let transformedCount = 0;

  while (searchCursor < text.length) {
    const start = findJsonStart(text, searchCursor);
    if (start === null) {
      break;
    }

    const end = findBalancedJsonEnd(text, start);
    if (end === null) {
      break;
    }

    const candidate = text.slice(start, end);
    const leadingContext = text.slice(cursor, start);
    const decision = edgeJsonCandidate(candidate, leadingContext);
    if (!decision.supported || decision.output === undefined) {
      searchCursor = end;
      continue;
    }

    output += text.slice(cursor, start);
    output += decision.output;
    cursor = end;
    searchCursor = end;
    transformedCount += 1;
  }

  if (transformedCount === 0) {
    return { text, transformedCount };
  }

  output += text.slice(cursor);
  return { text: output, transformedCount };
}

export function requiresOriginForJsonText(text: string): boolean {
  let cursor = 0;
  let searchCursor = 0;

  while (searchCursor < text.length) {
    const start = findJsonStart(text, searchCursor);
    if (start === null) {
      return false;
    }

    const end = findBalancedJsonEnd(text, start);
    if (end === null) {
      return looksLikeJsonPrefix(text.slice(start));
    }

    const candidate = text.slice(start, end);
    const leadingContext = text.slice(cursor, start);
    if (shouldInspectJsonCandidate(candidate)) {
      const decision = edgeJsonCandidate(candidate, leadingContext);
      if (!decision.supported) {
        return true;
      }
    }

    cursor = end;
    searchCursor = end;
  }

  return false;
}

function edgeJsonCandidate(candidate: string, leadingContext: string): JsonCandidateDecision {
  if (!isMediumLargeJson(candidate)) {
    const parsedSmall = parseJson(candidate);
    if (parsedSmall.ok && (containsLlmToolExchange(parsedSmall.value) || containsSchemaMarkers(parsedSmall.value))) {
      return { supported: false };
    }
    return { supported: true };
  }

  const parsed = parseJson(candidate);
  if (!parsed.ok) {
    return { supported: false };
  }

  if (
    contextRequiresVerbatimJson(leadingContext)
    || containsLlmToolExchange(parsed.value)
    || containsSchemaMarkers(parsed.value)
    || containsDuplicateJsonKeys(candidate)
  ) {
    return { supported: false };
  }

  const toon = encodeSafeRecordArrayToon(parsed.value);
  if (toon === null || !toon.trim()) {
    return { supported: false };
  }

  const savings = 1 - toon.length / candidate.length;
  if (savings < MIN_TOON_SAVINGS) {
    return { supported: false };
  }

  return { supported: true, output: toon };
}

function shouldInspectJsonCandidate(candidate: string): boolean {
  if (isMediumLargeJson(candidate)) {
    return true;
  }
  const parsed = parseJson(candidate);
  return parsed.ok && (containsLlmToolExchange(parsed.value) || containsSchemaMarkers(parsed.value));
}

function encodeSafeRecordArrayToon(value: JsonValue): string | null {
  if (Array.isArray(value)) {
    return encodeRecordArray("items", value);
  }

  if (!isJsonObject(value)) {
    return null;
  }

  const entries = Object.entries(value);
  if (entries.length !== 1) {
    return null;
  }

  const [key, nested] = entries[0];
  if (!safeIdentifier(key) || !Array.isArray(nested)) {
    return null;
  }

  return encodeRecordArray(key, nested);
}

function encodeRecordArray(name: string, value: JsonValue[]): string | null {
  if (!safeIdentifier(name) || value.length < 2) {
    return null;
  }
  if (value.some((item) => !isJsonObject(item))) {
    return null;
  }

  const rows = value as JsonObject[];
  const columns = Object.keys(rows[0]);
  if (columns.length === 0 || columns.some((key) => !safeIdentifier(key))) {
    return null;
  }

  const encodedRows: string[] = [];
  for (const row of rows) {
    const keys = Object.keys(row);
    if (keys.length !== columns.length || columns.some((column) => !keys.includes(column))) {
      return null;
    }

    const encodedValues = columns.map((column) => encodeScalar(row[column]));
    if (encodedValues.some((item) => item === null)) {
      return null;
    }
    encodedRows.push(`  ${encodedValues.join(",")}`);
  }

  return `${name}[${rows.length}]{${columns.join(",")}}:\n${encodedRows.join("\n")}`;
}

function encodeScalar(value: unknown): string | null {
  if (value === null) {
    return "null";
  }
  if (typeof value === "boolean") {
    return value ? "true" : "false";
  }
  if (typeof value === "number") {
    return Number.isFinite(value) ? String(value) : null;
  }
  if (typeof value !== "string") {
    return null;
  }
  if (/[\n\r,]/.test(value)) {
    return null;
  }
  return value;
}

function safeIdentifier(value: string): boolean {
  return /^[A-Za-z_][A-Za-z0-9_-]*$/.test(value);
}

function containsLlmToolExchange(value: JsonValue): boolean {
  if (Array.isArray(value)) {
    return value.some(containsLlmToolExchange);
  }
  if (!isJsonObject(value)) {
    return false;
  }

  const role = value.role;
  if (typeof role === "string" && ["tool", "function"].includes(role.toLowerCase())) {
    return true;
  }

  const toolMarkers = new Set([
    "functionCall",
    "functionResponse",
    "function_call",
    "tool_call",
    "tool_call_id",
    "tool_calls",
    "tool_result",
    "tool_use",
    "tool_use_id"
  ]);
  if (Object.keys(value).some((key) => toolMarkers.has(key))) {
    return true;
  }

  const toolTypes = new Set([
    "function",
    "function_call",
    "function_call_output",
    "tool_call",
    "tool_result",
    "tool_use"
  ]);
  if (typeof value.type === "string" && toolTypes.has(value.type)) {
    return true;
  }

  return Object.values(value).some((item) => containsLlmToolExchange(item as JsonValue));
}

function containsSchemaMarkers(value: JsonValue): boolean {
  if (Array.isArray(value)) {
    return value.some(containsSchemaMarkers);
  }
  if (!isJsonObject(value)) {
    return false;
  }
  if ("schema" in value || "properties" in value) {
    return true;
  }
  return Object.values(value).some((item) => containsSchemaMarkers(item as JsonValue));
}

function contextRequiresVerbatimJson(leadingContext: string): boolean {
  const context = leadingContext.slice(-300).toLowerCase();
  if (!context) {
    return false;
  }

  const explicitJsonTerms = [
    "exact json",
    "exactly this json",
    "valid json",
    "json syntax",
    "json schema",
    "return json",
    "respond with json",
    "output json",
    "must be json",
    "json template",
    "template json",
    "json fixture",
    "fixture json",
    "json example",
    "example json"
  ];
  if (explicitJsonTerms.some((term) => context.includes(term))) {
    return true;
  }

  if (/\b(?:schema|template|fixture)\s*:/.test(context)) {
    return true;
  }

  const exactnessTerms = ["verbatim", "unchanged", "preserve", "do not change", "don't change", "return exactly"];
  const jsonTargetTerms = ["json", "schema", "template", "fixture"];
  return exactnessTerms.some((term) => context.includes(term))
    && jsonTargetTerms.some((term) => context.includes(term));
}

function isMediumLargeJson(candidate: string): boolean {
  return candidate.length >= MIN_JSON_CHARS || candidate.split("\n").length >= MIN_JSON_LINES;
}

function parseJson(candidate: string): { ok: true; value: JsonValue } | { ok: false } {
  try {
    return { ok: true, value: JSON.parse(candidate) as JsonValue };
  } catch {
    return { ok: false };
  }
}

function containsDuplicateJsonKeys(candidate: string): boolean {
  try {
    const parsed = parseValue(candidate, 0);
    return parsed.duplicateFound;
  } catch {
    return false;
  }
}

function parseValue(text: string, start: number): { index: number; duplicateFound: boolean } {
  let index = skipWhitespace(text, start);
  const char = text[index];
  if (char === "{") {
    return parseObject(text, index);
  }
  if (char === "[") {
    return parseArray(text, index);
  }
  if (char === "\"") {
    return { index: parseJsonString(text, index).index, duplicateFound: false };
  }
  if (char === "-" || /[0-9]/.test(char)) {
    return { index: parseNumber(text, index), duplicateFound: false };
  }
  for (const literal of ["true", "false", "null"]) {
    if (text.startsWith(literal, index)) {
      return { index: index + literal.length, duplicateFound: false };
    }
  }
  throw new Error("invalid json value");
}

function parseObject(text: string, start: number): { index: number; duplicateFound: boolean } {
  let index = skipWhitespace(text, start + 1);
  const keys = new Set<string>();
  let duplicateFound = false;

  if (text[index] === "}") {
    return { index: index + 1, duplicateFound };
  }

  while (index < text.length) {
    if (text[index] !== "\"") {
      throw new Error("invalid json object key");
    }
    const parsedKey = parseJsonString(text, index);
    if (keys.has(parsedKey.value)) {
      duplicateFound = true;
    }
    keys.add(parsedKey.value);
    index = skipWhitespace(text, parsedKey.index);
    if (text[index] !== ":") {
      throw new Error("invalid json object colon");
    }
    const parsedValue = parseValue(text, index + 1);
    duplicateFound = duplicateFound || parsedValue.duplicateFound;
    index = skipWhitespace(text, parsedValue.index);
    if (text[index] === "}") {
      return { index: index + 1, duplicateFound };
    }
    if (text[index] !== ",") {
      throw new Error("invalid json object comma");
    }
    index = skipWhitespace(text, index + 1);
  }

  throw new Error("unterminated json object");
}

function parseArray(text: string, start: number): { index: number; duplicateFound: boolean } {
  let index = skipWhitespace(text, start + 1);
  let duplicateFound = false;

  if (text[index] === "]") {
    return { index: index + 1, duplicateFound };
  }

  while (index < text.length) {
    const parsedValue = parseValue(text, index);
    duplicateFound = duplicateFound || parsedValue.duplicateFound;
    index = skipWhitespace(text, parsedValue.index);
    if (text[index] === "]") {
      return { index: index + 1, duplicateFound };
    }
    if (text[index] !== ",") {
      throw new Error("invalid json array comma");
    }
    index = skipWhitespace(text, index + 1);
  }

  throw new Error("unterminated json array");
}

function parseJsonString(text: string, start: number): { value: string; index: number } {
  let raw = "\"";
  let escaped = false;
  for (let index = start + 1; index < text.length; index += 1) {
    const char = text[index];
    raw += char;
    if (escaped) {
      escaped = false;
      continue;
    }
    if (char === "\\") {
      escaped = true;
      continue;
    }
    if (char === "\"") {
      return { value: JSON.parse(raw) as string, index: index + 1 };
    }
  }
  throw new Error("unterminated json string");
}

function parseNumber(text: string, start: number): number {
  const match = /^-?(?:0|[1-9]\d*)(?:\.\d+)?(?:[eE][+-]?\d+)?/.exec(text.slice(start));
  if (match === null) {
    throw new Error("invalid json number");
  }
  return start + match[0].length;
}

function skipWhitespace(text: string, start: number): number {
  let index = start;
  while (index < text.length && /[\t\n\r ]/.test(text[index])) {
    index += 1;
  }
  return index;
}

function findJsonStart(text: string, start: number): number | null {
  JSON_START_PATTERN.lastIndex = start;
  const match = JSON_START_PATTERN.exec(text);
  return match === null ? null : match.index;
}

function findBalancedJsonEnd(text: string, start: number): number | null {
  const opening = text[start];
  const stack = [opening === "{" ? "}" : "]"];
  let inString = false;
  let escaped = false;

  for (let index = start + 1; index < text.length; index += 1) {
    const char = text[index];

    if (inString) {
      if (escaped) {
        escaped = false;
      } else if (char === "\\") {
        escaped = true;
      } else if (char === "\"") {
        inString = false;
      }
      continue;
    }

    if (char === "\"") {
      inString = true;
    } else if (char === "{" || char === "[") {
      stack.push(char === "{" ? "}" : "]");
    } else if (stack.length > 0 && char === stack[stack.length - 1]) {
      stack.pop();
      if (stack.length === 0) {
        return index + 1;
      }
    }
  }

  return null;
}

function looksLikeJsonPrefix(text: string): boolean {
  return /^[\s]*[{\[]/.test(text);
}

function isJsonObject(value: unknown): value is JsonObject {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}
