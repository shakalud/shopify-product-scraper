# Changelog

All notable changes to this project will be documented in this file.

## [1.0.0] - 2026-06-28

### Added

- Export of publicly available Shopify product and variant data to CSV.
- Full-store and collection-specific public Shopify JSON endpoints.
- Pagination, configurable request delays, timeouts, and retries.
- HTML description cleanup and product URL generation.
- Append mode, row deduplication, progress tracking, and resumable runs.
- Separate error CSV and machine-readable JSON run summary.
- Local JSON configuration with command-line overrides.
- Dockerfile and Docker Compose support.
- Safe Docker build context and first-run example configuration.
- Offline pytest coverage for core helper behavior.
- GitHub Actions test matrix for Python 3.11 and 3.12.
- MIT License and portfolio-oriented documentation.

### Security

- Local configuration, logs, cache, and generated output are excluded from Git.
- Local configuration and generated runtime files are excluded from Docker images.
- The documented scope is limited to publicly available Shopify product data.
