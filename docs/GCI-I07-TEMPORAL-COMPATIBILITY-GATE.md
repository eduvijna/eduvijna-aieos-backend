---
id: GCI-I07-TEMPORAL-COMPATIBILITY-GATE
title: Temporal compatibility smoke gate before production workflow deployment
status: draft
version: 0.1.0
---

# WFI-G13 — Temporal compatibility smoke gate

GCI-I07 validates ContentReviewWorkflowV1 against the official Temporal Python SDK test environment on the developer/CI Python 3.14 runtime.

Before any production workflow deployment, rerun the Temporal compatibility smoke suite (start, signal, query, result, cancellation, history replay) on the **actual target OCI/runtime platform**.

GCI-I07 test-server success is not the production-runtime gate.
