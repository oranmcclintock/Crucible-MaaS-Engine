"""
tests/test_dossier_invariants.py
================================
Formal invariant test suite for the Crucible Migration Readiness Dossier
(`templates/readiness_dossier.html.j2` + `reportgen.py`).

Enforces 4 formal invariants:
  1. Air-Gapped Invariant: Zero external stylesheets, scripts, or @import URLs.
  2. XSS & URI Safety Invariant: Autoescaped brand/table/column XSS payloads,
     strict hex brand_color, safe brand_logo URIs, and safe canonical_url.
  3. Manpage Paper & Ink Typography & Token Invariant: OKLCH semantic tokens,
     tabular-nums, .dark theme + #theme-toggle, and >= 12px font-size floor.
  4. Public GEO & Accessibility Invariant: <main>, single <h1>, meta description,
     canonical link, OpenGraph tags, and valid JSON-LD structured data.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest

import reportgen


def _minimal_profile(
    table_name: str = "citizens",
    column_name: str = "full_name",
) -> dict[str, Any]:
    """Build a minimal, deterministic profile dictionary for dossier rendering."""
    return {
        "metadata": {
            "schemafirst_version": "2.0.0",
            "generated_at": "2026-11-25T16:00:00+00:00",
            "source_redacted_url": "sqlite:///demo_council.db",
            "dialect": "sqlite",
            "sample_size_per_table": 100,
        },
        "executive_summary": {
            "overall_readiness_score": 72,
            "readiness_grade": "AMBER - MODERATE REMEDIATION REQUIRED",
            "total_tables": 1,
            "total_columns": 1,
            "estimated_remediation_hours": 4.0,
            "blocker_counts": {
                "dms_replication_blockers": 1,
                "referential_integrity_failures": 0,
                "semantic_data_rot": 0,
                "shadow_pii_exposures": 0,
                "custom_rules": 0,
            },
        },
        "topology": {
            "load_order": [table_name],
            "fk_cycle_warning": [],
            "mermaid_dag": f"graph TD;\n  {table_name};",
            "edges": [],
        },
        "tables": {
            table_name: {
                "row_count_estimate": 100,
                "row_sample_size": 100,
                "table_readiness_score": 72,
                "declared_pk": ["id"],
                "dms_blockers": [
                    {
                        "blocker_id": "postgres_null_byte_poison",
                        "severity": "CRITICAL",
                        "column": column_name,
                        "description": f"Blocker on {column_name}",
                    }
                ],
                "declared_fks": [],
                "inferred_fks": [],
                "dangling_fk_findings": [],
                "columns": {
                    column_name: {
                        "declared_type": "TEXT",
                        "recommended_target_type": "TEXT",
                        "nullable": True,
                        "is_declared_pk": False,
                        "null_rate": 0.0,
                        "distinct_rate": 1.0,
                        "flaws": [
                            {
                                "flaw_id": "postgres_null_byte_poison",
                                "category": "dms_replication_blockers",
                                "severity": "CRITICAL",
                                "confidence": 1.0,
                                "affected_rate": 0.05,
                                "evidence": f"Detected NUL byte in {column_name}",
                                "remediation_advice": "Strip NUL bytes.",
                                "dbt_sql_expr": f'REPLACE("{column_name}", CHR(0), \'\')',
                                "diagnostic_sql": f'SELECT "{column_name}" FROM "{table_name}";',
                            }
                        ],
                    }
                },
            }
        },
    }


def _extract_style_blocks(html: str) -> str:
    """Concatenate all inline <style> block contents from rendered HTML."""
    blocks = re.findall(r"<style\b[^>]*>(.*?)</style>", html, flags=re.IGNORECASE | re.DOTALL)
    return "\n".join(blocks)


# =============================================================================
# 1. Air-Gapped Invariant
# =============================================================================


def test_air_gapped_invariant_no_external_assets(tmp_path: Path) -> None:
    """Rendered HTML must contain zero external stylesheet, script, or @import HTTP(S) refs."""
    out_html = tmp_path / "dossier.html"
    reportgen.generate_dossier(_minimal_profile(), str(out_html))
    html = out_html.read_text(encoding="utf-8")

    # External <link rel="stylesheet" href="http..."> (attribute order independent)
    ext_stylesheet_a = re.findall(
        r"""<link\b[^>]*\brel=["']?stylesheet["']?[^>]*\bhref=["']?https?://[^"'\s>]+""",
        html,
        flags=re.IGNORECASE,
    )
    ext_stylesheet_b = re.findall(
        r"""<link\b[^>]*\bhref=["']?https?://[^"'\s>]+[^>]*\brel=["']?stylesheet["']?""",
        html,
        flags=re.IGNORECASE,
    )
    assert not ext_stylesheet_a and not ext_stylesheet_b, (
        f"Found external stylesheet reference(s): {ext_stylesheet_a + ext_stylesheet_b}"
    )

    # External <script src="http...">
    ext_scripts = re.findall(
        r"""<script\b[^>]*\bsrc=["']?https?://[^"'\s>]+""",
        html,
        flags=re.IGNORECASE,
    )
    assert not ext_scripts, f"Found external script reference(s): {ext_scripts}"

    # External CSS @import url(http...)
    ext_imports = re.findall(
        r"""@import\s+(?:url\(\s*)?["']?https?://[^"'\s)]+""",
        html,
        flags=re.IGNORECASE,
    )
    assert not ext_imports, f"Found external CSS @import reference(s): {ext_imports}"


