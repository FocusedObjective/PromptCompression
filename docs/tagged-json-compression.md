# Tagged JSON Compression

Tagged JSON compression lets a tenant selectively compress approved long text
values inside JSON without exposing the JSON structure to probabilistic model
compression. Keys, types, array positions, and unapproved values remain under
deterministic control.

Use this feature when a prompt contains structured JSON with large narrative
fields such as descriptions, comments, notes, or summaries. Do not use it for
JSON that must remain byte-identical.

## Safety invariant

> A tag can identify a policy, but only the tenant profile can authorize which
> JSON string paths may be compressed.

An untrusted prompt cannot add paths or increase its own compression limits.
The server compares the tag's policy identifier with the request-scoped tenant
profile and uses only the paths and limits in that profile.

## XML-style tag schema

Wrap one JSON object or array in a `compress-json` tag:

```xml
<compress-json policy="issue-v1">
{
  "id": "ISSUE-73",
  "title": "Customer quota threshold crossed notification",
  "description": "A long narrative description...",
  "comments": [
    {
      "author": "Ada",
      "body": "A long narrative comment..."
    }
  ]
}
</compress-json>
```

The supported opening-tag schema is:

```text
<compress-json policy="POLICY_ID">JSON_OBJECT_OR_ARRAY</compress-json>
```

Tag requirements:

- `policy` is required for selective value compression.
- The value must be quoted with single or double quotes.
- It may contain ASCII letters, numbers, `_`, `-`, `.`, and `:` but no spaces.
- Its length must be between 1 and 128 characters.
- The body must contain exactly one valid JSON object or array followed only by
  whitespace and the closing tag.
- Multiple tagged blocks may appear in one prompt.
- Nested `compress-json` blocks are not supported.
- Text resembling `</compress-json>` inside a valid JSON string is handled as
  string content and does not close the block early.

The tags are control markup and are removed from the final compressed prompt.

## Tenant-profile schema

These settings live inside `tenant_profile` on `/compress`, `/v1/compress`, and
`/v1/messages/compress` requests.

| Field | Type | Default | Constraints | Meaning |
| --- | --- | --- | --- | --- |
| `json_compression_policy_id` | string or null | `null` | 1-128 characters; `[A-Za-z0-9_.:-]+` | Policy identifier that a tag must reference. |
| `json_value_compression_paths` | array of strings | `[]` | Safe JSONPath subset | String leaves eligible for compression. |
| `json_value_min_tokens` | integer | `200` | 1-1,000,000 | Minimum estimated tokens in an allowlisted string. |
| `json_value_max_reduction` | number | `0.25` | 0.0-1.0 | Maximum accepted token reduction for each string. |
| `json_value_max_values` | integer | `8` | 1-100 | Maximum accepted compressed values across all tagged blocks in one request. |

Example:

```json
{
  "profile_id": "tenant_123:issue-v1",
  "default_aggressiveness": 0.2,
  "min_rate": 0.6,
  "json_compression_policy_id": "issue-v1",
  "json_value_compression_paths": [
    "$.description",
    "$.comments[*].body"
  ],
  "json_value_min_tokens": 200,
  "json_value_max_reduction": 0.25,
  "json_value_max_values": 8
}
```

## Supported path syntax

Paths use a deliberately small JSONPath subset.

| Pattern | Supported | Meaning |
| --- | --- | --- |
| `$.description` | Yes | Root object's `description` string. |
| `$.metadata.summary` | Yes | Nested `summary` string. |
| `$.comments[*].body` | Yes | `body` string in every item of the `comments` array. |
| `$.comments[0].body` | No | Explicit array indexes are intentionally unsupported. |
| `$..description` | No | Recursive descent is unsupported. |
| `$.items[?(@.type=='note')]` | No | Filters and expressions are unsupported. |
| `$['key with spaces']` | No | Quoted or special-character property access is unsupported. |

Property names in configured paths must start with a letter or underscore and
may then contain letters, numbers, underscores, or hyphens. Array traversal is
available only through `[*]`.

Paths must resolve to JSON strings. Objects, arrays, numbers, Booleans, and
null values are never sent for partial model compression.

## Complete API example

```json
{
  "tenant_id": "tenant_123",
  "tenant_profile": {
    "profile_id": "tenant_123:issue-v1",
    "json_compression_policy_id": "issue-v1",
    "json_value_compression_paths": [
      "$.description",
      "$.comments[*].body"
    ],
    "json_value_min_tokens": 200,
    "json_value_max_reduction": 0.25,
    "json_value_max_values": 8
  },
  "text": "Review this issue:\n<compress-json policy=\"issue-v1\">{\"id\":\"ISSUE-73\",\"description\":\"Long narrative...\",\"comments\":[{\"author\":\"Ada\",\"body\":\"Long comment...\"}]}</compress-json>",
  "aggressiveness": 0.25,
  "mode": "model_auto"
}
```

