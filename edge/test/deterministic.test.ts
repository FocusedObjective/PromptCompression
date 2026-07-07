import { describe, expect, it } from "vitest";
import {
  buildCompressResponse,
  buildMessagesResponse,
  buildV1CompressResponse,
  deterministicText,
  evaluateEdgeOriginGate,
  needsOriginForDeterministic,
  resolveTenantProfile,
  shouldUseOrigin,
  validateRequestBody
} from "../src/deterministic";

const LONG_MODEL_TEXT = Array.from({ length: 80 }, (_, index) => {
  return `This is reusable operational context sentence ${index} with enough ordinary prose for model compression decisions.`;
}).join(" ");

describe("deterministic edge compression", () => {
  it("uses model origin only for model modes", () => {
    expect(shouldUseOrigin({ text: "hello" }, "/compress")).toBe(true);
    expect(shouldUseOrigin({ text: "hello", mode: "deterministic" }, "/compress")).toBe(false);
    expect(shouldUseOrigin({ input: "hello" }, "/v1/compress")).toBe(false);
    expect(shouldUseOrigin({ input: "hello", compression_settings: { mode: "model_force" } }, "/v1/compress")).toBe(true);
  });

  it("validates request shapes for supported routes", () => {
    expect(() => validateRequestBody({ text: "hello", mode: "deterministic" }, "/compress")).not.toThrow();
    expect(() => validateRequestBody({ input: "hello" }, "/v1/compress")).not.toThrow();
    expect(() => validateRequestBody({ messages: [{ role: "user", content: "hello" }] }, "/v1/messages/compress")).not.toThrow();
    expect(() => validateRequestBody({ text: "hello" }, "/tokens/estimate")).not.toThrow();

    expect(() => validateRequestBody({ text: "", mode: "deterministic" }, "/compress")).toThrow("text must be a non-empty string");
    expect(() => validateRequestBody({ text: "hello", mode: "bad" }, "/compress")).toThrow("mode must be one of");
    expect(() => validateRequestBody({ input: "hello", compression_settings: { aggressiveness: 2 } }, "/v1/compress")).toThrow("compression_settings.aggressiveness");
    expect(() => validateRequestBody({ messages: [{ content: "hello" }] }, "/v1/messages/compress")).toThrow("message.role");
    expect(() => validateRequestBody({ text: 12 }, "/tokens/estimate")).toThrow("text must be a string");
  });

  it("applies only safe deterministic text edits", () => {
    const tenant = resolveTenantProfile({}, null);

    expect(deterministicText("  <nocompress>Keep me</nocompress>  \n\n\n", tenant)).toBe("Keep me");
  });

  it("does not rewrite whitespace inside protected code-like blocks", () => {
    const tenant = resolveTenantProfile({}, null);
    const input = "Before\n```json\n{  \"id\": 1 }\n```\n\n\nAfter  ";

    expect(deterministicText(input, tenant)).toBe(input);
  });

  it.each([
    {
      name: "collapses repeated blank lines and trims trailing line whitespace",
      input: "Alpha   \n\n\nBeta\t\n",
      output: "Alpha\n\nBeta"
    },
    {
      name: "removes nocompress wrapper tags without rewriting enclosed text",
      input: "Start\n<nocompress>Exact Terms</nocompress>\n\n\nEnd",
      output: "Start\nExact Terms\n\nEnd"
    },
    {
      name: "keeps markdown tables on the edge-safe path",
      input: "| A | B |  \n| - | - |\n\n\n| 1 | 2 |",
      output: "| A | B |\n| - | - |\n\n| 1 | 2 |"
    },
    {
      name: "keeps markdown lists on the edge-safe path",
      input: "- first  \n\n\n- second  ",
      output: "- first\n\n- second"
    }
  ])("handles generic deterministic text: $name", ({ input, output }) => {
    const tenant = resolveTenantProfile({}, null);

    expect(deterministicText(input, tenant)).toBe(output);
  });

  it("routes complex deterministic work to origin when possible", () => {
    expect(needsOriginForDeterministic({ text: "plain text", mode: "deterministic" }, "/compress")).toBe(false);
    expect(needsOriginForDeterministic({ text: "<div>HTML</div>", mode: "deterministic" }, "/compress")).toBe(true);
    expect(needsOriginForDeterministic({ text: "plain", include_sections: true, mode: "deterministic" }, "/compress")).toBe(true);
    expect(needsOriginForDeterministic({
      input: `{
  "users": [
    {"id": 1, "name": "Alice", "role": "admin"},
    {"id": 2, "name": "Bob", "role": "user"}
  ]
}`
    }, "/v1/compress")).toBe(false);
    expect(needsOriginForDeterministic({ input: "{\"schema\":{\"type\":\"object\"}}" }, "/v1/compress")).toBe(true);
    expect(needsOriginForDeterministic({
      input: "plain text",
      tenant_profile: { force_drop_phrases: ["Reusable preamble. "] }
    }, "/v1/compress")).toBe(true);
    expect(needsOriginForDeterministic({
      messages: [
        { role: "system", content: "<div>ignored</div>" },
        { role: "user", content: "plain user text" }
      ]
    }, "/v1/messages/compress")).toBe(false);
    expect(needsOriginForDeterministic({
      messages: [
        { role: "user", content: [{ type: "text", text: "```json\n{}\n```" }] }
      ]
    }, "/v1/messages/compress")).toBe(true);
  });

  it("skips model_auto origin calls for low-value edge candidates", () => {
    const decision = evaluateEdgeOriginGate({
      input: "Short prompt that is not worth a Cloud Run model call.",
      compression_settings: { mode: "model_auto" }
    }, "/v1/compress", null);

    expect(decision.useOrigin).toBe(false);
    expect(decision.reason).toBe("edge_skipped_no_candidate_prose");
  });

  it("keeps model_force available for candidate-sized prose", () => {
    const decision = evaluateEdgeOriginGate({
      text: LONG_MODEL_TEXT,
      mode: "model_force"
    }, "/compress", null);

    expect(decision.useOrigin).toBe(true);
    expect(decision.reason).toBeNull();
  });

  it("hard-skips model origin for exact-output requests", () => {
    const decision = evaluateEdgeOriginGate({
      text: `Preserve whitespace exactly.\n\n${LONG_MODEL_TEXT}`,
      mode: "model_force"
    }, "/compress", null);

    expect(decision.useOrigin).toBe(false);
    expect(decision.reason).toBe("edge_skipped_exact_output_context");
  });

  it("skips message origin calls when there is no user text candidate", () => {
    const decision = evaluateEdgeOriginGate({
      messages: [
        { role: "system", content: LONG_MODEL_TEXT },
        { role: "assistant", content: LONG_MODEL_TEXT }
      ],
      compression_settings: { mode: "model_auto" }
    }, "/v1/messages/compress", null);

    expect(decision.useOrigin).toBe(false);
    expect(decision.reason).toBe("edge_skipped_no_candidate_prose");
  });

  it("returns /compress compatible deterministic shape", () => {
    const tenant = resolveTenantProfile({ tenant_id: "tenant_1" }, null);
    const response = buildCompressResponse({ text: "Prompt   text\n\n\nnext", mode: "deterministic" }, tenant, 3);

    expect(response.compressed_text).toBe("Prompt   text\n\nnext");
    expect(response.tenant_id).toBe("tenant_1");
    expect(response.compression_mode).toBe("deterministic");
    expect(response.warnings).toContain("edge_deterministic_response");
  });

  it("returns /v1/compress compatible deterministic shape", () => {
    const tenant = resolveTenantProfile({}, "tenant_header");
    const response = buildV1CompressResponse({ input: "hello\n\n\nworld" }, tenant, 2);

    expect(response.output).toBe("hello\n\nworld");
    expect(response.tenant_id).toBe("tenant_header");
    expect(response.tokens_saved).toBeGreaterThanOrEqual(0);
  });

  it("returns edge-transformed JSON for supported record arrays", () => {
    const tenant = resolveTenantProfile({}, null);
    const response = buildV1CompressResponse({
      input: `Please review:
{
  "users": [
    {"id": 1, "name": "Alice", "role": "admin"},
    {"id": 2, "name": "Bob", "role": "user"}
  ]
}`
    }, tenant, 2);

    expect(response.output).toContain("users[2]{id,name,role}:");
    expect(response.output).toContain("  1,Alice,admin");
    expect(response.output).not.toContain('"users"');
  });

  it("compresses only user message text", () => {
    const tenant = resolveTenantProfile({}, null);
    const response = buildMessagesResponse({
      system: "Keep system",
      messages: [
        { role: "system", content: "System stays" },
        { role: "user", content: "  User text\n\n\nnext  " },
        { role: "assistant", content: "Assistant stays" }
      ]
    }, tenant, 1);

    expect(response.messages).toEqual([
      { role: "system", content: "System stays" },
      { role: "user", content: "User text\n\nnext" },
      { role: "assistant", content: "Assistant stays" }
    ]);
    expect(response.compressed_request).not.toHaveProperty("compression_settings");
    expect(response.message_stats).toHaveLength(3);
  });

  it("compresses user message text parts and preserves non-text parts", () => {
    const tenant = resolveTenantProfile({}, null);
    const response = buildMessagesResponse({
      messages: [
        {
          role: "user",
          content: [
            { type: "text", text: " First part  \n\n\nnext " },
            { type: "image_url", image_url: { url: "https://example.test/image.png" } },
            { type: "text", text: "Second part   " }
          ]
        },
        { role: "tool", content: "Tool result stays   " }
      ]
    }, tenant, 1);

    expect(response.messages).toEqual([
      {
        role: "user",
        content: [
          { type: "text", text: "First part\n\nnext" },
          { type: "image_url", image_url: { url: "https://example.test/image.png" } },
          { type: "text", text: "Second part" }
        ]
      },
      { role: "tool", content: "Tool result stays   " }
    ]);
    expect(response.message_stats).toHaveLength(2);
  });
});
