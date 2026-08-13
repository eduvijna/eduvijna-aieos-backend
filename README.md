# EduVijna AIEOS Backend

Application backend for EduVijna **AIEOS** (Artificial Intelligence Engineering Education Operating System).

## Mission

Provide stable application APIs, domain services, persistence contracts, and AI-platform capabilities **behind** product service façades — so Teacher OS and other AIEOS surfaces never call agents, MCP tools, or LLM providers directly.

## Purpose

This repository is the **AIEOS backend implementation** workspace.

Architecture and product intelligence do **not** live here:

| Concern | Canonical home |
|---------|----------------|
| Enterprise architecture, ADRs, reviews | [eduvijna-architecture](https://github.com/eduvijna/eduvijna-architecture) |
| Product vision, Teacher OS product architecture, EBPs, EDRs | [eduvijna-product](https://github.com/eduvijna/eduvijna-product) |
| AIEOS frontend / Teacher OS shell | [eduvijna-aieos-frontend](https://github.com/eduvijna/eduvijna-aieos-frontend) |
| AIEOS backend / APIs / domain services | **this repository** |

## Repository Scope

In scope:

- Application APIs and domain services
- Persistence contracts and data access for AIEOS backend capabilities
- Feature flags, authorization, and tenant isolation
- AI platform concerns (orchestrator, agents, MCP, LLM providers) **behind** stable product services (ADR-044)
- Tests, CI, and operational artefacts for this backend

Out of scope:

- Product vision, personas, journeys, and Teacher OS product architecture (Product Office)
- ADRs, EAO governance, and enterprise discovery (Architecture Office)
- Frontend / UI implementation (AIEOS frontend)
- Unauthorised new databases, agents, MCP, or orchestration outside approved ADRs and the active EBP

## Repository Structure

| Path | Role |
|------|------|
| `README.md` | Mission, scope, and contribution entry |
| `CONTRIBUTING.md` | Contribution rules (aligned with architecture and product repos) |
| `CODEOWNERS` | Ownership map |
| `.github/` | Issue and pull-request templates |
| `src/` | Backend implementation (to be added under approved blueprints) |
| `contracts/openapi/` | Checked-in OpenAPI 3.1 snapshots |
| `docs/` | Repository-local implementation notes (not ADRs) |

HTTP Content create (`POST /api/v1/contents`) is a **non-production foundation**. See `docs/GCI-I04-NON-PRODUCTION-MUTATION-BOUNDARY.md`.

## Engineering Lifecycle

1. **Discover** — Confirm the change is authorised by an ADR, EBP, or Product Architecture Review.
2. **Decide** — Escalate architectural choices to `eduvijna-architecture`; implementation-only choices to EDRs in `eduvijna-product`.
3. **Implement** — Deliver a vertical slice behind feature flags; reuse existing capabilities before creating new ones.
4. **Review** — Pull request required; Architecture Review for material contract or boundary changes.
5. **Validate** — No merge without automated tests appropriate to the slice.
6. **Govern** — Preserve tenant isolation, JWT/authorization patterns, and rollback-by-flag.

## Contribution Workflow

1. Confirm the change belongs in this backend repository (not architecture or product).
2. Open an issue using the appropriate template when the change is material.
3. Branch from `main` and keep the change focused.
4. Submit a pull request; merge requires review.
5. Architecture Review is required for material API, persistence, or AI-platform boundary changes.
6. Follow `CONTRIBUTING.md`.

## Ownership

EduVijna Engineering, under Architecture Office and Product Office stewardship.

GitHub: [github.com/eduvijna/eduvijna-aieos-backend](https://github.com/eduvijna/eduvijna-aieos-backend)

## License

Copyright 2026 EduVijna

Licensed under the [Apache License, Version 2.0](LICENSE).