For `/v1/compress`, place `mode` and `aggressiveness` inside
`compression_settings`; the tagged-JSON fields remain inside `tenant_profile`.

## Processing sequence

For each tagged block, the service:

1. Locates the opening tag and parses exactly one JSON value with a JSON-aware
   boundary parser.
2. Requires an object or array root and checks for duplicate keys.
3. Verifies that the tag policy matches the tenant policy.
4. Walks the JSON tree in stable source order.
5. Selects only allowlisted string paths, up to `json_value_max_values`.
6. Skips strings smaller than `json_value_min_tokens`.
7. Compresses each eligible string independently using the request's mode and
   aggressiveness.
8. Rejects empty results, non-saving results, excessive reductions, and results
   that lose protected spans.
9. Restores rejected strings from their original values.
10. Rebuilds compact JSON with correct escaping.
11. Converts the complete object to TOON when safe and beneficial; otherwise it
    retains protected JSON.
12. Replaces the entire TOON or JSON segment with a placeholder before any outer
    prompt model-compression call, then restores it afterward.

The model never edits JSON keys, punctuation, types, or object relationships.

## Model modes

### `deterministic`

Allowlisted strings receive only deterministic processing. The rebuilt JSON may
still be compacted or converted to TOON. No LLMLingua call is made.

### `model_auto`

Each allowlisted value independently passes the normal auto-mode gates. Values
that fail those gates remain unchanged. The outer prompt then passes its own
model-auto gate after the tagged JSON has become a protected segment.

### `model_force`

Each eligible value is submitted for bounded model compression. The surrounding
prompt may require one additional model call. A request can therefore make up
to `json_value_max_values + 1` model calls, subject to segment eligibility and
fallback behavior.

Use a low `json_value_max_values` setting when latency is important.

## Acceptance and fallback rules

An independently compressed string is accepted only when:

- The output is non-empty.
- Its estimated token count is lower than the original value.
- Its reduction does not exceed `json_value_max_reduction`.
- Every protected URL, email, number, money value, inline-code span, identifier,
  constant, and supported constraint span still appears with the required
  occurrence count.

Failure affects only that value; its original string is restored. Other values
may still be accepted.

## Tag and policy failure behavior

| Condition | Behavior | Warning |
| --- | --- | --- |
| Policy matches | Apply allowlisted value rules. | None solely for matching. |
| Policy absent or unauthorized | Do not partially compress values; remove the tag and pass valid JSON to normal TOON/protection. | `json_tag_policy_not_authorized:<id>` when an ID is present. |
| Invalid configured path | Ignore that path. | `json_value_path_invalid:<path>` |
| Invalid tagged JSON | Protect its body verbatim. | `json_tag_invalid_json_protected` |
| Duplicate JSON keys | Protect its body verbatim to avoid key collapse. | `json_tag_duplicate_keys_protected` |
| Scalar JSON root | Protect it; selective compression requires an object or array. | `json_tag_root_must_be_object_or_array_protected` |
| Missing or misplaced closing tag | Protect the parsed/body content rather than exposing it. | `json_tag_unclosed_protected` or `json_tag_missing_close_after_json_protected` |

Warnings appear in the normal response `warnings` array.

## Output expectations

The final structured block may be TOON when TOON meets the normal safety and
savings gates, or compact/protected JSON when TOON is not appropriate. The
original `compress-json` wrapper is never included in the result.

Do not use this feature when a downstream consumer requires byte-identical JSON
or when the prompt presents an exact fixture, schema, template, or tool
exchange. Use `<nocompress>...</nocompress>` for explicitly verbatim text.

## Recommended policy design

Prefer narrow semantic allowlists such as:

```json
{
  "json_value_compression_paths": [
    "$.description",
    "$.notes",
    "$.comments[*].body"
  ]
}
```

Avoid paths containing IDs, keys, hashes, enums, URLs, file paths, timestamps,
model names, code, SQL, stack traces, templates, prompts, tool exchanges,
base64, encrypted data, or text whose exact wording is contractual.

Start with `model_auto`, a `json_value_min_tokens` of at least 200, a maximum
reduction around 0.20-0.25, and no more than 4-8 values per request. Validate
quality on tenant-specific examples before using `model_force`.

## Operational notes

- Selective string compression can increase latency because values are handled
  independently.
- Tags without a matching tenant policy do not grant extra authority.
- Ordinary untagged valid JSON still follows the global rule: TOON when safe and
  beneficial, otherwise protect it verbatim from model compression.
- Structured Markdown such as `fieldName: value` is not JSON and is outside the
  scope of this feature.
