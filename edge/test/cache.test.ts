import { describe, expect, it } from "vitest";
import { buildCacheKeyParts, stableJson } from "../src/cache";

describe("edge cache keys", () => {
  it("canonicalizes object keys before hashing", () => {
    expect(stableJson({ b: 2, a: { d: 4, c: 3 } })).toBe(
      "{\"a\":{\"c\":3,\"d\":4},\"b\":2}"
    );
  });

  it("builds stable key parts without raw auth material", async () => {
    const first = await buildCacheKeyParts(new Request("https://edge.test/v1/compress", {
      headers: {
        authorization: "Bearer secret-1",
        "x-tenant-id": "tenant_1"
      }
    }), "/v1/compress", { input: "hello", model: "bear-2" });
    const second = await buildCacheKeyParts(new Request("https://edge.test/v1/compress", {
      headers: {
        authorization: "Bearer secret-2",
        "x-tenant-id": "tenant_1"
      }
    }), "/v1/compress", { model: "bear-2", input: "hello" });

    expect(stableJson(first)).toBe(stableJson(second));
    expect(stableJson(first)).not.toContain("secret");
  });

  it("separates tenants in cache key parts", async () => {
    const first = await buildCacheKeyParts(new Request("https://edge.test/v1/compress", {
      headers: { "x-tenant-id": "tenant_1" }
    }), "/v1/compress", { input: "hello" });
    const second = await buildCacheKeyParts(new Request("https://edge.test/v1/compress", {
      headers: { "x-tenant-id": "tenant_2" }
    }), "/v1/compress", { input: "hello" });

    expect(stableJson(first)).not.toBe(stableJson(second));
  });
});
