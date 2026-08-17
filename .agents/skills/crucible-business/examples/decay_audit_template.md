# Legacy Schema Decay Audit
**Client:** [Redacted SI / Public Sector Agency]
**System:** Legacy Property Management Database (NYC Airbnb Extract)
**Date:** August 2026

## Executive Summary
This document outlines the findings of the Legacy Schema Decay Audit performed on a representative synthetic DDL and data sample exported from your legacy Property Management Database. 

The purpose of this audit is to proactively identify undocumented schema decay, type coercions, and orphaned dependencies that frequently cause cascading failures and budget overruns during live migration cut-overs.

---

## 1. Topological Dependency Graph
Using our proprietary Deterministic Migration Engine, we parsed the provided schema to extract foreign key constraints and establish a safe, lock-free migration load order.

**Discovered Load Sequence (Safest Path):**
1. `nyc_airbnb_2019` (Root entity, no inbound foreign keys detected in isolated sample)

*Note: In the full production schema, our engine will output a comprehensive DAG (Directed Acyclic Graph) highlighting circular dependencies requiring manual resolution.*

---

## 2. Schema Decay Analysis & Profiling
We ingested 48,895 synthetic records and subjected them to deep profiling. The following data types were inferred from actual usage patterns, revealing significant deviation from the declared legacy schema definitions.

### Table: `nyc_airbnb_2019`
*Total Rows Analyzed: 48,895*

| Column Name | Inferred Strict Type | Nullability | Decay Risk |
| :--- | :--- | :--- | :--- |
| `id` | `int` | Non-Nullable | **Low** |
| `name` | `str` | Nullable (16 nulls detected) | **Medium** - Missing identifiers |
| `host_id` | `int` | Non-Nullable | **Low** |
| `neighbourhood_group` | `str` | Non-Nullable | **Low** |
| `price` | `int` | Non-Nullable | **High** - Legacy financial data stored as integer, potential precision loss on modern systems |
| `last_review` | `date` | Nullable (10,052 nulls detected) | **High** - Massive null injection implies disabled application logic |
| `reviews_per_month` | `float` | Nullable (10,052 nulls detected) | **Medium** |

---

## 3. Recommended Remediation & Code Generation Strategy

The legacy decay identified above presents a severe risk to the target system's strict typing requirements.

### The "Code-as-a-Deliverable" Solution
If you authorize Phase 2 of this project, we will deploy our Deterministic Engine to generate standard, cleanly formatted Python transformation modules. 

**Deliverables for Phase 2:**
- **Strict Pydantic Contracts:** We will generate `Pydantic` models that enforce strict validation (e.g., forbidding extra fields, strictly casting strings to dates, and safely handling the massive influx of nulls on `last_review`).
- **AST-Sandboxed Execution:** We deliver standard Python code that your engineers can execute safely behind your own firewalls. We never require access to your live production environment or PII.
- **Quarantine Logging:** Any legacy row that violates the deterministic cleaning rules will be safely caught and logged in a Postgres Quarantine Database for human review, ensuring the overall batch migration never crashes.

### Estimated Migration Retainer: 
Based on the complexity of the decay discovered in this sample, full migration code generation is quoted at a fixed price of **£25,000**.
*(Includes delivery of all Python transformation modules and 2 rounds of UAT mapping adjustments).*

---
**CONFIDENTIALITY NOTICE:**
*This audit was performed strictly on synthetic/anonymized schema exports. All temporary data processing was executed in isolated, non-networked sandboxes and has been securely destroyed upon delivery of this report.*
