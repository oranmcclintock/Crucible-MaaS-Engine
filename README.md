# SchemaFirst (`schemafirst`)

`schemafirst` (v2.0.0) is a free, open-source, read-only pre-migration profiler and dbt compiler for SQLite and PostgreSQL databases. It scans legacy schemas before a migration starts, scores readiness across four defect categories, and writes a static HTML dossier, a JSON evidence file, dbt staging models, and diagnostic SQL queries.

**Live Demo Dossier:** [https://schemafirst.parkanore.com](https://schemafirst.parkanore.com) | **Compiled dbt & Diagnostic SQL:** [`demo_dossier/generated_dbt/`](demo_dossier/generated_dbt/)

## Core invariants

The tool runs locally with no network calls and no external SaaS dependencies. It connects to target databases in read-only mode and never alters source tables, rows, or schemas.

All generated outputs follow a Zero-PII contract. `profile.json`, the HTML dossier, and the compiled SQL files record only counts, rates, column metadata, and rule IDs. Raw cell values, sample orphan keys, and partial PII masks are never written to disk.

## Installation

Requires Python 3.10 or newer.

Install the base package for SQLite:

```bash
pip install .
```

Install with the PostgreSQL driver (`psycopg`):

```bash
pip install ".[postgres]"
```

## Quickstart

Generate a synthetic SQLite database containing common legacy schema defects, then run an audit against it:

```bash
schemafirst seed-demo
schemafirst audit --db sqlite:///demo_legacy_council.db --out-dir ./demo_dossier
```

The command writes the following files inside `./demo_dossier`:

- `profile.json`: machine-readable audit evidence, readiness score, and topological load order
- `migration_readiness_report.html`: self-contained HTML dossier that opens offline in any browser
- `generated_dbt/models/staging/`: dbt staging SQL and `src_legacy.yml` source definitions
- `generated_dbt/diagnostics/`: read-only SQL queries for investigating flagged defects

## PostgreSQL usage

Point `--db` at a PostgreSQL connection URL using the `postgresql+psycopg://` dialect:

```bash
schemafirst audit \
  --db "postgresql+psycopg://readonly_user:password@db.internal:5432/legacy_db" \
  --out-dir ./audit_out
```

Generated PostgreSQL diagnostic queries use POSIX word boundaries (`\y`) for regex rules and read-only `SELECT` statements.

## Branding and custom rules

Teams can white-label the HTML dossier and customize detection rules without modifying Python code.

### White-label dossier branding

Pass branding flags directly to `schemafirst audit`:

```bash
schemafirst audit \
  --db sqlite:///demo_legacy_council.db \
  --out-dir ./audit_out \
  --brand-name "Northwind Data Advisory" \
  --brand-color "#1E40AF" \
  --brand-footer "Prepared for Client Discovery"
```

Or load defaults from a YAML or JSON config file via `--brand-config`:

```bash
schemafirst audit \
  --db sqlite:///demo_legacy_council.db \
  --out-dir ./audit_out \
  --brand-config ./branding.yaml
```

CLI flags override values from `--brand-config`. Brand colors accept 3-digit or 6-digit hex values (`#RGB` or `#RRGGBB`), and logo URLs accept `https://`, `http://`, or `data:image/...;base64,` URIs.

### External `rules.yaml`

Override the built-in ruleset with `--rules <path>` to toggle rule packs (`aws_dms_cdc`, `uk_shadow_pii`, `schema_integrity`, `data_hygiene`), disable individual rules, tune `fk_inference.min_overlap_ratio`, or add `custom_rules` regex detectors:

```bash
schemafirst audit \
  --db sqlite:///demo_legacy_council.db \
  --out-dir ./audit_out \
  --rules ./custom_rules.yaml
```

See [rules.yaml](rules.yaml) for the default configuration format.

## Docker

Build the container image with PostgreSQL support included:

```bash
docker build -t schemafirst:2.0.0 .
```

Run an audit against a local SQLite file:

```bash
docker run --rm -v "$(pwd):/work" -w /work schemafirst:2.0.0 \
  audit --db sqlite:///demo_legacy_council.db --out-dir /work/audit_out
```

Run an audit against a PostgreSQL server:

```bash
docker run --rm -v "$(pwd)/audit_out:/out" schemafirst:2.0.0 \
  audit --db "postgresql+psycopg://readonly_user:password@host.docker.internal:5432/legacy_db" --out-dir /out
```

## Detection categories

`schemafirst audit` groups findings into four categories:

- DMS CDC blockers: missing primary keys, wide LOB columns, NUL bytes (`0x00`), CP1252 mojibake sequences, and legacy proprietary types
- Referential integrity: declared foreign keys, inferred shadow foreign keys, circular dependency detection, and separate counts for orphaned rows vs. distinct orphan keys
- Data hygiene: currency strings in numeric columns, non-numeric text in numeric fields, impossible or sentinel dates, mixed date locales, negative values in unsigned fields, overloaded free-text columns, and control characters
- Shadow PII: UK National Insurance numbers, Modulus-11 validated NHS numbers, and legacy redaction markers

## Development

Run the test suite and linter:

```bash
pytest
ruff check .
```