import { afterEach, describe, expect, it } from "vitest";
import { checkRateLimit, rateLimitKey, resetLocalRateLimits } from "../src/rateLimit";

afterEach(() => {
  resetLocalRateLimits();
});

describe("tenant-aware rate limiting", () => {
  it("prefers authorization hash over tenant and IP keys", async () => {
    const key = await rateLimitKey(new Request("https://edge.test/compress", {
      headers: {
        authorization: "Bearer secret",
        "cf-connecting-ip": "203.0.113.10",
        "x-tenant-id": "tenant_header"
      }
    }), { tenant_id: "tenant_body" }, "compress");

    expect(key).toMatch(/^compress:default:auth:/);
    expect(key).not.toContain("secret");
    expect(key).not.toContain("tenant_body");
  });

  it("uses body tenant before header tenant", async () => {
    const bodyTenantKey = await rateLimitKey(new Request("https://edge.test/compress", {
      headers: { "x-tenant-id": "tenant_header" }
    }), { tenant_id: "tenant_body" }, "compress");
    const headerTenantKey = await rateLimitKey(new Request("https://edge.test/compress", {
      headers: { "x-tenant-id": "tenant_header" }
    }), {}, "compress");

    expect(bodyTenantKey).toMatch(/^compress:default:tenant:/);
    expect(headerTenantKey).toMatch(/^compress:default:tenant:/);
    expect(bodyTenantKey).not.toBe(headerTenantKey);
  });

  it("falls back to client IP when no tenant or auth is present", async () => {
    const key = await rateLimitKey(new Request("https://edge.test/compress", {
      headers: { "cf-connecting-ip": "203.0.113.10" }
    }), {}, "compress");

    expect(key).toMatch(/^compress:default:ip:/);
    expect(key).not.toContain("203.0.113.10");
  });

  it("applies stricter message route local fallback policy", async () => {
    const context = {
      requestId: "request_1",
      startMs: Date.now(),
      decision: "reject" as const,
      cache: "bypass" as const,
      rateLimit: "not-checked" as const
    };
    let result = await checkRateLimit(
      new Request("https://edge.test/v1/messages/compress", {
        headers: { "x-tenant-id": "tenant_1" }
      }),
      {},
      "/v1/messages/compress",
      {},
      context
    );

    for (let index = 0; index < 20; index += 1) {
      result = await checkRateLimit(
        new Request("https://edge.test/v1/messages/compress", {
          headers: { "x-tenant-id": "tenant_1" }
        }),
        {},
        "/v1/messages/compress",
        {},
        context
      );
    }

    expect(result.allowed).toBe(false);
    expect(result.retryAfterSeconds).toBe(60);
  });

  it("blocks tenants configured as blocked", async () => {
    const context = {
      requestId: "request_1",
      startMs: Date.now(),
      decision: "reject" as const,
      cache: "bypass" as const,
      rateLimit: "not-checked" as const
    };

    const result = await checkRateLimit(
      new Request("https://edge.test/compress", {
        headers: { "x-tenant-id": "tenant_blocked" }
      }),
      { RATE_LIMIT_TENANT_TIERS: "{\"tenant_blocked\":\"blocked\"}" },
      "/compress",
      {},
      context
    );

    expect(result.allowed).toBe(false);
    expect(result.source).toBe("blocked");
    expect(result.tier).toBe("blocked");
    expect(context.rateLimit).toBe("blocked");
  });

  it("uses strict and trusted native bindings by tenant tier", async () => {
    const context = {
      requestId: "request_1",
      startMs: Date.now(),
      decision: "reject" as const,
      cache: "bypass" as const,
      rateLimit: "not-checked" as const
    };
    const strictCalls: Array<{ key: string }> = [];
    const trustedCalls: Array<{ key: string }> = [];

    const strictResult = await checkRateLimit(
      new Request("https://edge.test/compress", {
        headers: { "x-tenant-id": "tenant_strict" }
      }),
      {
        RATE_LIMIT_TENANT_TIERS: "{\"tenant_strict\":\"strict\",\"tenant_trusted\":\"trusted\"}",
        RATE_LIMIT_COMPRESS_STRICT: {
          limit: async (options) => {
            strictCalls.push(options);
            return { success: true };
          }
        },
        RATE_LIMIT_COMPRESS_TRUSTED: {
          limit: async (options) => {
            trustedCalls.push(options);
            return { success: true };
          }
        }
      },
      "/compress",
      {},
      context
    );
    const trustedResult = await checkRateLimit(
      new Request("https://edge.test/compress", {
        headers: { "x-tenant-id": "tenant_trusted" }
      }),
      {
        RATE_LIMIT_TENANT_TIERS: "{\"tenant_strict\":\"strict\",\"tenant_trusted\":\"trusted\"}",
        RATE_LIMIT_COMPRESS_STRICT: {
          limit: async (options) => {
            strictCalls.push(options);
            return { success: true };
          }
        },
        RATE_LIMIT_COMPRESS_TRUSTED: {
          limit: async (options) => {
            trustedCalls.push(options);
            return { success: true };
          }
        }
      },
      "/compress",
      {},
      context
    );

    expect(strictResult.tier).toBe("strict");
    expect(trustedResult.tier).toBe("trusted");
    expect(strictCalls[0].key).toMatch(/^compress:strict:tenant:/);
    expect(trustedCalls[0].key).toMatch(/^compress:trusted:tenant:/);
  });
});
