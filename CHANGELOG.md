# Changelog

All notable changes to the EduVijna AIEOS Backend repository are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this repository follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- GCI-I01 Generic Content pure-domain contracts under `src/aieos/domains/content/domain/` (identity, stewardship, origin, aggregate, version, schema registry, review decision, publication, domain errors) with focused unit tests. No persistence, HTTP, NATS, Temporal, or AI-provider behavior.
- GCI-I01R1: tenant/principal/correlation fields are stdlib UUID values (not Content-owned identity types); Python pin `>=3.14,<3.15`.
- GCI-I01R2: deeply immutable JSON ContentPayload; version_number==1 iff no parent; ARCHIVED withdraws active published_version_id.
- GCI-I02 Generic Content PostgreSQL schema `content.contents` / `content.content_versions` via Alembic, SQLAlchemy 2.0 mappings, FORCE RLS, transaction-local `aieos.tenant_id`, and ContentVersion immutability triggers. No repositories, HTTP, or GCI-I03+ persistence behavior.

### Changed

- Nothing yet.

### Deprecated

- Nothing yet.

### Removed

- Nothing yet.

### Fixed

- Nothing yet.

### Security

- Nothing yet.

## [0.1.0] - 2026-08-13

### Added

- Repository foundation: README, CONTRIBUTING, CODEOWNERS, LICENSE (Apache-2.0), VERSION, changelog, and GitHub issue/PR templates aligned with `eduvijna-architecture` and `eduvijna-product`.
