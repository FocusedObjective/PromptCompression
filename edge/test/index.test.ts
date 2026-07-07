import { afterEach, describe, expect, it, vi } from "vitest";
import worker from "../src/index";
import { resetLocalRateLimits } from "../src/rateLimit";

const LONG_MODEL_TEXT = Array.from({ length: 80 }, (_, index) => {
  return `This is reusable operational context sentence ${index} with enough ordinary prose for model compression decisions.`;
}).join(" ");

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  resetLocalRateLimits();
});

describe("worker routes", () => {
  it("returns edge health in the Cloud Run health shape", async () => {
    const response = await worker.fetch(new Request("https://edge.test/health"), { EDGE_ENV: "test" });
    const body = await response.json() as Record<string, unknown>;

    expect(response.status).toBe(200);
    expect(body.status).toBe("ok");
    expect(body.model_loaded).toBe(true);
    expect(response.headers.get("x-edge-decision")).toBe("edge-deterministic");
    expect(response.headers.get("x-edge-ratelimit")).toBe("local");
  });

  it("preserves caller request IDs in edge responses", async () => {
    const response = await worker.fetch(new Request("https://edge.test/health", {
      headers: { "x-request-id": "request_123" }
    }), { EDGE_ENV: "test" });

    expect(response.headers.get("x-request-id")).toBe("request_123");
  });

  it("rejects unsupported routes", async () => {
    const response = await worker.fetch(new Request("https://edge.test/docs"), {});
    const body = await response.json() as Record<string, unknown>;

    expect(response.status).toBe(404);
    expect(body.error).toBe("not_found");
  });

  it("allows CORS preflight for supported POST routes", async () => {
    const response = await worker.fetch(new Request("https://edge.test/compress", {
      method: "OPTIONS"
    }), {});

    expect(response.status).toBe(204);
    expect(response.headers.get("access-control-allow-origin")).toBe("*");
    expect(response.headers.get("access-control-allow-methods")).toContain("POST");
  });

  it("rejects unsupported methods", async () => {
    const response = await worker.fetch(new Request("https://edge.test/compress", {
      method: "GET"
    }), {});
    const body = await response.json() as Record<string, unknown>;

    expect(response.status).toBe(405);
    expect(response.headers.get("allow")).toContain("POST");
    expect(body.error).toBe("method_not_allowed");
  });

  it("rejects POST requests without application/json", async () => {
    const response = await worker.fetch(new Request("https://edge.test/compress", {
      method: "POST",
      headers: { "content-type": "text/plain" },
      body: "hello"
    }), {});
    const body = await response.json() as Record<string, unknown>;

    expect(response.status).toBe(415);
    expect(body.error).toBe("unsupported_media_type");
  });

  it("rejects invalid JSON", async () => {
    const response = await worker.fetch(new Request("https://edge.test/compress", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: "{"
    }), {});
    const body = await response.json() as Record<string, unknown>;

    expect(response.status).toBe(400);
    expect(body.error).toBe("invalid_request");
  });

  it("rejects oversized request bodies before routing", async () => {
    const response = await worker.fetch(new Request("https://edge.test/compress", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ text: "abcdef", mode: "deterministic" })
    }), { MAX_BODY_BYTES: "8" });
    const body = await response.json() as Record<string, unknown>;

    expect(response.status).toBe(413);
    expect(body.error).toBe("request_too_large");
  });

  it("rejects invalid compression modes instead of silently changing behavior", async () => {
    const response = await worker.fetch(new Request("https://edge.test/compress", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ text: "hello", mode: "surprise" })
    }), {});
    const body = await response.json() as Record<string, unknown>;

    expect(response.status).toBe(400);
    expect(body.error).toBe("invalid_request");
  });

  it("answers token estimates at the edge", async () => {
    const response = await worker.fetch(new Request("https://edge.test/tokens/estimate", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ text: "hello, world" })
    }), {});
    const body = await response.json() as Record<string, unknown>;

    expect(response.status).toBe(200);
    expect(response.headers.get("x-edge-decision")).toBe("edge-deterministic");
    expect(body.tokens).toBe(3);
    expect(body.tokenizer_backed).toBe(false);
  });

  it("allows missing API keys while auth is permissive", async () => {
    const response = await worker.fetch(new Request("https://edge.test/compress", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ text: "hello\n\n\nworld", mode: "deterministic" })
    }), { AUTH_REQUIRED: "false" });
    const body = await response.json() as Record<string, unknown>;

    expect(response.status).toBe(200);
    expect(response.headers.get("x-edge-auth")).toBe("permissive");
    expect(body.compressed_text).toBe("hello\n\nworld");
  });

  it("can require an API key when auth enforcement is enabled", async () => {
    const response = await worker.fetch(new Request("https://edge.test/compress", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ text: "hello", mode: "deterministic" })
    }), { AUTH_REQUIRED: "true" });
    const body = await response.json() as Record<string, unknown>;

    expect(response.status).toBe(401);
    expect(response.headers.get("x-edge-auth")).toBe("missing");
    expect(body.error).toBe("unauthorized");
  });

  it("caches stub authorization decisions and denies future requests before compression cache", async () => {
    installMemoryCache();
    const rawBody = JSON.stringify({ text: "hello", mode: "deterministic", tenant_id: "tenant_denied" });
    const first = await worker.fetch(new Request("https://edge.test/compress", {
      method: "POST",
      headers: {
        authorization: "Bearer denied-key",
        "content-type": "application/json"
      },
      body: rawBody
    }), {
      AUTH_STUB_APPROVED: "false",
      CACHE_ENABLED: "true"
    });
    const second = await worker.fetch(new Request("https://edge.test/compress", {
      method: "POST",
      headers: {
        authorization: "Bearer denied-key",
        "content-type": "application/json"
      },
      body: rawBody
    }), {
      AUTH_STUB_APPROVED: "false",
      CACHE_ENABLED: "true"
    });
    const secondBody = await second.json() as Record<string, unknown>;

    expect(first.status).toBe(200);
    expect(first.headers.get("x-edge-auth")).toBe("stub-allowed");
    expect(second.status).toBe(401);
    expect(second.headers.get("x-edge-auth")).toBe("denied");
    expect(second.headers.get("x-edge-cache")).toBe("bypass");
    expect(secondBody.error).toBe("unauthorized");
  });

  it("proxies model requests to the configured origin", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(new Response(
      JSON.stringify({ compressed_text: "origin result" }),
      { status: 200, headers: { "content-type": "application/json" } }
    ));
    const rawBody = JSON.stringify({ text: LONG_MODEL_TEXT, mode: "model_force" });
    const response = await worker.fetch(new Request("https://edge.test/compress", {
      method: "POST",
      headers: {
        authorization: "Bearer api-key",
        "content-type": "application/json",
        "x-request-id": "request_origin",
        "x-tenant-id": "tenant_header"
      },
      body: rawBody
    }), {
      ORIGIN_BASE_URL: "https://origin.test/base/",
      ORIGIN_AUTH_MODE: "shared-secret",
      ORIGIN_SHARED_SECRET: "secret"
    });
    const body = await response.json() as Record<string, unknown>;
    const originUrl = fetchMock.mock.calls[0]?.[0] as URL;
    const originInit = fetchMock.mock.calls[0]?.[1] as RequestInit;
    const originHeaders = new Headers(originInit.headers);

    expect(response.status).toBe(200);
    expect(response.headers.get("x-edge-decision")).toBe("origin");
    expect(response.headers.get("x-origin-status")).toBe("200");
    expect(body.compressed_text).toBe("origin result");
    expect(originUrl.toString()).toBe("https://origin.test/compress");
    expect(originHeaders.get("authorization")).toBe("Bearer api-key");
    expect(originHeaders.get("x-tenant-id")).toBe("tenant_header");
    expect(originHeaders.get("x-origin-shared-secret")).toBe("secret");
    expect(originInit.body).toBe(rawBody);
  });

  it("passes origin 4xx responses through to the client", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(new Response(
      JSON.stringify({ detail: "origin validation error" }),
      { status: 422, headers: { "content-type": "application/json" } }
    ));

    const response = await worker.fetch(new Request("https://edge.test/compress", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ text: LONG_MODEL_TEXT, mode: "model_force" })
    }), { ORIGIN_BASE_URL: "https://origin.test" });
    const body = await response.json() as Record<string, unknown>;

    expect(response.status).toBe(422);
    expect(response.headers.get("x-edge-decision")).toBe("origin");
    expect(response.headers.get("x-origin-status")).toBe("422");
    expect(body.detail).toBe("origin validation error");
  });

  it("falls back to deterministic response when origin fetch fails", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockRejectedValueOnce(new Error("origin down"));
    const response = await worker.fetch(new Request("https://edge.test/compress", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ text: `${LONG_MODEL_TEXT}\n\n\n${LONG_MODEL_TEXT}`, mode: "model_force" })
    }), { ORIGIN_BASE_URL: "https://origin.test" });
    const body = await response.json() as Record<string, unknown>;

    expect(response.status).toBe(200);
    expect(response.headers.get("x-edge-decision")).toBe("fallback-deterministic");
    expect(body.compressed_text).toBe(`${LONG_MODEL_TEXT}\n\n${LONG_MODEL_TEXT}`);
    expect(body.warnings).toContain("edge_origin_unavailable_deterministic_fallback");
    expect(fetchMock).toHaveBeenCalledOnce();
  });

  it("falls back to deterministic response when origin returns 5xx", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(new Response("origin down", {
      status: 503
    }));

    const response = await worker.fetch(new Request("https://edge.test/v1/compress", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({
        input: `${LONG_MODEL_TEXT}\n\n\n${LONG_MODEL_TEXT}`,
        compression_settings: { mode: "model_force" }
      })
    }), { ORIGIN_BASE_URL: "https://origin.test" });
    const body = await response.json() as Record<string, unknown>;

    expect(response.status).toBe(200);
    expect(response.headers.get("x-edge-decision")).toBe("fallback-deterministic");
    expect(body.output).toBe(`${LONG_MODEL_TEXT}\n\n${LONG_MODEL_TEXT}`);
    expect(body.warnings).toContain("edge_origin_unavailable_deterministic_fallback");
  });

  it("skips origin for low-value model_auto requests", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch");

    const response = await worker.fetch(new Request("https://edge.test/v1/compress", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({
        input: "Short prompt that is not worth a Cloud Run model call.",
        compression_settings: { mode: "model_auto" }
      })
    }), { ORIGIN_BASE_URL: "https://origin.test" });
    const body = await response.json() as Record<string, unknown>;

    expect(response.status).toBe(200);
    expect(response.headers.get("x-edge-decision")).toBe("edge-deterministic");
    expect(body.output).toBe("Short prompt that is not worth a Cloud Run model call.");
    expect(body.warnings).toContain("edge_skipped_no_candidate_prose");
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("delegates complex deterministic inputs to origin when configured", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(new Response(
      JSON.stringify({ output: "origin deterministic result" }),
      { status: 200, headers: { "content-type": "application/json" } }
    ));

    const response = await worker.fetch(new Request("https://edge.test/v1/compress", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ input: "{\"schema\":{\"type\":\"object\",\"properties\":{\"id\":{\"type\":\"string\"}}}}" })
    }), { ORIGIN_BASE_URL: "https://origin.test" });
    const body = await response.json() as Record<string, unknown>;

    expect(response.status).toBe(200);
    expect(response.headers.get("x-edge-decision")).toBe("origin");
    expect(response.headers.get("x-origin-status")).toBe("200");
    expect(body.output).toBe("origin deterministic result");
    expect(fetchMock).toHaveBeenCalledOnce();
  });

  it("handles supported JSON record arrays at the edge", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch");
    const response = await worker.fetch(new Request("https://edge.test/v1/compress", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({
        input: `Please review:
{
  "users": [
    {"id": 1, "name": "Alice", "role": "admin"},
    {"id": 2, "name": "Bob", "role": "user"}
  ]
}`
      })
    }), { ORIGIN_BASE_URL: "https://origin.test" });
    const body = await response.json() as Record<string, unknown>;

    expect(response.status).toBe(200);
    expect(response.headers.get("x-edge-decision")).toBe("edge-deterministic");
    expect(String(body.output)).toContain("users[2]{id,name,role}:");
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("delegates tenant-profile deterministic inputs to origin when configured", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(new Response(
      JSON.stringify({ output: "origin tenant result" }),
      { status: 200, headers: { "content-type": "application/json" } }
    ));

    const response = await worker.fetch(new Request("https://edge.test/v1/compress", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({
        input: "Reusable preamble. Keep this.",
        tenant_profile: { force_drop_phrases: ["Reusable preamble. "] }
      })
    }), { ORIGIN_BASE_URL: "https://origin.test" });
    const body = await response.json() as Record<string, unknown>;

    expect(response.status).toBe(200);
    expect(response.headers.get("x-edge-decision")).toBe("origin");
    expect(response.headers.get("x-origin-status")).toBe("200");
    expect(body.output).toBe("origin tenant result");
    expect(fetchMock).toHaveBeenCalledOnce();
  });

  it("still returns deterministic fallback for complex inputs when origin is unavailable", async () => {
    vi.spyOn(globalThis, "fetch").mockRejectedValueOnce(new Error("origin unavailable"));

    const response = await worker.fetch(new Request("https://edge.test/v1/compress", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({
        input: `Please review:
{
  "account": {"id": "acct_1", "plan": "enterprise"},
  "region": "us-west-2"
}`
      })
    }), { ORIGIN_BASE_URL: "https://origin.test" });
    const body = await response.json() as Record<string, unknown>;

    expect(response.status).toBe(200);
    expect(response.headers.get("x-edge-decision")).toBe("fallback-deterministic");
    expect(body.output).toBe("Please review:\n{\n  \"account\": {\"id\": \"acct_1\", \"plan\": \"enterprise\"},\n  \"region\": \"us-west-2\"\n}");
    expect(body.warnings).toContain("edge_origin_unavailable_complex_deterministic_fallback");
  });

  it("labels tenant-profile deterministic work as fallback when origin is unavailable", async () => {
    const response = await worker.fetch(new Request("https://edge.test/v1/compress", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({
        input: "Reusable preamble. Keep this.",
        tenant_profile: { force_drop_phrases: ["Reusable preamble. "] }
      })
    }), {});
    const body = await response.json() as Record<string, unknown>;

    expect(response.status).toBe(200);
    expect(response.headers.get("x-edge-decision")).toBe("fallback-deterministic");
    expect(response.headers.get("x-edge-cache")).toBe("bypass");
    expect(body.output).toBe("Keep this.");
    expect(body.warnings).toContain("edge_origin_unavailable_complex_deterministic_fallback");
  });

  it("stores and serves edge deterministic responses from Cloudflare cache", async () => {
    installMemoryCache();
    const rawBody = JSON.stringify({ input: "hello\n\n\nworld" });
    const first = await worker.fetch(new Request("https://edge.test/v1/compress", {
      method: "POST",
      headers: { "content-type": "application/json", "x-request-id": "first" },
      body: rawBody
    }), { CACHE_ENABLED: "true", CACHE_TTL_SECONDS: "120" });
    const second = await worker.fetch(new Request("https://edge.test/v1/compress", {
      method: "POST",
      headers: { "content-type": "application/json", "x-request-id": "second" },
      body: rawBody
    }), { CACHE_ENABLED: "true", CACHE_TTL_SECONDS: "120" });
    const secondBody = await second.json() as Record<string, unknown>;

    expect(first.headers.get("x-edge-cache")).toBe("store");
    expect(second.headers.get("x-edge-decision")).toBe("cache-hit");
    expect(second.headers.get("x-edge-cache")).toBe("hit");
    expect(second.headers.get("x-request-id")).toBe("second");
    expect(secondBody.output).toBe("hello\n\nworld");
  });

  it("stores and serves origin responses from Cloudflare cache", async () => {
    installMemoryCache();
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(new Response(
      JSON.stringify({ compressed_text: "cached origin result" }),
      { status: 200, headers: { "content-type": "application/json" } }
    ));
    const rawBody = JSON.stringify({ text: LONG_MODEL_TEXT, mode: "model_force" });

    const first = await worker.fetch(new Request("https://edge.test/compress", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: rawBody
    }), { ORIGIN_BASE_URL: "https://origin.test", CACHE_ENABLED: "true" });
    const second = await worker.fetch(new Request("https://edge.test/compress", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: rawBody
    }), { ORIGIN_BASE_URL: "https://origin.test", CACHE_ENABLED: "true" });
    const secondBody = await second.json() as Record<string, unknown>;

    expect(first.headers.get("x-edge-decision")).toBe("origin");
    expect(first.headers.get("x-edge-cache")).toBe("store");
    expect(second.headers.get("x-edge-decision")).toBe("cache-hit");
    expect(second.headers.get("x-edge-cache")).toBe("hit");
    expect(secondBody.compressed_text).toBe("cached origin result");
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("bypasses cache when Cache-Control no-store is present", async () => {
    installMemoryCache();
    const fetchMock = vi.spyOn(globalThis, "fetch")
      .mockResolvedValueOnce(new Response(JSON.stringify({ compressed_text: "first" }), {
        status: 200,
        headers: { "content-type": "application/json" }
      }))
      .mockResolvedValueOnce(new Response(JSON.stringify({ compressed_text: "second" }), {
        status: 200,
        headers: { "content-type": "application/json" }
      }));
    const rawBody = JSON.stringify({ text: LONG_MODEL_TEXT, mode: "model_force" });

    const first = await worker.fetch(new Request("https://edge.test/compress", {
      method: "POST",
      headers: { "cache-control": "no-store", "content-type": "application/json" },
      body: rawBody
    }), { ORIGIN_BASE_URL: "https://origin.test", CACHE_ENABLED: "true" });
    const second = await worker.fetch(new Request("https://edge.test/compress", {
      method: "POST",
      headers: { "cache-control": "no-store", "content-type": "application/json" },
      body: rawBody
    }), { ORIGIN_BASE_URL: "https://origin.test", CACHE_ENABLED: "true" });
    const secondBody = await second.json() as Record<string, unknown>;

    expect(first.headers.get("x-edge-cache")).toBe("bypass");
    expect(second.headers.get("x-edge-cache")).toBe("bypass");
    expect(secondBody.compressed_text).toBe("second");
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it("does not cache degraded fallback responses", async () => {
    installMemoryCache();
    const fetchMock = vi.spyOn(globalThis, "fetch")
      .mockResolvedValueOnce(new Response("down", { status: 503 }))
      .mockResolvedValueOnce(new Response("down", { status: 503 }));
    const rawBody = JSON.stringify({ text: `${LONG_MODEL_TEXT}\n\n\n${LONG_MODEL_TEXT}`, mode: "model_force" });

    const first = await worker.fetch(new Request("https://edge.test/compress", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: rawBody
    }), { ORIGIN_BASE_URL: "https://origin.test", CACHE_ENABLED: "true" });
    const second = await worker.fetch(new Request("https://edge.test/compress", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: rawBody
    }), { ORIGIN_BASE_URL: "https://origin.test", CACHE_ENABLED: "true" });

    expect(first.headers.get("x-edge-decision")).toBe("fallback-deterministic");
    expect(first.headers.get("x-edge-cache")).toBe("miss");
    expect(second.headers.get("x-edge-decision")).toBe("fallback-deterministic");
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it("uses native Cloudflare rate limiting bindings when configured", async () => {
    const limit = vi.fn().mockResolvedValueOnce({ success: false });
    const response = await worker.fetch(new Request("https://edge.test/compress", {
      method: "POST",
      headers: {
        "content-type": "application/json",
        "x-tenant-id": "tenant_1"
      },
      body: JSON.stringify({ text: "hello", mode: "deterministic" })
    }), {
      RATE_LIMIT_COMPRESS: { limit }
    });
    const body = await response.json() as Record<string, unknown>;

    expect(response.status).toBe(429);
    expect(response.headers.get("x-edge-decision")).toBe("reject");
    expect(response.headers.get("x-edge-ratelimit")).toBe("native");
    expect(body.error).toBe("rate_limited");
    expect(limit).toHaveBeenCalledOnce();
    expect(limit.mock.calls[0][0].key).toMatch(/^compress:default:tenant:/);
  });

  it("rate limits by tenant before cache hits are served", async () => {
    installMemoryCache();
    let finalResponse: Response | null = null;

    for (let index = 0; index < 31; index += 1) {
      finalResponse = await worker.fetch(new Request("https://edge.test/compress", {
        method: "POST",
        headers: {
          "content-type": "application/json",
          "x-tenant-id": "tenant_rate_limited"
        },
        body: JSON.stringify({ text: "hello", mode: "deterministic" })
      }), {
        RATE_LIMIT_LOCAL_FALLBACK: "true"
      });
    }

    expect(finalResponse).not.toBeNull();
    expect(finalResponse?.status).toBe(429);
    expect(finalResponse?.headers.get("x-edge-decision")).toBe("reject");
    expect(finalResponse?.headers.get("x-edge-ratelimit")).toBe("local");
    expect(finalResponse?.headers.get("retry-after")).toBe("60");
  });

  it("can disable rate limiting through env config", async () => {
    let finalResponse: Response | null = null;
    for (let index = 0; index < 35; index += 1) {
      finalResponse = await worker.fetch(new Request("https://edge.test/compress", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ text: "hello", mode: "deterministic" })
      }), {
        RATE_LIMIT_ENABLED: "false"
      });
    }

    expect(finalResponse?.status).toBe(200);
    expect(finalResponse?.headers.get("x-edge-decision")).toBe("edge-deterministic");
    expect(finalResponse?.headers.get("x-edge-ratelimit")).toBe("disabled");
  });
});

function installMemoryCache(): void {
  const store = new Map<string, Response>();
  vi.stubGlobal("caches", {
    default: {
      match: async (request: Request) => store.get(request.url)?.clone() ?? null,
      put: async (request: Request, response: Response) => {
        store.set(request.url, response.clone());
      }
    }
  });
}
