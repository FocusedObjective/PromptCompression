import {
  RequestShapeError,
  buildCompressResponse,
  buildMessagesResponse,
  buildTokenEstimateResponse,
  buildV1CompressResponse,
  evaluateEdgeOriginGate,
  needsOriginForDeterministic,
  resolveTenantProfile,
  shouldUseOrigin,
  validateRequestBody
} from "./deterministic";
import { authorizeRequest } from "./auth";
import { matchEdgeCache, storeEdgeCache } from "./cache";
import { fetchOrigin } from "./origin";
import { checkRateLimit } from "./rateLimit";
import { GPU_POLICY } from "./gpuPolicy";
import { applyCorsPreflightHeaders, applyCorsResponseHeaders } from "./cors";
import type { EdgeContext, Env, JsonObject } from "./types";

const POST_ROUTES = new Set([
  "/compress",
  "/v1/compress",
  "/v1/messages/compress",
  "/tokens/estimate"
]);

export default {
  async fetch(request: Request, env: Env, ctx?: ExecutionContext): Promise<Response> {
    const context: EdgeContext = {
      requestId: request.headers.get("x-request-id") || crypto.randomUUID(),
      startMs: Date.now(),
      decision: "reject",
      cache: "bypass",
      rateLimit: "not-checked",
      auth: "not-checked"
    };

    let response: Response;
    try {
      response = await handleRequest(request, env, context, ctx);
    } catch (error) {
      if (error instanceof RequestShapeError || error instanceof SyntaxError) {
        context.decision = "reject";
        response = jsonResponse(
          { error: "invalid_request", message: error.message || "Invalid JSON request body.", request_id: context.requestId },
          400,
          context
        );
      } else {
        context.decision = "reject";
        console.error(JSON.stringify({
          event: "prompt_compression_edge_error",
          error_type: error instanceof Error ? error.name : "UnknownError",
          method: request.method,
          path: new URL(request.url).pathname
        }));
        response = jsonResponse(
          { error: "edge_error", message: "The edge compression gateway failed.", request_id: context.requestId },
          500,
          context
        );
      }
    }
    logEdgeRequest(request, response, context, env);
    return response;
  }
};

export async function handleRequest(
  request: Request,
  env: Env,
  context: EdgeContext,
  ctx?: ExecutionContext
): Promise<Response> {
  const url = new URL(request.url);
  const route = normalizePath(url.pathname);

  if (route === "/health") {
    if (request.method !== "GET") {
      return methodNotAllowed(context);
    }
    const rateLimit = await checkRateLimit(request, env, route, null, context);
    if (!rateLimit.allowed) {
      return rateLimitedResponse(context, rateLimit.retryAfterSeconds);
    }
    context.decision = "edge-deterministic";
    return jsonResponse({
      status: "ok",
      deployment_version: `edge-${env.EDGE_ENV || "dev"}`,
      deployment_timestamp: "edge-runtime",
      model: "edge-deterministic",
      model_loaded: true,
      edge_env: env.EDGE_ENV || "dev",
      runtime: { compression_policy: GPU_POLICY.schemaVersion }
    }, 200, context);
  }

  if (!POST_ROUTES.has(route)) {
    context.decision = "reject";
    return jsonResponse({ error: "not_found", message: "Route not found.", request_id: context.requestId }, 404, context);
  }

  if (request.method === "OPTIONS") {
    context.decision = "reject";
    return corsPreflightResponse(context);
  }

  if (request.method !== "POST") {
    return methodNotAllowed(context);
  }

  const contentType = request.headers.get("content-type") || "";
  if (!contentType.toLowerCase().includes("application/json")) {
    context.decision = "reject";
    return jsonResponse({ error: "unsupported_media_type", message: "POST routes require application/json.", request_id: context.requestId }, 415, context);
  }

  const maxBodyBytes = parsePositiveInt(env.MAX_BODY_BYTES, 1048576);
  const rawBody = await request.text();
  if (new TextEncoder().encode(rawBody).byteLength > maxBodyBytes) {
    context.decision = "reject";
    return jsonResponse({ error: "request_too_large", message: "Request body exceeds the edge limit.", request_id: context.requestId }, 413, context);
  }

  const body = JSON.parse(rawBody) as JsonObject;
  if (!isObject(body)) {
    throw new RequestShapeError("request body must be a JSON object");
  }
  validateRequestBody(body, route);

  const auth = await authorizeRequest(request, env, body, context, ctx);
  if (!auth.allowed) {
    context.decision = "reject";
    context.cache = "bypass";
    return jsonResponse({
      error: "unauthorized",
      message: auth.reason === "api_key_missing" ? "API key is required." : "API key is not authorized for this tenant.",
      request_id: context.requestId
    }, 401, context);
  }

  if (route === "/tokens/estimate") {
    const rateLimit = await checkRateLimit(request, env, route, body, context);
    if (!rateLimit.allowed) {
      return rateLimitedResponse(context, rateLimit.retryAfterSeconds);
    }
    const { hit, handle } = await matchEdgeCache(request, env, route, body, context);
    if (hit) {
      return hit;
    }
    context.decision = "edge-deterministic";
    return storeEdgeCache(
      handle,
      jsonResponse(buildTokenEstimateResponse(body), 200, context),
      context,
      ctx
    );
  }

  const rateLimit = await checkRateLimit(request, env, route, body, context);
  if (!rateLimit.allowed) {
    return rateLimitedResponse(context, rateLimit.retryAfterSeconds);
  }

  const { hit, handle } = await matchEdgeCache(request, env, route, body, context);
  if (hit) {
    return hit;
  }

  if (shouldUseOrigin(body, route)) {
    const originGate = evaluateEdgeOriginGate(body, route, request.headers.get("x-tenant-id"));
    if (originGate.useOrigin) {
      context.decision = "origin";
      const originResponse = await fetchOrigin(request, env, route, rawBody, context);
      if (originResponse) {
        return storeEdgeCache(handle, originResponse, context, ctx);
      }
      context.decision = "fallback-deterministic";
      return deterministicResponse(route, body, request, context, ["edge_origin_unavailable_deterministic_fallback"]);
    }
    context.decision = "edge-deterministic";
    return storeEdgeCache(
      handle,
      deterministicResponse(route, body, request, context, originGate.reason ? [originGate.reason] : []),
      context,
      ctx
    );
  }

  if (needsOriginForDeterministic(body, route)) {
    if (env.ORIGIN_BASE_URL) {
      context.decision = "origin";
      const originResponse = await fetchOrigin(request, env, route, rawBody, context);
      if (originResponse) {
        return storeEdgeCache(handle, originResponse, context, ctx);
      }
    }
    context.decision = "fallback-deterministic";
    return deterministicResponse(route, body, request, context, ["edge_origin_unavailable_complex_deterministic_fallback"]);
  }

  context.decision = "edge-deterministic";
  return storeEdgeCache(
    handle,
    deterministicResponse(route, body, request, context),
    context,
    ctx
  );
}

