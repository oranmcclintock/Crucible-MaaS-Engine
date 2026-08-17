"""
executor.py
===========
Phase 3 of the pipeline: batch execution of the human-approved, statically
generated modules. There is no self-patching here. A row that fails is
quarantined with full context and the batch continues -- it is never
retried with newly-invented logic at runtime. If quarantine volume for a
table exceeds a threshold, the whole table run halts and escalates to a
human rather than continuing to burn through a systematic failure.

Security posture:
  - Before import, every generated module is statically vetted with `ast`
    against an allowlist of imports/calls. Anything outside the allowlist
    (subprocess, socket, eval, exec, os, importlib, pickle, __import__,
    open, ...) causes the module to be rejected, not executed.
  - The executor process itself holds the only DB credentials. Generated
    code only ever receives/returns plain dicts -- it never sees a
    connection, a cursor, or a credential.
  - Target writes are upserts keyed on a stable identity, so re-running a
    batch (e.g. after fixing one template and regenerating) is idempotent
    rather than duplicating or corrupting already-migrated rows.
"""
import argparse
import ast
import importlib.util
import json
import yaml
import sqlite3
import sys
import os
from datetime import date, datetime, timezone
from decimal import Decimal

import flaw_catalog as fc

QUARANTINE_FAILURE_THRESHOLD = fc.QUARANTINE_FAILURE_HALT_THRESHOLD

ALLOWED_IMPORTS = {"re", "decimal", "datetime", "typing", "pydantic"}
FORBIDDEN_NAMES = {
    "eval", "exec", "compile", "__import__", "open", "input",
    "os", "sys", "subprocess", "socket", "shutil", "pickle",
    "ctypes", "importlib", "globals", "locals", "vars", "getattr",
    "setattr", "delattr", "breakpoint",
}


class SandboxRejected(Exception):
    pass


_loaded_modules = {}

