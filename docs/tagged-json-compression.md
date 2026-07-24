# Tagged JSON Compression

Tagged JSON compression selectively compresses approved string values without
exposing JSON keys, punctuation, types, arrays, or unselected values to
LLMLingua.

## Profiler inline format

The `/compress` profiler/debug endpoint accepts an inline path list when the
request explicitly sets `allow_inline_json_compression_paths` to `true`:

```xml
<compress-json paths="$.description,$.comments[*].body">
{
  "id": "ISSUE-73",
  "title": "Customer quota threshold crossed notification",
  "description": "A long narrative description...",
  "comments": [{"author": "Ada", "body": "A long narrative comment..."}]
}
</compress-json>
```

```json
{
  "text": "<compress-json paths=\"$.description,$.comments[*].body\">{...}</compress-json>",
  "allow_inline_json_compression_paths": true,
  "aggressiveness": 0.25,
  "mode": "model_auto"
}
```

Inline paths are disabled by default and are not exposed by `/v1/compress` or
`/v1/messages/compress`. Without the explicit opt-in, the JSON is protected and
the response includes `json_tag_inline_paths_not_authorized`.

Compression limits remain server-owned. The request's tenant profile supplies
`json_value_min_tokens`, `json_value_max_reduction`, and
`json_value_max_values`; their defaults are 200, 0.25, and 8.

## Tenant-policy format

Production callers should authorize paths in `tenant_profile`:

```json
{
  "tenant_profile": {
    "json_compression_policy_id": "issue-v1",
    "json_value_compression_paths": [
      "$.description",
      "$.comments[*].body"
    ],
    "json_value_min_tokens": 200,
    "json_value_max_reduction": 0.25,
    "json_value_max_values": 8
  },
  "text": "<compress-json policy=\"issue-v1\">{...}</compress-json>"
}
```

A policy tag may request a narrower authorized subset:

```xml
<compress-json
  policy="issue-v1"
  paths="$.description,$.comments[*].body">
```

The effective selection is the intersection of inline requested paths and
tenant-authorized paths. A policy mismatch is always rejected, even when the
profiler inline opt-in is enabled.

## Supported paths

The supported JSONPath subset is deliberately small:

- `$.description`
- `$.metadata.summary`
- `$.comments[*].body`

Explicit array indexes, recursive descent, filters, quoted property access, and
special-character keys are unsupported. Selected values must be strings.

## Safety and fallback

Each value is compressed independently. A result is accepted only when it is
non-empty, saves tokens, stays within `json_value_max_reduction`, and preserves
protected values such as identifiers, URLs, numbers, and money amounts.

Invalid JSON, duplicate keys, invalid attributes, unsupported paths, policy
mismatches, short values, and failed acceptance checks remain unchanged. The
rebuilt JSON is protected from the surrounding LLMLingua call and may be
converted to TOON when the normal safety and savings gates allow it. The
`compress-json` wrapper is removed from the final output.

Use `<nocompress>...</nocompress>` instead when JSON must remain byte-identical.
