import type { EdgeContext, Env, JsonObject, RateLimitBinding } from "./types";

type RateLimitRoute = "health" | "estimate" | "compress" | "messages";
type TenantTier = "blocked" | "strict" | "default" | "trusted";

interface RateLimitPolicy {
  route: RateLimitRoute;
  limit: number;
  periodSeconds: number;
}

interface LocalBucket {
  count: number;
  resetAtMs: number;
}

export interface RateLimitResult {
  allowed: boolean;
  key: string;
  route: RateLimitRoute;
  source: "native" | "local" | "blocked" | "disabled";
  tier: TenantTier;
  retryAfterSeconds?: number;
}

const LOCAL_BUCKETS = new Map<string, LocalBucket>();
const LOCAL_BUCKET_MAX = 10000;

const POLICIES: Record<RateLimitRoute, RateLimitPolicy> = {
  health: { route: "health", limit: 120, periodSeconds: 60 },
  estimate: { route: "estimate", limit: 120, periodSeconds: 60 },
  compress: { route: "compress", limit: 30, periodSeconds: 60 },
  messages: { route: "messages", limit: 20, periodSeconds: 60 }
};

const TIER_MULTIPLIERS: Record<Exclude<TenantTier, "blocked">, number> = {
  strict: 0.5,
  default: 1,
  trusted: 4
};

export async function checkRateLimit(
  request: Request,
  env: Env,
  route: string,
  body: JsonObject | null,
  context: EdgeContext
): Promise<RateLimitResult> {
  const policy = policyForRoute(route);
  const tier = tenantTier(request, body, env);
  const key = await rateLimitKey(request, body, policy.route, tier);

  if (env.RATE_LIMIT_ENABLED?.toLowerCase() === "false") {
    context.rateLimit = "disabled";
    return { allowed: true, key, route: policy.route, source: "disabled", tier };
  }

  if (tier === "blocked") {
    context.rateLimit = "blocked";
    return { allowed: false, key, route: policy.route, source: "blocked", tier };
  }

  const binding = bindingForRoute(env, policy.route, tier);
  if (binding) {
    const result = await binding.limit({ key });
    context.rateLimit = "native";
    return { allowed: result.success, key, route: policy.route, source: "native", tier };
  }

  if (env.RATE_LIMIT_LOCAL_FALLBACK?.toLowerCase() === "false") {
    context.rateLimit = "disabled";
    return { allowed: true, key, route: policy.route, source: "disabled", tier };
  }

  const localResult = checkLocalBucket(key, tieredPolicy(policy, tier), Date.now());
  context.rateLimit = "local";
  if (!localResult.allowed) {
    context.decision = "reject";
  }
  return { ...localResult, key, route: policy.route, source: "local", tier };
}

export async function rateLimitKey(
  request: Request,
  body: JsonObject | null,
  route: RateLimitRoute,
  tier: TenantTier = "default"
): Promise<string> {
  const auth = request.headers.get("authorization");
  if (auth) {
    return `${route}:${tier}:auth:${await sha256Hex(auth)}`;
  }

  const bodyTenant = typeof body?.tenant_id === "string" && body.tenant_id.trim()
    ? body.tenant_id.trim()
    : null;
  if (bodyTenant) {
    return `${route}:${tier}:tenant:${await sha256Hex(bodyTenant)}`;
  }

  const headerTenant = request.headers.get("x-tenant-id");
  if (headerTenant?.trim()) {
    return `${route}:${tier}:tenant:${await sha256Hex(headerTenant.trim())}`;
  }

  const clientIp = request.headers.get("cf-connecting-ip")
    || request.headers.get("x-forwarded-for")
    || "unknown";
  return `${route}:${tier}:ip:${await sha256Hex(clientIp)}`;
}

export function resetLocalRateLimits(): void {
  LOCAL_BUCKETS.clear();
}

function policyForRoute(route: string): RateLimitPolicy {
  if (route === "/health") {
    return POLICIES.health;
  }
  if (route === "/tokens/estimate") {
    return POLICIES.estimate;
  }
  if (route === "/v1/messages/compress") {
    return POLICIES.messages;
  }
  return POLICIES.compress;
}