# =============================================================================
# 2. XSS & URI Safety Invariant
# =============================================================================


XSS_PAYLOADS = [
    "<script>alert(1)</script>",
    '"><img src=x onerror=alert(1)>',
    "'><svg/onload=alert(1)>",
]


@pytest.mark.parametrize("payload", XSS_PAYLOADS)
def test_xss_autoescaping_in_branding_and_schema_identifiers(
    tmp_path: Path, payload: str
) -> None:
    """Malicious brand_name, brand_footer, table, and column names must be autoescaped."""
    profile = _minimal_profile(
        table_name=f"tbl_{payload}",
        column_name=f"col_{payload}",
    )
    branding = {
        "brand_name": f"Acme {payload}",
        "brand_footer": f"Footer {payload}",
        "brand_color": "#0F172A",
        "brand_logo": None,
    }
    out_html = tmp_path / "xss_dossier.html"
    reportgen.generate_dossier(profile, str(out_html), branding=branding)
    html = out_html.read_text(encoding="utf-8")

    assert payload not in html, f"Unescaped XSS payload found in rendered HTML: {payload!r}"


@pytest.mark.parametrize(
    "bad_color",
    [
        "red",
        "#12345",
        "#GGGGGG",
        "#0F172A; } body { display: none",
        "rgb(0, 0, 0)",
        "expression(alert(1))",
        "",
    ],
)
def test_brand_color_rejects_non_hex(bad_color: str) -> None:
    """resolve_branding must reject any non-hex brand_color."""
    with pytest.raises(ValueError, match="brand_color"):
        reportgen.resolve_branding(brand_color=bad_color)


@pytest.mark.parametrize(
    "bad_logo",
    [
        "javascript:alert(1)",
        "JAVASCRIPT:alert(1)",
        "data:text/html,<script>alert(1)</script>",
        "data:text/html;base64,PHNjcmlwdD5hbGVydCgxKTwvc2NyaXB0Pg==",
        "vbscript:msgbox(1)",
        "file:///etc/passwd",
    ],
)
def test_brand_logo_rejects_unsafe_uris(bad_logo: str) -> None:
    """resolve_branding must reject javascript:, data:text/html, and other unsafe logo URIs."""
    with pytest.raises(ValueError, match="brand_logo"):
        reportgen.resolve_branding(brand_logo=bad_logo)


@pytest.mark.parametrize(
    "bad_canonical",
    [
        "javascript:alert(1)",
        "data:text/html,<script>alert(1)</script>",
        "ftp://example.com/dossier.html",
        "//evil.example.com/dossier.html",
        "file:///etc/passwd",
    ],
)
def test_canonical_url_rejects_non_http_schemes_or_falls_back_safely(
    tmp_path: Path, bad_canonical: str
) -> None:
    """canonical_url must reject non-http(s):// schemes or omit/sanitize them safely."""
    out_html = tmp_path / "canonical_unsafe.html"
    try:
        reportgen.generate_dossier(
            _minimal_profile(),
            str(out_html),
            canonical_url=bad_canonical,
        )
    except ValueError:
        return  # Explicit rejection is valid

    # If it fell back safely instead of raising, the unsafe URI must never appear in a canonical link
    html = out_html.read_text(encoding="utf-8")
    canonical_tags = re.findall(
        r"""<link\b[^>]*\brel=["']?canonical["']?[^>]*>""",
        html,
        flags=re.IGNORECASE,
    )
    for tag in canonical_tags:
        assert bad_canonical not in tag, f"Unsafe canonical_url rendered in link tag: {tag}"
        assert re.search(r"""href=["']https?://""", tag, flags=re.IGNORECASE), (
            f"Canonical link tag does not use http(s)://: {tag}"
        )


# =============================================================================
# 3. Manpage Paper & Ink Typography & Token Invariant
# =============================================================================


