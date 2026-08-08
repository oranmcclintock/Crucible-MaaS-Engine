"""
codegen.py
==========
Phase 2 of the pipeline. Deterministic and offline: reads profile.json,
looks up each detected flaw in the fixed catalog from flaw_catalog.py, and
mechanically assembles one Python module per table by string-substituting
`{col}` into pre-written, pre-reviewed templates.

There is NO LLM call in this file, at generation time or otherwise. The
mapping from "flaw signature" to "code fragment" is a lookup table, not a
model inference. This means:
  - the same profile.json always produces byte-identical generated code
  - every code path that can possibly be emitted is enumerable and testable
    ahead of time by reviewing flaw_catalog.py once
  - a human (or CI) can diff the generated module against the previous run
    before it is ever pointed at real data
  - there is nothing here for a prompt-injection attack (via malicious row
    content) to influence, because no row content is ever sent to a model

Generated modules are written to generated/<table>_transform.py and are
inert until explicitly imported by executor.py -- generation and execution
are separate, auditable steps with a human gate in between.
"""
import json
import os

from jinja2 import Environment, FileSystemLoader

import flaw_catalog as fc


def flaw_signature(table_profile: dict) -> str:
    """Stable signature of which flaws fired for this table, for header provenance."""
    ids = []
    for col, meta in sorted(table_profile["columns"].items()):
        for f in meta["flaws"]:
            ids.append(f"{col}:{f['flaw_id']}")
    return ",".join(ids) if ids else "none"


def python_type_for(declared_type: str, flaws: list) -> str:
    flaw_ids = {f["flaw_id"] for f in flaws}
    if "text_currency" in flaw_ids or "non_numeric_in_numeric_col" in flaw_ids or "negative_unsigned_field" in flaw_ids:
        return "Optional[Decimal]"
    if "impossible_or_sentinel_date" in flaw_ids:
        return "Optional[Any]"  # date or None (sentinel) -- left as Any so Pydantic doesn't reject None dates
    dt = declared_type.upper()
    if "INT" in dt:
        return "Optional[int]"
    if "REAL" in dt or "FLOAT" in dt or "DOUBLE" in dt or "NUMERIC" in dt:
        return "Optional[float]"
    if "CHAR" in dt or "TEXT" in dt or "CLOB" in dt:
        return "Optional[str]"
    return "Optional[str]"


def generate_table_module(table: str, table_profile: dict) -> str:
    env = Environment(loader=FileSystemLoader("templates"))
    template = env.get_template("module.py.j2")

    sig = flaw_signature(table_profile)

    cleaner_funcs = []       # generated helper function bodies (from templates)
    body_lines = []          # lines inside clean_row()
    model_fields = []
    seen_key_sets = []       # for duplicate-key template's `seen_set` closure

    for col, meta in table_profile["columns"].items():
        flaws = meta["flaws"]
        if not flaws:
            model_fields.append(f"    {col}: {python_type_for(meta['declared_type'], flaws)} = None")
            continue

        # highest-confidence flaw for this column drives the primary cleaner;
        # additional flaws on the same column are appended as extra checks.
        flaws_sorted = sorted(flaws, key=lambda f: -f["confidence"])
        for f in flaws_sorted:
            flaw_id = f["flaw_id"]
            if flaw_id not in fc.TEMPLATES:
                continue
            tmpl = fc.TEMPLATES[flaw_id].format(col=col)
            cleaner_funcs.append(tmpl)

        # wire the primary (highest-confidence) cleaner into clean_row()
        primary = flaws_sorted[0]["flaw_id"]
        if primary == "control_chars":
            body_lines.append(f'    out["{col}"] = _clean_control_chars_{col}(out.get("{col}"))')
        elif primary == "text_currency":
            body_lines.append(f'    out["{col}"] = _clean_currency_{col}(out.get("{col}"))')
        elif primary == "non_numeric_in_numeric_col":
            body_lines.append(f'    out["{col}"] = _clean_numeric_{col}(out.get("{col}"))')
        elif primary == "impossible_or_sentinel_date":
            body_lines.append(f'    out["{col}"] = _clean_date_{col}(out.get("{col}"))')
        elif primary == "overloaded_freetext_column":
            body_lines.append(
                f'    _pairs_{col}, _raw_{col} = _unpack_freetext_{col}(out.get("{col}"))\n'
                f'    out["{col}"] = _raw_{col}\n'
                f'    out["{col}_parsed_fields"] = _pairs_{col}  # promoted for optional downstream mapping, never dropped'
            )
            # extra="forbid" on the model means every field we add to `out` must
            # be declared -- otherwise every single row fails validation.
            model_fields.append(f"    {col}_parsed_fields: dict = {{}}")
        elif primary == "duplicate_key_values":
            seen_key_sets.append(col)
            body_lines.append(f'    out["{col}"] = _flag_duplicate_key_{col}(out.get("{col}"), _seen_{col})')
        elif primary == "negative_unsigned_field":
            body_lines.append(f'    out["{col}"] = _check_unsigned_{col}(out.get("{col}"))')

        model_fields.append(f"    {col}: {python_type_for(meta['declared_type'], flaws)} = None")

    model_name = "".join(p.capitalize() for p in table.split("_")) + "Clean"
    
    return template.render(
        table=table,
        sig=sig,
        cleaner_funcs=cleaner_funcs,
        seen_key_sets=seen_key_sets,
        model_name=model_name,
        model_fields=model_fields,
        body_lines=body_lines
    )


def generate_all(profile_path="profile.json", out_dir="generated"):
    with open(profile_path) as f:
        profile = json.load(f)

    os.makedirs(out_dir, exist_ok=True)
    generated_files = []
    for table, table_profile in profile["tables"].items():
        code = generate_table_module(table, table_profile)
        path = os.path.join(out_dir, f"{table}_transform.py")
        with open(path, "w") as f:
            f.write(code)
        generated_files.append(path)
    return generated_files


if __name__ == "__main__":
    files = generate_all()
    print("Generated (review before executing):")
    for f in files:
        print(f"  {f}")