function bindingForRoute(
  env: Env,
  route: RateLimitRoute,
  tier: TenantTier
): RateLimitBinding | undefined {
  if (route === "health") {
    return env.RATE_LIMIT_HEALTH;
  }
  if (route === "estimate") {
    return env.RATE_LIMIT_ESTIMATE;
  }
  if (route === "messages") {
    if (tier === "strict") {
      return env.RATE_LIMIT_MESSAGES_STRICT ?? env.RATE_LIMIT_MESSAGES;
    }
    if (tier === "trusted") {
      return env.RATE_LIMIT_MESSAGES_TRUSTED ?? env.RATE_LIMIT_MESSAGES;
    }
    return env.RATE_LIMIT_MESSAGES;
  }
  if (tier === "strict") {
    return env.RATE_LIMIT_COMPRESS_STRICT ?? env.RATE_LIMIT_COMPRESS;
  }
  if (tier === "trusted") {
    return env.RATE_LIMIT_COMPRESS_TRUSTED ?? env.RATE_LIMIT_COMPRESS;
  }
  return env.RATE_LIMIT_COMPRESS;
}

function tenantTier(request: Request, body: JsonObject | null, env: Env): TenantTier {
  const tenantId = tenantIdFromRequest(request, body);
  if (!tenantId || !env.RATE_LIMIT_TENANT_TIERS) {
    return "default";
  }

  try {
    const tiers = JSON.parse(env.RATE_LIMIT_TENANT_TIERS) as Record<string, unknown>;
    const tier = tiers[tenantId];
    return isTenantTier(tier) ? tier : "default";
  } catch {
    return "default";
  }
}

function tenantIdFromRequest(request: Request, body: JsonObject | null): string | null {
  const bodyTenant = typeof body?.tenant_id === "string" && body.tenant_id.trim()
    ? body.tenant_id.trim()
    : null;
  if (bodyTenant) {
    return bodyTenant;
  }
  const headerTenant = request.headers.get("x-tenant-id");
  return headerTenant?.trim() || null;
}

function isTenantTier(value: unknown): value is TenantTier {
  return value === "blocked" || value === "strict" || value === "default" || value === "trusted";
}

function tieredPolicy(policy: RateLimitPolicy, tier: TenantTier): RateLimitPolicy {
  if (tier === "blocked") {
    return { ...policy, limit: 0 };
  }
  const multiplier = TIER_MULTIPLIERS[tier];
  return {
    ...policy,
    limit: Math.max(1, Math.floor(policy.limit * multiplier))
  };
}

function checkLocalBucket(
  key: string,
  policy: RateLimitPolicy,
  nowMs: number
): { allowed: boolean; retryAfterSeconds?: number } {
  pruneLocalBuckets(nowMs);

  const resetAtMs = nowMs + policy.periodSeconds * 1000;
  const existing = LOCAL_BUCKETS.get(key);
  if (!existing || existing.resetAtMs <= nowMs) {
    LOCAL_BUCKETS.set(key, { count: 1, resetAtMs });
    return { allowed: true };
  }

  if (existing.count >= policy.limit) {
    return {
      allowed: false,
      retryAfterSeconds: Math.max(1, Math.ceil((existing.resetAtMs - nowMs) / 1000))
    };
  }

  existing.count += 1;
  return { allowed: true };
}

function pruneLocalBuckets(nowMs: number): void {
  if (LOCAL_BUCKETS.size < LOCAL_BUCKET_MAX) {
    return;
  }
  for (const [key, bucket] of LOCAL_BUCKETS) {
    if (bucket.resetAtMs <= nowMs) {
      LOCAL_BUCKETS.delete(key);
    }
  }
}

async function sha256Hex(value: string): Promise<string> {
  const bytes = new TextEncoder().encode(value);
  const digest = await crypto.subtle.digest("SHA-256", bytes);
  return [...new Uint8Array(digest)]
    .map((byte) => byte.toString(16).padStart(2, "0"))
    .join("");
}