function deterministicResponse(
  route: string,
  body: JsonObject,
  request: Request,
  context: EdgeContext,
  warnings: string[] = []
): Response {
  const elapsedMs = Date.now() - context.startMs;
  const tenant = resolveTenantProfile(body, request.headers.get("x-tenant-id"));
  if (route === "/compress") {
    return jsonResponse(buildCompressResponse(body, tenant, elapsedMs, warnings), 200, context);
  }
  if (route === "/v1/compress") {
    return jsonResponse(buildV1CompressResponse(body, tenant, elapsedMs, warnings), 200, context);
  }
  if (route === "/v1/messages/compress") {
    return jsonResponse(buildMessagesResponse(body, tenant, elapsedMs, warnings), 200, context);
  }
  throw new RequestShapeError("unsupported deterministic route");
}

function jsonResponse(body: JsonObject, status: number, context: EdgeContext): Response {
  const headers = new Headers({
    "content-type": "application/json; charset=utf-8",
    "x-request-id": context.requestId,
    "x-edge-decision": context.decision,
    "x-edge-cache": context.cache,
    "x-edge-ratelimit": context.rateLimit,
    "x-edge-auth": context.auth,
    "x-compression-policy": GPU_POLICY.schemaVersion
  });
  applyCorsResponseHeaders(headers);

  return new Response(JSON.stringify(body), { status, headers });
}

function corsPreflightResponse(context: EdgeContext): Response {
  const headers = new Headers({
    "x-request-id": context.requestId,
    "x-edge-decision": context.decision,
    "x-edge-cache": context.cache,
    "x-edge-ratelimit": context.rateLimit,
    "x-edge-auth": context.auth
  });
  applyCorsPreflightHeaders(headers);

  return new Response(null, {
    status: 204,
    headers
  });
}

function methodNotAllowed(context: EdgeContext): Response {
  context.decision = "reject";
  const response = jsonResponse({ error: "method_not_allowed", message: "Method not allowed.", request_id: context.requestId }, 405, context);
  response.headers.set("allow", "GET, POST, OPTIONS");
  return response;
}

function rateLimitedResponse(context: EdgeContext, retryAfterSeconds?: number): Response {
  context.decision = "reject";
  context.cache = "bypass";
  const response = jsonResponse({
    error: "rate_limited",
    message: "Too many compression requests. Retry later.",
    request_id: context.requestId
  }, 429, context);
  if (retryAfterSeconds !== undefined) {
    response.headers.set("retry-after", String(retryAfterSeconds));
  }
  return response;
}

function logEdgeRequest(
  request: Request,
  response: Response,
  context: EdgeContext,
  env: Env
): void {
  if (env.STRUCTURED_LOGS_ENABLED?.toLowerCase() !== "true") {
    return;
  }
  console.log(JSON.stringify({
    event: "prompt_compression_edge_request",
    method: request.method,
    path: new URL(request.url).pathname,
    status: response.status,
    decision: context.decision,
    cache_status: context.cache,
    rate_limit: context.rateLimit,
    auth: context.auth,
    elapsed_ms: Math.max(0, Date.now() - context.startMs),
    compression_policy: GPU_POLICY.schemaVersion
  }));
}

function normalizePath(pathname: string): string {
  if (pathname.length > 1 && pathname.endsWith("/")) {
    return pathname.slice(0, -1);
  }
  return pathname;
}

function parsePositiveInt(value: string | undefined, fallback: number): number {
  if (!value) {
    return fallback;
  }
  const parsed = Number.parseInt(value, 10);
  return Number.isFinite(parsed) && parsed > 0 ? parsed : fallback;
}

function isObject(value: unknown): value is JsonObject {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}
