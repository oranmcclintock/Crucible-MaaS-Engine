# Crucible MaaS Engine: High-Level Refresher

This document is your "Read Tomorrow" guide to quickly familiarize yourself with the core mechanics of your Deterministic Legacy Migration Engine before you start pitching it to SIs. 

## The Core Philosophy
The Crucible Engine is **not** an AI that magically fixes databases at runtime. It is a strict, deterministic, three-phase compiler. It acts as an air-gapped "Ghost Subcontractor." You never touch the client's live PII; you simply take their DDL (schema) and an anonymized sample, run the engine, and hand them mathematically proven Python code to execute on their own servers.

---

## What Actually Does Phase 1 (The Profiler) Do?

Phase 1 (`profiler.py`) is the reconnaissance mission. It reads the legacy database and outputs a `profile.json` blueprint. It has two main jobs:

### 1. The Topological Sorter (The "Dependency Graph")
Legacy databases are massive webs of foreign keys. If an SI tries to migrate the `Orders` table before migrating the `Customers` table, the database will crash with a Foreign Key Violation.
- **What it does:** Phase 1 uses Graph Theory (Directed Acyclic Graphs - DAGs) to map every relationship in the database.
- **The Output:** It calculates the absolute, mathematically safe load order. It guarantees that independent tables are loaded first, and dependent tables are loaded exactly when their parent records exist.

### 2. The Decay Scanner (The "Rot Profiler")
Legacy databases lie. A column labeled "Integer" might be 99% integers and 1% strings that say "N/A" or "PENDING". 
- **What it does:** Phase 1 uses Pandas to scan a sample of the legacy data against a predefined `flaw_catalog.py`. 
- **The Output:** It identifies silent data corruption *before* code is written. It flags type coercions, extreme null injection rates, and structural anomalies. This allows you to say to an SI: *"We found 4 critical vulnerabilities in your schema before we even wrote the migration script."*

---

## The Rest of the Pipeline (Phases 2 & 3)

### Phase 2: Code Generation (`codegen.py`)
- **What it does:** It takes the `profile.json` blueprint from Phase 1 and passes it through strict `Jinja2` templates.
- **The Output:** It generates standard, human-readable Python files (`gen_table.py`). These scripts contain strict **Pydantic** contracts that define exactly what a clean row must look like. *This generated code is your final deliverable to the client.*

### Phase 3: Multiprocessing Execution & Quarantine (`executor.py`)
- **What it does:** When the client runs your generated code on their live production data, it runs across a multiprocessing worker pool utilizing all available CPU cores. 
- **The Safety Mechanism:** When a rotten row hits the Pydantic contract, it throws a `ValidationError`. Instead of crashing the entire batch, the engine catches the error, routes the raw JSON and the exact failure reason into a **Postgres Quarantine Database**, and instantly moves to the next row. Systemic corruption is prevented, and the migration never stalls.
