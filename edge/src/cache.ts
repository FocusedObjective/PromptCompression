import type { EdgeContext, Env, JsonObject } from "./types";

const CACHE_SCHEMA_VERSION = "edge-cache-v1";
const CACHE_KEY_ORIGIN = "https://cache.prompt-compression.local";

export interface EdgeCacheHandle {
  request: Request;
  ttlSeconds: number;
}

export async function matchEdgeCache(
  request: Request,
  env: Env,
  route: string,
  body: JsonObject,
  context: EdgeContext
): Promise<{ hit: Response | null; handle: EdgeCacheHandle | null }> {
  const handle = await buildCacheHandle(request, env, route, body);
  if (!handle) {
    context.cache = isCacheExplicitlyDisabled(env) ? "disabled" : "bypass";
    return { hit: null, handle: null };
  }

  context.cache = "miss";
  const cached = await caches.default.match(handle.request);
  if (!cached) {
    return { hit: null, handle };
  }

  context.decision = "cache-hit";
  context.cache = "hit";
  return { hit: withEdgeHeaders(cached, context), handle };
}

export async function storeEdgeCache(
  handle: EdgeCacheHandle | null,
  response: Response,
  context: EdgeContext
): Promise<Response> {
  if (!handle || !isCacheableResponse(response, context)) {
    return response;
  }

  const headers = new Headers(response.headers);
  headers.set("cache-control", `public, max-age=${handle.ttlSeconds}`);
  headers.set("x-edge-cache", "store");
  headers.set("x-request-id", context.requestId);
  headers.set("x-edge-decision", context.decision);
  headers.set("x-edge-ratelimit", context.rateLimit);

  const cacheable = new Response(response.clone().body, {
    status: response.status,
    statusText: response.statusText,
    headers
  });
  await caches.default.put(handle.request, cacheable.clone());
  context.cache = "store";
  return withEdgeHeaders(cacheable, context);
}

export async function buildCacheKeyParts(
  request: Request,
  route: string,
  body: JsonObject
): Promise<JsonObject> {
  return {
    schema: CACHE_SCHEMA_VERSION,
    route,
    tenant_header: request.headers.get("x-tenant-id") || "",
    body
  };
}

export function stableJson(value: unknown): string {
  if (value === null || typeof value !== "object") {
    return JSON.stringify(value);
  }
  if (Array.isArray(value)) {
    return `[${value.map((item) => stableJson(item)).join(",")}]`;
  }

  const object = value as Record<string, unknown>;
  return `{${Object.keys(object)
    .sort()
    .map((key) => `${JSON.stringify(key)}:${stableJson(object[key])}`)
    .join(",")}}`;
}

async function buildCacheHandle(
  request: Request,
  env: Env,
  route: string,
  body: JsonObject
): Promise<EdgeCacheHandle | null> {
  if (!isCacheAvailable() || isCacheExplicitlyDisabled(env) || shouldBypassCache(request, body)) {
    return null;
  }

  const keyParts = await buildCacheKeyParts(request, route, body);
  const digest = await sha256Hex(stableJson(keyParts));
  const cacheUrl = `${CACHE_KEY_ORIGIN}/${CACHE_SCHEMA_VERSION}/${digest}`;
  return {
    request: new Request(cacheUrl, { method: "GET" }),
    ttlSeconds: parsePositiveInt(env.CACHE_TTL_SECONDS, defaultTtlSeconds(env))
  };
}

function shouldBypassCache(request: Request, body: JsonObject): boolean {
  const cacheControl = request.headers.get("cache-control") || "";
  return (
    /\bno-store\b/i.test(cacheControl)
    || body.include_diagnostics === true
    || body.debug === true
  );
}

function isCacheableResponse(response: Response, context: EdgeContext): boolean {
  if (context.decision === "fallback-deterministic" || context.decision === "reject") {
    return false;
  }
  const contentType = response.headers.get("content-type") || "";
  return response.status === 200 && contentType.toLowerCase().includes("application/json");
}

function withEdgeHeaders(response: Response, context: EdgeContext): Response {
  const headers = new Headers(response.headers);
  headers.set("x-request-id", context.requestId);
  headers.set("x-edge-decision", context.decision);
  headers.set("x-edge-cache", context.cache);
  headers.set("x-edge-ratelimit", context.rateLimit);
  headers.set("access-control-allow-origin", "*");
  headers.set("access-control-allow-methods", "GET, POST, OPTIONS");
  headers.set("access-control-allow-headers", "authorization, content-type, x-request-id, x-tenant-id");

  return new Response(response.body, {
    status: response.status,
    statusText: response.statusText,
    headers
  });
}

function isCacheAvailable(): boolean {
  return typeof caches !== "undefined" && caches.default !== undefined;
}

function isCacheExplicitlyDisabled(env: Env): boolean {
  return env.CACHE_ENABLED?.toLowerCase() === "false";
}

function defaultTtlSeconds(env: Env): number {
  return env.EDGE_ENV === "prod" ? 300 : 60;
}

function parsePositiveInt(value: string | undefined, fallback: number): number {
  if (!value) {
    return fallback;
  }
  const parsed = Number.parseInt(value, 10);
  return Number.isFinite(parsed) && parsed > 0 ? parsed : fallback;
}

async function sha256Hex(value: string): Promise<string> {
  const bytes = new TextEncoder().encode(value);
  const digest = await crypto.subtle.digest("SHA-256", bytes);
  return [...new Uint8Array(digest)]
    .map((byte) => byte.toString(16).padStart(2, "0"))
    .join("");
}
