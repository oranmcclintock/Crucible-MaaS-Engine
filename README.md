# Legacy Migration Engine — Deterministic Codegen, No Runtime Agent

This is a working reference implementation of the revised architecture:
**profile once, generate once, review, execute in batch.** There is no
LLM call at runtime, no self-patching loop, and no agent making decisions
mid-migration. The only place an LLM *could* optionally sit is fully
outside the execution path (see "Where an LLM would actually help," below).

## Pipeline

```
legacy_source.db
      │
      ▼
┌─────────────┐   profile.json    ┌─────────────┐   generated/*.py   ┌─────────────┐
│ profiler.py │ ────────────────▶ │ codegen.py  │ ──────────────────▶│  (human      │
│ (read-only) │                   │ (templates, │                    │   review /   │
└─────────────┘                   │  no LLM)    │                    │   CI diff)   │
                                   └─────────────┘                    └──────┬───────┘
                                                                              │ approve
                                                                              ▼
                                                                      ┌─────────────┐
                                                                      │ executor.py │
                                                                      │ (sandboxed, │
                                                                      │  batch,     │
                                                                      │  quarantine)│
                                                                      └─────────────┘
```

- **`rules.yaml`** — the human-editable config layer. Thresholds, column-name
  keyword lists, currency symbols/suffixes, accepted date formats, and the
  quarantine halt threshold all live here as plain data. A reviewer can
  retune detection for a new legacy source (e.g. add a currency symbol, add
  "balance" to the numeric-keyword list, tighten the halt threshold) by
  editing YAML — no Python change, no platform-team code review needed for
  that class of change. The actual parsing/validation *logic* (e.g. "is this
  a real calendar date") stays in `flaw_catalog.py`, because getting logic
  wrong is how corruption slips through silently; only its parameters are
  exposed here.
- **`flaw_catalog.py`** — the entire taxonomy of detectable corruption
  patterns lives here as plain functions, each paired with a fixed code
  template, reading their tunable parameters from `rules.yaml`. This file is
  the actual "intelligence" of the system, and it's 100% static, reviewable,
  and unit-testable. New corruption *pattern* → new catalog entry → code
  review → merge. New *threshold or keyword* → edit `rules.yaml` → no code
  review required. Not a runtime decision either way.
- **`profiler.py`** — Phase 1. Reflects schema via SQLAlchemy, builds a
  dependency graph (from declared FKs, falling back to confidence-scored
  naming-convention inference when none are declared — very common in
  real legacy DBs), topologically sorts it for load order, samples rows,
  and runs every detector in the catalog. Output: `profile.json`, a plain
  JSON evidence file. No writes, no code generation, no LLM call.
- **`codegen.py`** — Phase 2. Reads `profile.json`, looks up each detected
  flaw in the catalog, and mechanically stitches templates into one
  Python module per table (`generated/<table>_transform.py`). Same input
  → byte-identical output, every time. This is the artifact a human (or
  CI) reviews and approves before it's ever pointed at real data.
- **`executor.py`** — Phase 3. Loads only *vetted* modules (static AST
  allowlist check — rejects `os`, `subprocess`, `eval`, `exec`, `pickle`,
  etc. before import), runs them in dependency order, and routes any row
  that fails validation to a SQLite quarantine table with full context
  (raw row, offending column, reason). If a table's quarantine rate
  exceeds `QUARANTINE_FAILURE_THRESHOLD` (default 30%), that table's run
  is flagged `halted_for_review` — the engine does not keep grinding
  through a systematic issue with newly-invented logic; it stops and
  escalates.

## Run it

```bash
pip install sqlalchemy pydantic networkx pyyaml --break-system-packages
python3 make_legacy_db.py   # builds a synthetic corrupted SQLite DB
python3 profiler.py         # -> profile.json
python3 codegen.py          # -> generated/*.py  (review this before continuing!)
python3 executor.py         # -> quarantine/quarantine.db + console summary
```

## What this design deliberately gives up, and why

- **No fully-automated fix for ambiguous rows.** A quarantined row stays
  quarantined until a human resolves it (either by writing a new catalog
  entry + regenerating, or a one-off manual correction). This is
  intentional — see the earlier critique: silent semantic correction of
  ambiguous legacy data (dangling FK meaning "orphan, delete" vs "parent
  soft-deleted, cascade") is a business decision, not a pattern-match.
- **Regeneration, not patching.** If a new corruption pattern is found
  mid-migration, you add a catalog entry and re-run `codegen.py` — you
  never mutate a generated module in place or patch it during a live run.
  Every generated module is versioned and diffable against the last run.
- **Idempotency over speed.** `executor.py` is written so re-running a
  table after fixing a catalog entry doesn't require any special-casing —
  clean rows are the same content each time; only intended in a full
  implementation to upsert on a stable key, deferred here since the
  synthetic source has no clean natural key on `customers` (worth noting:
  the profiler surfaced that exact issue — duplicate values in the
  presumed key column).

## Where an LLM would actually help (and where it's kept out of the loop)

If you want LLM assistance at all, the safe place for it is **authoring
new catalog entries offline**, e.g.: "here's an anomaly the detectors
didn't catch, draft a candidate detector + template for
`flaw_catalog.py`." That draft still goes through normal code review
before merging — the model never sees a live row of customer data, never
generates code that executes inline, and is never in a position where
malicious row content could reach it (no prompt-injection surface). That
preserves the entire benefit (less manual template-writing) without any
of the risk profile from the original agentic design.

## Extended Capabilities & Wrappers

### 1. The Pipeline Wrapper (`run_pipeline.py`)
To make testing easier against real online databases (e.g. PostgreSQL, MySQL), a CLI wrapper is included. It orchestrates the 3 phases sequentially.
```bash
python3 run_pipeline.py --db-url "postgresql://user:password@hostname/dbname"
```
Flags available: `--skip-profiler`, `--skip-codegen`, `--skip-executor`, and `--auto-approve`.

### 2. Automated Case Studies (`auto_case_study.py`)
To generate proof-of-concept case studies for proposals, this script automates the downloading of messy CSVs, loads them into SQLite, runs the pipeline, and generates a formatted Markdown report. All output is safely isolated into `caseStudy/<dataset_name>/` directories.
```bash
# Run all default case studies
python3 auto_case_study.py --all

# Run a specific custom case study
python3 auto_case_study.py --name "Custom Data" --csv-url "https://raw...csv"
```

### 3. Unit Testing
The core `flaw_catalog.py` detection rules are fully unit-testable.
```bash
# Install testing dependencies
pip install pytest pandas

# Run the test suite
python3 -m pytest tests/
```