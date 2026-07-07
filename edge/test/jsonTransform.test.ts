import { describe, expect, it } from "vitest";
import { requiresOriginForJsonText, transformJsonSegmentsForEdge } from "../src/jsonTransform";

describe("edge JSON transform", () => {
  it("converts embedded homogeneous record arrays to TOON-like text", () => {
    const input = `Please review:
{
  "users": [
    {"id": 1, "name": "Alice", "role": "admin"},
    {"id": 2, "name": "Bob", "role": "user"},
    {"id": 3, "name": "Cora", "role": "user"}
  ]
}
Please review after.`;

    const result = transformJsonSegmentsForEdge(input);

    expect(result.transformedCount).toBe(1);
    expect(result.text).toContain("users[3]{id,name,role}:");
    expect(result.text).toContain("  1,Alice,admin");
    expect(result.text).not.toContain('"users"');
    expect(requiresOriginForJsonText(input)).toBe(false);
  });

  it("converts labeled JSON candidates without parsing the whole prompt", () => {
    const input = `Payload: {
  "users": [
    {"id": 1, "name": "Alice", "role": "admin"},
    {"id": 2, "name": "Bob", "role": "user"}
  ]
}`;

    expect(transformJsonSegmentsForEdge(input).text).toContain("Payload: users[2]{id,name,role}:");
    expect(requiresOriginForJsonText(input)).toBe(false);
  });

  it("keeps small generic JSON on the edge-safe path without transforming it", () => {
    const input = 'Please review {"ok": true} after.';
    const result = transformJsonSegmentsForEdge(input);

    expect(result.transformedCount).toBe(0);
    expect(result.text).toBe(input);
    expect(requiresOriginForJsonText(input)).toBe(false);
  });

  it("delegates exact JSON template contexts", () => {
    const input = `Return exactly this JSON shape:
{
  "items": [
    {"id": "A1", "label": "Alpha"},
    {"id": "B2", "label": "Beta"}
  ]
}`;

    expect(transformJsonSegmentsForEdge(input).transformedCount).toBe(0);
    expect(requiresOriginForJsonText(input)).toBe(true);
  });

  it("delegates duplicate-key JSON instead of accepting JSON.parse overwrite semantics", () => {
    const input = `Please review:
{
  "feature": "old",
  "feature": "new",
  "items": [
    {"id": 1},
    {"id": 2}
  ]
}`;

    expect(transformJsonSegmentsForEdge(input).transformedCount).toBe(0);
    expect(requiresOriginForJsonText(input)).toBe(true);
  });

  it("delegates schemas and tool exchanges", () => {
    expect(requiresOriginForJsonText('{"schema":{"type":"object"}}')).toBe(true);
    expect(requiresOriginForJsonText(`[
  {
    "role": "assistant",
    "tool_calls": [
      {"id": "call_123", "type": "function"}
    ]
  }
]`)).toBe(true);
  });

  it("delegates unsupported nested JSON shapes", () => {
    const input = `Please review:
{
  "account": {"id": "acct_1", "plan": "enterprise"},
  "region": "us-west-2"
}`;

    expect(transformJsonSegmentsForEdge(input).transformedCount).toBe(0);
    expect(requiresOriginForJsonText(input)).toBe(true);
  });
});
