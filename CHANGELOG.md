# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- Console exporter colorized output option
- Retry logic now exposes attempt count in callbacks

### Fixed
- JSON file exporter handles unicode characters correctly

## [1.1.0] - 2024-09-15

### Added
- JSON file exporter for writing traces to local files
- Console exporter for development and debugging
- Batch processing utilities for high-throughput workloads
- OAuth 2.0 authentication support
- Configuration file support (YAML and TOML)
- Exponential backoff retry logic with jitter

### Changed
- OTLP exporter now supports gzip compression by default
- Client constructor accepts optional `timeout` parameter
- Improved error messages for authentication failures

### Fixed
- OTLP exporter no longer drops spans when batch size exceeds 1000
- Race condition in batch exporter flush on interpreter shutdown
- Config parser handles environment variable interpolation correctly

## [1.0.0] - 2024-07-01

### Added
- Initial release of the Acme SDK for Python
- `AcmeClient` with HTTP transport
- Core data models: `Trace`, `Span`, `Event`, `Metric`
- OTLP HTTP exporter
- API key authentication
- Basic retry logic
- Comprehensive test suite
- Documentation and examples

[Unreleased]: https://github.com/OWNER/acme-agent-evals/compare/v1.1.0...HEAD
[1.1.0]: https://github.com/OWNER/acme-agent-evals/compare/v1.0.0...v1.1.0
[1.0.0]: https://github.com/OWNER/acme-agent-evals/releases/tag/v1.0.0
