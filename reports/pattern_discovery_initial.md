# Initial Compression Pattern Discovery

## Scope

This pass analyzes three `model_force`, aggressiveness 0.25 benchmark cohorts.
It prioritizes general deterministic opportunities before tenant-specific ones.
The generated aggregate data is in `pattern_discovery_initial.json`.

The analysis does not reproduce production prompt text. Model deletion counts
are computed only for records where deterministic preprocessing saved zero
tokens, so stage attribution is unambiguous. Counts use token-multiset deltas;
they identify audit candidates, not proven semantic loss.

## Corpus

| Tenant | Successful records | Pure-model records | Input tokens | Deterministic saved | Model saved |
|---|---:|---:|---:|---:|---:|
| Alvarez Search | 99 | 31 | 2,936,085 | 24,270 | 271,676 |
| Kanban Zone | 48 | 7 | 263,948 | 3,183 | 20,656 |
| Delivery Tower | 164 | 164 | 127,154 | 0 | 18,788 |

## General deterministic candidates

### Priority 1: whitespace canonicalization

All tenants contain mechanically removable whitespace. Across the successful
records the source contains 1,958 lines with trailing whitespace, 18,756 runs
of multiple spaces, and 31,377 blank lines. The safe transform is not “remove
all whitespace”; it is syntax-aware canonicalization outside code, tables,
YAML, preformatted HTML, and protected spans.

This should be investigated first because it is deterministic, inexpensive,
and testable with strong invariants. Existing preprocessing already reports a
whitespace category, so the discovery is likely an opportunity to widen
coverage or correct segmentation gaps rather than introduce a new concept.

### Priority 2: recognized comments and non-semantic wrappers

Kanban Zone contains 6 HTML comments and Delivery Tower contains 25. Comments
and known presentation-only wrappers can be dropped when the parser proves
their type and tenant policy does not treat them as instructions. Unknown
comments must remain protected.

### Priority 3: structured-data compaction

The corpus contains 4,413 JSON-like lines and 1,828 HTML tags. General rules
should operate only after successful parsing:

- minify valid JSON while preserving values and array order;
- compact repetitive JSON Schema/tool metadata using a reversible encoding;
- convert an allowlisted subset of presentation-only HTML to compact text;
- preserve code, template expressions, attributes with behavior, and invalid
  or ambiguous markup unchanged.

The current compressor already has JSON-minify, TOON, and HTML/Markdown
categories. The next analysis should measure why substantial structured input
still reaches the model and which parser/gating conditions prevent conversion.

### Priority 4: exact duplicate blocks

Within-record scanning found 3,430 repeated normalized line occurrences,
representing approximately 242,000 repeated characters. This is an upper
bound, not immediately safe savings: repeated schema fields, examples, and
policy statements may differ in context or be intentionally repeated.

A safe general transform should require an exact contiguous block match,
preserve the first occurrence, reject blocks inside examples or ordered data,
and emit diagnostics identifying every removed duplicate. Hash equality alone
is insufficient without structural boundaries.

### Priority 5: conservative markup normalization

There are 6,221 Markdown headings and 41,669 bullet lines. Punctuation and
spacing around well-formed headings and lists can sometimes be canonicalized,
but the content and hierarchy must remain intact. Expected savings are modest;
this ranks below parsed structured-data work.

## Model deletion patterns that should not become blind rules

Across all three tenants, the most frequent model-stage token losses are
articles, conjunctions, auxiliaries, pronouns, and discourse words. Removing
these mechanically could produce telegraphic text, change attachment, or alter
the subject of an instruction. These patterns are useful for proposing narrow
templates, not a global stop-word deletion pass.

The pure-model subset also shows safety-sensitive token deltas:

| Tenant | Negation | Obligation | Scope | Permission | Destructive action |
|---|---:|---:|---:|---:|---:|
| Alvarez Search | 871 | 213 | 1,491 | 1,752 | 22 |
| Kanban Zone | 140 | 105 | 189 | 63 | 0 |
| Delivery Tower | 8 | 1 | 141 | 159 | 1 |

These counts are inflated by repeated prompt templates and may include
rephrasing rather than true semantic deletion. Nevertheless, they establish
the first protection audit categories: negation, obligation, scope,
permission, and destructive actions. Contextual alignment is required before
labeling any instance harmful.

## Tenant-specific leads

### Alvarez Search

- Largest source of general structural opportunities: roughly 218,000
  characters in repeated normalized lines, 15,895 multiple-space runs, and
  1,452 trailing-whitespace lines.
- Very long prompts (median 18,009 input tokens; p95 about 117,855) make block
  deduplication and structured-schema compaction more promising than lexical
  rewriting.
- The 14 failed fetches should be rerun before final tenant estimates.

### Kanban Zone

- Bimodal corpus: many tiny user turns and a small number of large system
  prompts. Tenant rules should target the system-prompt structure and continue
  leaving short user messages unchanged.
- HTML, comments, JSON-like content, and repeated lines make parsed structural
  transforms the strongest lead.

### Delivery Tower

- All 164 records are clean model-only observations because deterministic
  preprocessing saved zero tokens.
- Inputs are much smaller (median 768; p95 about 1,488 tokens) and contain less
  whitespace redundancy. General whitespace work will yield less here.
- Repeated domain phrases and stable output instructions are candidates for a
  tenant dictionary or tenant-authored compact template, but should not become
  global deletion rules.

## Next analysis

1. Instrument and replay the deterministic stage to explain missed whitespace,
   JSON, HTML, TOON, and duplicate-block candidates by gate reason.
2. Perform contextual token alignment for the five safety categories and label
   each occurrence as preserved meaning, ambiguous, or harmful.
3. Implement general candidates behind invariants and measure incremental
   savings on all three tenants.
4. Only after general transforms plateau, mine tenant templates and dictionaries
   from residual model-stage removals.

