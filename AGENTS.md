# AGENTS.md — `crucible-audit` v2.0.0

Canonical engineering guide for coding agents working in this repository.

## 1. Non-Negotiable Invariants

1. **Strictly Read-Only**: `crucible-audit` inspects source schemas and sample rows via `SELECT` queries only. It must never create, alter, insert, update, or delete anything in a target database.
2. **Air-Gapped Execution**: Zero outbound network calls at runtime. The generated HTML dossier (`migration_readiness_report.html`) must be 100% self-contained with no external CSS, JS, or font CDNs.
3. **Zero-PII Output Contract**: All emitted artifacts (`profile.json`, `migration_readiness_report.html`, dbt `.sql`/`.yml`, and diagnostic `.sql`) must contain only counts, rates, schema metadata, and rule IDs. Never emit raw cell values, `sample_orphan_values`, partial PII masks (such as NINO/NHS snippets), or packed free-text previews.
4. **XSS-Safe Rendering**:
   - HTML rendering in `reportgen.py` must use Jinja2 `select_autoescape(["html", "xml", "html.j2"])`.
   - SQL and YAML code generation in `codegen.py` must use a separate non-escaped Jinja2 `Environment`.
   - Brand colors must match `^#[0-9A-Fa-f]{3,6}$`; logo URLs must be restricted to `https://`, `http://`, or `data:image/...` schemes.
5. **PostgreSQL Regex Dialect**: All generated PostgreSQL regex diagnostic queries must use POSIX word boundaries (`\y`), never PCRE `\b`.
6. **Scope Boundary (No v1.0 Drift)**: This repository is strictly the v2.0.0 pre-migration profiler and dbt compiler. Do NOT reintroduce `executor.py`, Pydantic live data-movement pipelines, quarantine databases, `k8s/` manifests, or CRM/sales tooling.

## 2. Module Map

Flat Python package defined in `pyproject.toml` (`name = "crucible-audit"`, `version = "2.0.0"`):

| Path | Responsibility |
| --- | --- |
| `cli.py` | Entrypoint (`crucible = "cli:main"`). Dispatches `seed-demo` and `audit` subcommands, branding flags/config loading, and CLI summary output. |
| `profiler.py` | Read-only SQLAlchemy schema reflection, sampling, topological DAG load ordering (`networkx`), shadow FK inference, dangling FK checks (`distinct_orphan_keys`, `orphan_row_count`), readiness scoring (no double-counting of promoted DMS flaws), and `profile.json` output. |
| `flaw_catalog.py` | Rule loading (`rules.yaml`, pack toggles, per-rule `enabled` flags, `custom_rules`) and Zero-PII column/row defect detectors across all 4 pillars. |
| `codegen.py` | Compiles `profile.json` into dbt staging models (`models/staging/`), `schema.yml`, and dialect-aware read-only diagnostic SQL (`diagnostics/`, using `\y` for Postgres). |
| `reportgen.py` | Renders `profile.json` + branding configuration into the air-gapped HTML dossier using Jinja2 with strict HTML autoescaping. |
| `seed_dirty_gov_db.py` | Generates the synthetic UK council SQLite database (`demo_legacy_council.db`) containing representative defects for local testing and demos. |
| `rules.yaml` | Default ruleset with pack toggles (`aws_dms_cdc`, `uk_shadow_pii`, `schema_integrity`, `data_hygiene`), severity/remediation weights, `fk_inference` thresholds, and `custom_rules` support. |
| `templates/` | Jinja2 templates: `readiness_dossier.html.j2` (HTML dossier), `dbt_staging_model.sql.j2`, and `dbt_schema.yml.j2`. |
| `tests/` | `pytest` suite covering flaw detectors, Zero-PII guarantees, XSS escaping, Postgres `\y` regexes, scoring/orphan accuracy, branding, custom rules, and end-to-end CLI runs. |
| `Dockerfile` | Non-root container image (`appuser`) installing `.[postgres]` with `ENTRYPOINT ["crucible"]`. |

## 3. CLI Commands & Flags

### `crucible seed-demo`
Generates a synthetic SQLite test database.
- `--out <path>`: Target SQLite file path (default: `demo_legacy_council.db`).

### `crucible audit`
Runs read-only profiling, dbt compilation, and HTML dossier generation.
- `--db <url>` (required): SQLAlchemy connection URL (`sqlite:///...` or `postgresql+psycopg://...`).
- `--out-dir <dir>`: Output directory (default: `./audit_output`).
- `--rules <path>`: Path to external `rules.yaml` override.
- `--brand-name <str>`: Custom consultancy/organization display name in the HTML dossier.
- `--brand-color <hex>`: Primary accent color (`#RGB` or `#RRGGBB`).
- `--brand-logo <url>`: Logo URL (`https://`, `http://`, or `data:image/...`).
- `--brand-footer <str>`: Custom footer text in the HTML dossier.
- `--brand-config <path>`: Path to a YAML or JSON branding configuration file (CLI flags override config file values).

## 4. Verification Gates

Before finishing any change, verify all applicable gates:

1. **Unit & End-to-End Tests**:
   ```bash
   pytest
   ```
   Must verify Zero-PII output, XSS escaping, Postgres `\y` regexes, accurate orphan counts and non-double-counted DMS scoring, branding flags/config, and external `rules.yaml` pack toggles/custom rules.
2. **Static Linting**:
   ```bash
   ruff check .
   ```
3. **Container Build**:
   ```bash
   docker build -t crucible-audit:2.0.0 .
   ```
4. **Live PostgreSQL Integration Check**:
   Run `crucible audit --db postgresql+psycopg://...` against a live PostgreSQL instance and execute the generated `diagnostics/*.sql` queries against that database to confirm zero syntax or regex errors.
