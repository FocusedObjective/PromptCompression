# Changelog

This project records user-visible features, quality improvements, and deployment
milestones over time. Historical entries before July 12, 2026 were reconstructed
from the repository's commit history.

## 2026-07-12

### Added

- Added JSON protection and selective text compression based on tenant schema.
- Added tenant-authorized `<compress-json policy="...">` tagging, safe JSONPath
  allowlists, bounded per-value compression, deterministic reconstruction, and
  TOON-or-verbatim protection for the complete structure.
- Added dedicated tagged JSON compression documentation and an in-app changelog.

### Changed

- Valid JSON objects and arrays are now protected from model compression even
  when they are too small to benefit from TOON conversion.
- JSON size now determines TOON eligibility rather than protection eligibility.

## 2026-07-11

### Changed

- Improved `/compress` diagnostic results for benchmarking and attribution.

## 2026-07-10

### Added

- Added a streamlined iframe embedding demo.

## 2026-07-07

### Changed

- Normalized aggressiveness settings across compression paths.
- Improved the edge compression function.

## 2026-07-06

### Added

- Added the Cloudflare edge compression worker.
- Added an initial GPU Docker and Cloud Run configuration.
- Added deterministic HTML compression.

## 2026-07-05

### Added

- Added GPU benchmark tests and recommendations.

### Changed

- Implemented additional deterministic compression optimizations.

## 2026-06-30

### Changed

- Improved evaluation coverage.
- Added deployment version and timestamp fields to `/health`.

## 2026-06-27

### Added

- Added tenant compression settings and LoRA examples.

### Changed

- Improved token estimation by preferring the model tokenizer, then tiktoken,
  then the regex fallback.

## 2026-06-26

### Documentation

- Added the tenant adaptation plan.

## 2026-06-25

### Added

- Added the evaluation suite and established a passing baseline.

### Fixed

- Fixed Docker deployment of the data directory.

## 2026-06-24

### Changed

- Improved runtime performance.
- Refined the HTML and JSON preprocessing pipelines.

## 2026-06-23

### Added

- Added TOON and deterministic whitespace pipelines.
- Added the initial test-harness application.
- Prepared the Docker deployment path for Google Cloud Run.

### Fixed

- Expanded tests and fixed whitespace handling bugs.

## Initial release

### Added

- Established the PromptCompression project and its initial API/runtime shape.
