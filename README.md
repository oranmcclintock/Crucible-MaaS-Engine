# crucible-audit

`crucible-audit` (v2.0.0) is a free, open-source, read-only pre-migration profiler and dbt compiler for SQLite and PostgreSQL databases. It scans legacy schemas before a migration starts, scores readiness across four defect categories, and writes a static HTML dossier, a JSON evidence file, dbt staging models, and diagnostic SQL queries.

**Live Demo Dossier:** [https://crucible.parkanore.com](https://crucible.parkanore.com) | **Compiled dbt & Diagnostic SQL:** [`demo_dossier/generated_dbt/`](demo_dossier/generated_dbt/)

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
crucible seed-demo
crucible audit --db sqlite:///demo_legacy_council.db --out-dir ./demo_dossier
```

The command writes the following files inside `./demo_dossier`:

- `profile.json`: machine-readable audit evidence, readiness score, and topological load order
- `migration_readiness_report.html`: self-contained HTML dossier that opens offline in any browser
- `generated_dbt/models/staging/`: dbt staging SQL and `src_legacy.yml` source definitions
- `generated_dbt/diagnostics/`: read-only SQL queries for investigating flagged defects

## PostgreSQL usage

Point `--db` at a PostgreSQL connection URL using the `postgresql+psycopg://` dialect:

```bash
crucible audit \
  --db "postgresql+psycopg://readonly_user:password@db.internal:5432/legacy_db" \
  --out-dir ./audit_out
```

Generated PostgreSQL diagnostic queries use POSIX word boundaries (`\y`) for regex rules and read-only `SELECT` statements.

## Branding and custom rules

Consultancies and internal platform teams can white-label the HTML dossier and customize detection rules without modifying Python code.

### White-label dossier branding

Override report branding via CLI flags:

```bash
crucible audit \
  --db sqlite:///demo_legacy_council.db \
  --out-dir ./client_dossier \
  --brand-name "Northgate Data Advisory" \
  --brand-color "#1D4ED8" \
  --brand-logo "https://example.com/logo.png" \
  --brand-footer "Prepared for Migration Readiness Review"
```

You can also pass a YAML or JSON file with `--brand-config`:

```bash
crucible audit \
  --db sqlite:///demo_legacy_council.db \
  --out-dir ./client_dossier \
  --brand-config ./brand.yaml
```

Brand colors must be valid 3-digit or 6-digit hex values (`#RGB` or `#RRGGBB`), and logo URLs must use `https://`, `http://`, or `data:image/`.

### External rules configuration

Pass a custom rules file with `--rules` to toggle built-in rule packs (`aws_dms_cdc`, `uk_shadow_pii`, `schema_integrity`, `data_hygiene`), enable or disable individual rules, adjust `fk_inference.min_overlap_ratio`, or add custom regex checks under `custom_rules`:

```bash
crucible audit \
  --db sqlite:///demo_legacy_council.db \
  --out-dir ./audit_out \
  --rules ./custom_rules.yaml
```

See the bundled `rules.yaml` for the full schema and default thresholds.

## Docker usage

Build the container image (includes the PostgreSQL driver and runs as a non-root user):

```bash
docker build -t crucible-audit:2.0.0 .
```

Run an audit against a local SQLite database by mounting a working directory:

```bash
docker run --rm -v "$(pwd):/work" -w /work crucible-audit:2.0.0 \
  audit --db sqlite:///demo_legacy_council.db --out-dir ./demo_dossier
```

Run against a reachable PostgreSQL host:

```bash
docker run --rm -v "$(pwd)/audit_out:/out" crucible-audit:2.0.0 \
  audit --db "postgresql+psycopg://readonly_user:password@host.docker.internal:5432/legacy_db" \
  --out-dir /out
```

## What the audit checks

`crucible audit` groups findings into four categories:

1. AWS DMS and CDC blockers: ASCII NUL (`0x00`) bytes, CP1252/UTF-8 mojibake sequences, and proprietary legacy column types.
2. Referential and key integrity: duplicate primary keys, whitespace-padded keys, inferred shadow foreign keys, and dangling references (reported as `distinct_orphan_keys` and `orphan_row_count`).
3. Semantic data rot: ASCII control characters, text-formatted currency, non-numeric strings in numeric columns, sentinel or impossible dates, mixed UK/US date formats, negative values in unsigned fields, and key-value pairs packed into free-text columns.
4. Shadow PII exposures: unmasked UK National Insurance numbers, Modulus-11 verified UK NHS numbers, and legacy redaction markers.

## Development and testing

Install development dependencies and run the test suite:

```bash
pip install -e ".[dev,postgres]"
pytest
```