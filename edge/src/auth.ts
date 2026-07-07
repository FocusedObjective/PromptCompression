import type { EdgeContext, Env, JsonObject } from "./types";

const AUTH_SCHEMA_VERSION = "edge-auth-v1";
const AUTH_CACHE_ORIGIN = "https://auth.prompt-compression.local";

type AuthDecision = "allowed" | "denied";
type AuthSource = "cache" | "disabled" | "missing" | "permissive" | "stub" | "live";

interface CachedAuthDecision {
  decision: AuthDecision;
  source: AuthSource;
  checkedAt: number;
}

export interface AuthResult {
  allowed: boolean;
  source: AuthSource;
  decision: AuthDecision;
  cache: "hit" | "miss" | "bypass" | "disabled" | "store";
  reason?: string;
}

interface AuthCacheHandle {
  request: Request;
  allowTtlSeconds: number;
  denyTtlSeconds: number;
}

export async function authorizeRequest(
  request: Request,
  env: Env,
  body: JsonObject,
  context: EdgeContext,
  ctx?: ExecutionContext
): Promise<AuthResult> {
  if (env.AUTH_ENABLED?.toLowerCase() === "false") {
    context.auth = "disabled";
    return { allowed: true, source: "disabled", decision: "allowed", cache: "disabled" };
  }

  const apiKey = apiKeyFromRequest(request);
  const tenantId = tenantIdFromRequest(request, body);
  if (!apiKey) {
    const required = env.AUTH_REQUIRED?.toLowerCase() === "true";
    context.auth = required ? "missing" : "permissive";
    return {
      allowed: !required,
      source: "missing",
      decision: required ? "denied" : "allowed",
      cache: "bypass",
      reason: "api_key_missing"
    };
  }

  const handle = await buildAuthCacheHandle(env, apiKey, tenantId);
  if (handle) {
    const cached = await caches.default.match(handle.request);
    if (cached) {
      const decision = await readCachedDecision(cached);
      if (decision) {
        context.auth = decision.decision === "allowed" ? "allowed" : "denied";
        return {
          allowed: decision.decision === "allowed",
          source: "cache",
          decision: decision.decision,
          cache: "hit"
        };
      }
    }
  }

  const validation = validateAuthDecision(request, env, tenantId, apiKey);
  if (ctx) {
    ctx.waitUntil(cacheAuthDecision(handle, validation, env));
  } else {
    await cacheAuthDecision(handle, validation, env);
  }

  context.auth = env.AUTH_MODE === "live" ? "permissive" : "stub-allowed";
  return {
    allowed: true,
    source: env.AUTH_MODE === "live" ? "permissive" : "stub",
    decision: "allowed",
    cache: handle ? "miss" : "bypass",
    reason: env.AUTH_MODE === "live" ? "auth_validation_pending" : "stub_authorization"
  };
}

export function apiKeyFromRequest(request: Request): string | null {
  const explicit = request.headers.get("x-api-key")?.trim();
  if (explicit) {
    return explicit;
  }

  const authorization = request.headers.get("authorization")?.trim();
  const bearerMatch = /^Bearer\s+(.+)$/i.exec(authorization || "");
  if (bearerMatch?.[1]?.trim()) {
    return bearerMatch[1].trim();
  }
  return null;
}

async function validateAuthDecision(
  request: Request,
  env: Env,
  tenantId: string,
  apiKey: string
): Promise<CachedAuthDecision> {
  if (env.AUTH_MODE !== "live") {
    return {
      decision: env.AUTH_STUB_APPROVED?.toLowerCase() === "false" ? "denied" : "allowed",
      source: "stub",
      checkedAt: Date.now()
    };
  }

  const liveDecision = await fetchLiveAuthDecision(request, env, tenantId, apiKey);
  return liveDecision ?? {
    decision: "allowed",
    source: "permissive",
    checkedAt: Date.now()
  };
}

async function fetchLiveAuthDecision(
  request: Request,
  env: Env,
  tenantId: string,
  apiKey: string
): Promise<CachedAuthDecision | null> {
  const baseUrl = env.AUTH_API_BASE_URL || "https://api.usagetap.com";
  const path = env.AUTH_API_PATH || "/v1/api-keys/authorize";
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort("auth_timeout"), parsePositiveInt(env.AUTH_TIMEOUT_MS, 1500));

  try {
    const response = await fetch(new URL(path, baseUrl), {
      method: "POST",
      headers: {
        "authorization": `Bearer ${apiKey}`,
        "content-type": "application/json",
        "x-request-id": request.headers.get("x-request-id") || ""
      },
      body: JSON.stringify({ tenant_id: tenantId }),
      signal: controller.signal
    });

    if (!response.ok) {
      if (response.status === 401 || response.status === 403 || response.status === 404) {
        return { decision: "denied", source: "live", checkedAt: Date.now() };
      }
      return null;
    }

    const body = await response.json() as Record<string, unknown>;
    const allowed = body.allowed === true || body.authorized === true || body.valid === true;
    return {
      decision: allowed ? "allowed" : "denied",
      source: "live",
      checkedAt: Date.now()
    };
  } catch {
    return null;
  } finally {
    clearTimeout(timeout);
  }
}

async function buildAuthCacheHandle(
  env: Env,
  apiKey: string,
  tenantId: string
): Promise<AuthCacheHandle | null> {
  if (!isCacheAvailable()) {
    return null;
  }

  const keyHash = await sha256Hex(`${tenantId}:${apiKey}`);
  return {
    request: new Request(`${AUTH_CACHE_ORIGIN}/${AUTH_SCHEMA_VERSION}/${keyHash}`, { method: "GET" }),
    allowTtlSeconds: parsePositiveInt(env.AUTH_CACHE_TTL_SECONDS, 900),
    denyTtlSeconds: parsePositiveInt(env.AUTH_DENY_CACHE_TTL_SECONDS, 60)
  };
}

async function cacheAuthDecision(
  handle: AuthCacheHandle | null,
  decisionPromise: Promise<CachedAuthDecision>,
  env: Env
): Promise<void> {
  try {
    if (!handle) {
      await decisionPromise;
      return;
    }

    const decision = await decisionPromise;
    const ttl = decision.decision === "allowed" ? handle.allowTtlSeconds : handle.denyTtlSeconds;
    if (ttl <= 0) {
      return;
    }

    await caches.default.put(handle.request, new Response(JSON.stringify(decision), {
      headers: {
        "cache-control": `public, max-age=${ttl}`,
        "content-type": "application/json; charset=utf-8"
      }
    }));
  } catch {
    // Authorization cache failures should not make the permissive rollout path fail closed.
  }
}

async function readCachedDecision(response: Response): Promise<CachedAuthDecision | null> {
  try {
    const body = await response.json() as Partial<CachedAuthDecision>;
    if ((body.decision === "allowed" || body.decision === "denied") && typeof body.checkedAt === "number") {
      return {
        decision: body.decision,
        source: "cache",
        checkedAt: body.checkedAt
      };
    }
  } catch {
    return null;
  }
  return null;
}

function tenantIdFromRequest(request: Request, body: JsonObject): string {
  const bodyTenant = typeof body.tenant_id === "string" && body.tenant_id.trim()
    ? body.tenant_id.trim()
    : null;
  if (bodyTenant) {
    return bodyTenant;
  }
  return request.headers.get("x-tenant-id")?.trim() || "default";
}

function isCacheAvailable(): boolean {
  return typeof caches !== "undefined" && caches.default !== undefined;
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
