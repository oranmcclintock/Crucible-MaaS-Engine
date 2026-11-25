"""
cli.py
======
Unified Command Line Interface for the Crucible Migration Readiness Engine.

Commands:
  crucible seed-demo [--out demo_legacy_council.db]
  crucible audit --db <db_url> [--out-dir ./audit_output] [--rules rules.yaml]
"""
from __future__ import annotations

import argparse
import os

import codegen
import profiler
import reportgen
import seed_dirty_gov_db


def print_cli_summary(profile: dict, html_report_path: str, dbt_dir: str):
    exec_sum = profile.get("executive_summary", {})
    meta = profile.get("metadata", {})
    b_counts = exec_sum.get("blocker_counts", {})

    print("\n" + "=" * 76)
    print(" CRUCIBLE MIGRATION READINESS AUDIT COMPLETE (v2.0)")
    print("=" * 76)
    print(f" Source Target:        {meta.get('source_redacted_url')}")
    print(f" Overall Readiness:    {exec_sum.get('overall_readiness_score')}/100")
    print(f" Readiness Grade:      {exec_sum.get('readiness_grade')}")
    print(f" Remediation Effort:   {exec_sum.get('estimated_remediation_hours')} hours")
    print("-" * 76)
    print(" 4 Enterprise Migration Pillars:")
    print(f"   [1] DMS CDC Blockers:           {b_counts.get('dms_replication_blockers', 0)}")
    print(f"   [2] Referential Failures:       {b_counts.get('referential_integrity_failures', 0)}")
    print(f"   [3] Semantic Data Rot:          {b_counts.get('semantic_data_rot', 0)}")
    print(f"   [4] Shadow PII Exposures:       {b_counts.get('shadow_pii_exposures', 0)}")
    print("-" * 76)
    print(" Generated Artifacts:")
    print(f"   * Executive HTML Dossier:       {html_report_path}")
    print(f"   * Raw Audit Evidence Contract:  {os.path.join(os.path.dirname(html_report_path), 'profile.json')}")
    print(f"   * dbt Staging & Quarantine:     {dbt_dir}/models/staging/")
    print(f"   * DBA Diagnostic SQL Queries:   {dbt_dir}/diagnostics/")
    print("=" * 76 + "\n")


def cmd_seed_demo(args):
    out = seed_dirty_gov_db.seed_database(args.out)
    print(f"Successfully generated corrupted UK council test database: {out}")


def cmd_audit(args):
    branding = reportgen.resolve_branding(
        brand_config_path=args.brand_config,
        brand_name=args.brand_name,
        brand_color=args.brand_color,
        brand_logo=args.brand_logo,
        brand_footer=args.brand_footer,
    )

    out_dir = os.path.abspath(args.out_dir)
    os.makedirs(out_dir, exist_ok=True)

    profile_json_path = os.path.join(out_dir, "profile.json")
    html_report_path = os.path.join(out_dir, "migration_readiness_report.html")
    dbt_dir = os.path.join(out_dir, "generated_dbt")

    print(f"[*] Profiling legacy database: {profiler.mask_db_url(args.db)}...")
    profile = profiler.run_profile(
        db_url=args.db,
        out_path=profile_json_path,
        rules_path=args.rules,
    )

    print("[*] Compiling dbt staging package and diagnostic packs...")
    codegen.compile_dbt_package(profile_path=profile_json_path, out_dir=dbt_dir)

    print("[*] Generating air-gapped HTML schema audit...")
    reportgen.generate_dossier(
        profile=profile,
        output_html_path=html_report_path,
        dbt_dir=dbt_dir,
        branding=branding,
    )

    print_cli_summary(profile, html_report_path, dbt_dir)


def main():
    parser = argparse.ArgumentParser(
        prog="crucible",
        description="Crucible: Deterministic Migration Readiness Engine & dbt Compiler",
    )
    parser.add_argument(
        "--version",
        action="version",
        version="%(prog)s 2.0.0",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # seed-demo subcommand
    p_seed = subparsers.add_parser("seed-demo", help="Generate corrupted UK council demo database")
    p_seed.add_argument("--out", default="demo_legacy_council.db", help="Target SQLite file path (default: demo_legacy_council.db)")
    p_seed.set_defaults(func=cmd_seed_demo)

    # audit subcommand
    p_audit = subparsers.add_parser("audit", help="Run full discovery, dbt compiler, and HTML dossier pipeline")
    p_audit.add_argument("--db", required=True, help="Database connection URL (e.g., sqlite:///db.db or postgresql://...)")
    p_audit.add_argument("--out-dir", default="./audit_output", help="Output directory for generated dossier and dbt package")
    p_audit.add_argument("--rules", default=None, help="Path to custom rules.yaml file")
    p_audit.add_argument("--brand-name", default=None, help="White-label brand name for HTML dossier header")
    p_audit.add_argument("--brand-color", default=None, help="White-label hex brand color (e.g. #0F172A)")
    p_audit.add_argument("--brand-logo", default=None, help="White-label logo URL (https://, http://, or data:image/...;base64,...)")
    p_audit.add_argument("--brand-footer", default=None, help="White-label footer text for HTML dossier")
    p_audit.add_argument("--brand-config", default=None, help="Path to YAML or JSON branding configuration file")
    p_audit.set_defaults(func=cmd_audit)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