def test_manpage_paper_and_ink_tokens_and_dark_theme(tmp_path: Path) -> None:
    """Rendered HTML <style> must define OKLCH tokens, tabular-nums, .dark theme, and #theme-toggle."""
    out_html = tmp_path / "tokens_dossier.html"
    reportgen.generate_dossier(_minimal_profile(), str(out_html))
    html = out_html.read_text(encoding="utf-8")
    styles = _extract_style_blocks(html)

    assert styles, "Expected inline <style> block in rendered HTML"

    for token in ("--background", "--foreground", "--border", "--muted"):
        assert re.search(
            rf"{re.escape(token)}\s*:\s*oklch\(",
            styles,
            flags=re.IGNORECASE,
        ), f"Missing OKLCH definition for {token} in <style>"

    assert "tabular-nums" in styles, "Missing tabular-nums in <style>"
    assert re.search(r"\.dark\b", styles), "Missing .dark (Dark Ink) theme selector in <style>"
    assert 'id="theme-toggle"' in html or "id='theme-toggle'" in html, (
        "Missing #theme-toggle control in rendered HTML"
    )


def test_typography_minimum_12px_floor(tmp_path: Path) -> None:
    """Rendered HTML <style> must have ZERO font-size declarations below 12px."""
    out_html = tmp_path / "font_floor_dossier.html"
    reportgen.generate_dossier(_minimal_profile(), str(out_html))
    html = out_html.read_text(encoding="utf-8")
    styles = _extract_style_blocks(html)

    px_sizes = re.findall(r"font-size\s*:\s*([0-9]*\.?[0-9]+)\s*px", styles, flags=re.IGNORECASE)
    violations = [float(size) for size in px_sizes if float(size) < 12.0]
    assert not violations, f"Found font-size declarations below 12px floor: {violations}"


# =============================================================================
# 4. Public GEO & Accessibility Invariant
# =============================================================================


def test_public_geo_and_accessibility_invariants(tmp_path: Path) -> None:
    """Rendered HTML must satisfy landmark, single-H1, meta, canonical, OG, and JSON-LD invariants."""
    out_html = tmp_path / "geo_dossier.html"
    canonical = "https://crucible.example.com/demo/readiness-dossier.html"
    reportgen.generate_dossier(
        _minimal_profile(),
        str(out_html),
        canonical_url=canonical,
    )
    html = out_html.read_text(encoding="utf-8")

    # Landmark <main>
    assert re.search(r"<main\b", html, flags=re.IGNORECASE), "Missing <main> landmark"

    # Exactly one <h1>
    h1_tags = re.findall(r"<h1\b", html, flags=re.IGNORECASE)
    assert len(h1_tags) == 1, f"Expected exactly 1 <h1> element, found {len(h1_tags)}"

    # Meta description
    assert re.search(
        r"""<meta\b[^>]*\bname=["']description["'][^>]*\bcontent=["'][^"']+["']""",
        html,
        flags=re.IGNORECASE,
    ) or re.search(
        r"""<meta\b[^>]*\bcontent=["'][^"']+["'][^>]*\bname=["']description["']""",
        html,
        flags=re.IGNORECASE,
    ), 'Missing <meta name="description" content="..."> tag'

    # Canonical link when canonical_url is set
    assert re.search(
        rf"""<link\b[^>]*\brel=["']canonical["'][^>]*\bhref=["']{re.escape(canonical)}["']""",
        html,
        flags=re.IGNORECASE,
    ) or re.search(
        rf"""<link\b[^>]*\bhref=["']{re.escape(canonical)}["'][^>]*\brel=["']canonical["']""",
        html,
        flags=re.IGNORECASE,
    ), f'Missing <link rel="canonical" href="{canonical}"> tag'

    # OpenGraph og:title and og:description
    for og_prop in ("og:title", "og:description"):
        assert re.search(
            rf"""<meta\b[^>]*\bproperty=["']{re.escape(og_prop)}["'][^>]*\bcontent=["'][^"']+["']""",
            html,
            flags=re.IGNORECASE,
        ) or re.search(
            rf"""<meta\b[^>]*\bcontent=["'][^"']+["'][^>]*\bproperty=["']{re.escape(og_prop)}["']""",
            html,
            flags=re.IGNORECASE,
        ), f'Missing OpenGraph <meta property="{og_prop}"> tag'

    # Parseable JSON-LD with @context and @type
    ld_matches = re.findall(
        r"""<script\b[^>]*\btype=["']application/ld\+json["'][^>]*>(.*?)</script>""",
        html,
        flags=re.IGNORECASE | re.DOTALL,
    )
    assert ld_matches, 'Missing <script type="application/ld+json"> block'
    parsed_ld = json.loads(ld_matches[0].strip())
    assert isinstance(parsed_ld, dict), "JSON-LD root must be a JSON object"
    assert "@context" in parsed_ld, "JSON-LD missing @context"
    assert "@type" in parsed_ld, "JSON-LD missing @type"
