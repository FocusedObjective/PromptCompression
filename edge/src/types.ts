export interface Env {
  EDGE_ENV?: string;
  AUTH_ENABLED?: string;
  AUTH_REQUIRED?: string;
  AUTH_MODE?: "stub" | "live";
  AUTH_API_BASE_URL?: string;
  AUTH_API_PATH?: string;
  AUTH_TIMEOUT_MS?: string;
  AUTH_CACHE_TTL_SECONDS?: string;
  AUTH_DENY_CACHE_TTL_SECONDS?: string;
  AUTH_STUB_APPROVED?: string;
  ORIGIN_BASE_URL?: string;
  ORIGIN_AUTH_MODE?: "none" | "shared-secret" | "google-iam";
  ORIGIN_SHARED_SECRET?: string;
  MAX_BODY_BYTES?: string;
  ORIGIN_TIMEOUT_MS?: string;
  CACHE_ENABLED?: string;
  CACHE_TTL_SECONDS?: string;
  RATE_LIMIT_ENABLED?: string;
  RATE_LIMIT_LOCAL_FALLBACK?: string;
  RATE_LIMIT_TENANT_TIERS?: string;
  RATE_LIMIT_HEALTH?: RateLimitBinding;
  RATE_LIMIT_ESTIMATE?: RateLimitBinding;
  RATE_LIMIT_COMPRESS?: RateLimitBinding;
  RATE_LIMIT_COMPRESS_STRICT?: RateLimitBinding;
  RATE_LIMIT_COMPRESS_TRUSTED?: RateLimitBinding;
  RATE_LIMIT_MESSAGES?: RateLimitBinding;
  RATE_LIMIT_MESSAGES_STRICT?: RateLimitBinding;
  RATE_LIMIT_MESSAGES_TRUSTED?: RateLimitBinding;
}

export interface RateLimitBinding {
  limit(options: { key: string }): Promise<{ success: boolean }>;
}

export interface TenantProfile {
  tenantId: string;
  profileId: string;
  source: "default" | "api";
  minRate: number | null;
  forceDropPhrases: string[];
}

export interface EdgeContext {
  requestId: string;
  startMs: number;
  decision: "origin" | "edge-deterministic" | "fallback-deterministic" | "cache-hit" | "reject";
  cache: "hit" | "miss" | "store" | "bypass" | "disabled";
  rateLimit: "native" | "local" | "blocked" | "disabled" | "not-checked";
  auth: "allowed" | "denied" | "permissive" | "stub-allowed" | "missing" | "disabled" | "not-checked";
}

export type JsonObject = Record<string, unknown>;