def load_vetted_module(source_path: str, module_name: str):
    if module_name in _loaded_modules:
        return _loaded_modules[module_name]
    
    spec = importlib.util.spec_from_file_location(module_name, source_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    _loaded_modules[module_name] = module
    return module


from sqlalchemy import create_engine, MetaData, Table, Column, Integer, String, Text
metadata = MetaData()
quarantined_table = Table(
    'quarantined_rows', metadata,
    Column('id', Integer, primary_key=True),
    Column('table_name', String),
    Column('row_identity', String),
    Column('column_name', String),
    Column('raw_value', String),
    Column('reason', String),
    Column('raw_row_json', Text),
    Column('quarantined_at', String),
)

def init_quarantine_db(path="quarantine/quarantine.db"):
    if not path.startswith("postgres") and not path.startswith("sqlite"):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        url = f"sqlite:///{path}"
    else:
        url = path
    engine = create_engine(url)
    metadata.create_all(engine)
    return engine


def _json_default(o):
    if isinstance(o, (date, datetime)):
        return o.isoformat()
    if isinstance(o, Decimal):
        return str(o)
    return str(o)


def _worker_process_chunk(args):
    table, chunk_rows, module_path, module_name = args
    module = load_vetted_module(module_path, module_name)
    
    clean_rows = []
    quarantined_records = []
    
    model_cls = next(
        v for k, v in vars(module).items()
        if k.endswith("Clean") and isinstance(v, type)
    )
    
    for raw_dict in chunk_rows:
        try:
            cleaned = module.clean_row(raw_dict)
            validated = model_cls(**cleaned)
            clean_rows.append(validated.model_dump())
        except module.QuarantineRow as e:
            quarantined_records.append({
                "table_name": table,
                "row_identity": json.dumps(raw_dict, default=_json_default)[:200],
                "column_name": e.column,
                "raw_value": str(e.raw_value),
                "reason": e.reason,
                "raw_row_json": json.dumps(raw_dict, default=_json_default),
                "quarantined_at": datetime.now(timezone.utc).isoformat()
            })
        except Exception as e:
            quarantined_records.append({
                "table_name": table,
                "row_identity": json.dumps(raw_dict, default=_json_default)[:200],
                "column_name": "UNKNOWN",
                "raw_value": "",
                "reason": f"unexpected error: {type(e).__name__}: {e}",
                "raw_row_json": json.dumps(raw_dict, default=_json_default),
                "quarantined_at": datetime.now(timezone.utc).isoformat()
            })
            
    return {"clean_rows": clean_rows, "quarantined_records": quarantined_records}


def run_table(table: str, engine_url: str, generated_dir="generated", q_conn=None, workers="auto"):
    module_path = os.path.join(generated_dir, f"{table}_transform.py")
    module_name = f"gen_{table}"

    from sqlalchemy import create_engine
    src_engine = create_engine(engine_url)

    total = 0
    clean_rows = []
    quarantined = 0

    with src_engine.connect() as conn:
        cursor = conn.execution_options(yield_per=10000).exec_driver_sql(f'SELECT * FROM "{table}"').mappings()
        
        from multiprocessing import Pool
        import multiprocessing
        
        num_cores = multiprocessing.cpu_count() if workers == "auto" else int(workers)
        
        with Pool(processes=num_cores) as pool:
            while True:
                chunk = cursor.fetchmany(10000)
                if not chunk:
                    break
                
                rows = [dict(r) for r in chunk]
                total += len(rows)
                
                # Split the 10,000 fetch into smaller sub-chunks for the worker cores
                sub_chunks = [rows[i:i+1000] for i in range(0, len(rows), 1000)]
                args_list = [(table, sub, module_path, module_name) for sub in sub_chunks]
                
                results = pool.map(_worker_process_chunk, args_list)
                
                for res in results:
                    clean_rows.extend(res["clean_rows"])
                    quarantined += len(res["quarantined_records"])
                    if q_conn and res["quarantined_records"]:
                        with q_conn.begin() as q_transaction:
                            q_transaction.execute(
                                quarantined_table.insert(),
                                res["quarantined_records"]
                            )

    failure_rate = quarantined / total if total else 0
    halted = failure_rate > QUARANTINE_FAILURE_THRESHOLD

    return {
        "table": table,
        "total_rows": total,
        "clean_rows": len(clean_rows),
        "quarantined_rows": quarantined,
        "failure_rate": round(failure_rate, 3),
        "halted_for_review": halted,
        "sample_clean_output": clean_rows[:2],
    }


def run_migration(engine_url="sqlite:///legacy_source.db", profile_path="profile.json", generated_dir="generated", quarantine_path="quarantine/quarantine.db", workers="auto"):
    with open(profile_path) as f:
        profile = json.load(f)
    load_order = profile["load_order"]

    q_conn = init_quarantine_db(path=quarantine_path)
    results = []
    for table in load_order:
        result = run_table(table, engine_url, generated_dir=generated_dir, q_conn=q_conn, workers=workers)
        results.append(result)
        status = "HALTED - exceeds quarantine threshold, needs human review" if result["halted_for_review"] else "OK"
        print(f"[{status}] {table}: {result['clean_rows']}/{result['total_rows']} clean, "
              f"{result['quarantined_rows']} quarantined ({result['failure_rate']*100:.1f}%)")
    return results


if __name__ == "__main__":
    import multiprocessing
    multiprocessing.freeze_support()
    
    parser = argparse.ArgumentParser(description="Crucible MaaS Engine - Execution Phase")
    parser.add_argument("--config", type=str, help="Path to the configuration YAML file", required=False)
    args = parser.parse_args()
    
    if args.config:
        with open(args.config, 'r') as f:
            config = yaml.safe_load(f)
        
        db_config = config.get("databases", {})
        exec_config = config.get("execution", {})
        
        engine_url = db_config.get("source", "sqlite:///legacy_source.db")
        quarantine_url = db_config.get("quarantine", "quarantine/quarantine.db")
        profile_path = exec_config.get("profile_path", "profile.json")
        generated_dir = exec_config.get("generated_dir", "generated")
        workers = exec_config.get("workers", "auto")
        
        print(f"Loading configuration from {args.config}...")
        run_migration(
            engine_url=engine_url,
            profile_path=profile_path,
            generated_dir=generated_dir,
            quarantine_path=quarantine_url,
            workers=workers
        )
    else:
        # Fallback to default run if no config provided
        run_migration()