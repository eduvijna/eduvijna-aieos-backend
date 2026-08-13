# Contributing to EduVijna AIEOS Backend

Contributions to this AIEOS backend repository must preserve architecture-first delivery, traceability, and reviewability.

These rules mirror the contribution rules of [eduvijna-architecture](https://github.com/eduvijna/eduvijna-architecture) and [eduvijna-product](https://github.com/eduvijna/eduvijna-product), adapted for backend implementation (not EAO governance or product-intelligence directories).

## Rules

### Implementation belongs here; governance does not

Add application source, APIs, tests, and backend operational artefacts in this repository.

Do **not** add:

- ADRs or enterprise architecture models — those belong in `eduvijna-architecture`
- Product vision, personas, journeys, Teacher OS product architecture, EBPs, or EDRs — those belong in `eduvijna-product`
- Frontend / UI code — that belongs in `eduvijna-aieos-frontend`

Cursor is an implementation executor. It must not independently redefine approved architecture.

### Pull Request required

All changes land through a pull request to the default branch. Direct commits to `main` are not permitted under normal process.

### Architecture Review required

Material changes to service contracts, persistence, tenancy, authorization, AI-platform boundaries, or feature-flag semantics require **Architecture Review** before approval.

Cite the governing ADR, EBP, or Product Architecture artefact. If a choice would change architecture, open work in `eduvijna-architecture` first — do not encode an unapproved architecture change in a backend PR.

Editorial corrections and tightly scoped bug fixes may follow a lighter review path when they do not alter contracts or boundaries.

### Stable artifact IDs

Assigned identifiers (API routes, contract names, feature flags, artefact type IDs, review-package IDs) are stable. Do not reuse, renumber, or silently repurpose IDs. If a contract is superseded, retain history and mark status appropriately.

### Markdown quality

- Use clear headings and concise prose
- Prefer tables where they improve scanability
- Ensure links resolve within the repository or to canonical architecture/product artefacts
- Prefer document headers with `id`, `status`, `version`, and ownership where established

### Cross references

Link related artefacts by stable ID and path. Prefer repository-relative links. Keep terminology aligned with Teacher OS / AIEOS vocabulary (Teaching Intent, Teacher Memory, School Context, Daily Learning Loop, Review Queue) and `eduvijna-architecture` ADRs.

### Versioning expectations

- Update artefact or API `version` when meaning or contracts change
- Record consumer-visible repository changes in `CHANGELOG.md`
- Align repository version in `VERSION` when releasing
- Do not silently rewrite approved review outcomes

### Backend-specific constraints

- Frontend and other clients consume **stable product services** only; keep orchestrator, agents, MCP, and LLM providers behind those façades (ADR-044).
- Reuse existing JWT / authorization patterns — do not invent parallel auth.
- Enforce school / tenant isolation on Artifact / Work / Queue queries.
- Feature flags must not grant privilege escalation.
- AI outputs require explicit teacher approval before publish (ADR-046 / ADR-048).
- Inspect and reuse existing capabilities before creating duplicates.
- Do not introduce Agents, MCP, Orchestration, Teacher Memory, new databases, or new repositories unless authorised by approved architecture decisions and the active blueprint.
- Prefer additive change; never break existing schools when flags are off.

## Workflow

1. Open an issue for non-trivial work.
2. Create a descriptive branch from `main`.
3. Make focused changes within the correct directory.
4. Update `CHANGELOG.md` under `[Unreleased]` when appropriate.
5. Open a pull request using the PR template.
6. Request review from owners in `CODEOWNERS`.
7. Merge only after required approvals and automated validation appropriate to the slice.

## Contribution types

| Type | Process |
|------|---------|
| API / domain-service behaviour | PR + engineering review |
| Service contract / persistence / tenancy / AI-platform boundary | PR + Architecture Review |
| Feature flags / authorization | PR + Architecture Review when semantics change |
| Tests / CI / docs | PR + engineering review |
| Governance / contributing / templates | PR + EAO-aligned review |
| Editorial | Lightweight PR |

## Review checklist

Reviewers verify:

- Correct repository and directory placement
- No architecture or product-intelligence artefacts leaked into this repo
- Stable IDs and contracts preserved
- ADR-044 / ADR-042 / ADR-046 / ADR-048 constraints respected
- Cross-references and terminology consistency
- Markdown quality and working links
- Tests present for the slice
- Architecture Review completed when required
- `CHANGELOG.md` updated if consumer-visible

## Questions

Use a General or Governance issue template, or contact EduVijna Engineering through leadership channels. Architecture questions belong in [eduvijna-architecture](https://github.com/eduvijna/eduvijna-architecture); product questions belong in [eduvijna-product](https://github.com/eduvijna/eduvijna-product).
