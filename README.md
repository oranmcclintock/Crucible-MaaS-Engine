# Crucible MaaS Engine

**Deterministic Legacy Database Migration & "Dirty Data" Quarantine Engine**

System Integrators (SIs) and Enterprise Cloud transformation teams lose up to 50% of their profit margins fixing undocumented legacy schema decay. Furthermore, up to 95% of Generative AI initiatives stall due to the data preparation bottleneck. 

The Crucible Engine provides a deterministic, highly secure extraction and quarantine process that mathematically prevents dirty data from entering modern cloud architectures.

---

## The "Code-as-a-Deliverable" Model

Crucible is not a SaaS product that requires sensitive PII upload. We operate under a "Code-as-a-Deliverable" model.

We generate standard Python Pydantic models based on legacy schema constraints. The execution engine runs entirely within your secure environment.

1. **Zero PII Exposure:** The engine generates code from anonymized schema dumps. We do not require access to live data.
2. **Infrastructure-as-Code Ready:** The engine is configured via a standard `config.yml` file, integrating into existing DevOps pipelines.
3. **No Proprietary Vendor Lock-in:** The engine outputs standard, readable Python code. 

---

## How It Works: The 3-Phase Pipeline

### Phase 1: The Profiler (Mapping the Decay)
Legacy databases contain decades of undocumented logic, missing foreign keys, and recursive dependencies. Attempting a strict migration without mapping often results in database failures.
*   The Profiler scans the legacy tables and calculates the Topological Load Order using Directed Acyclic Graphs (DAGs) to ensure foreign key constraints are respected.
*   It outputs a comprehensive `profile.json` blueprint mapping the structural decay.

### Phase 2: The Generator (Creating the Filter)
Writing manual ETL scripts to coerce legacy formats into modern constraints is highly labor-intensive.
*   The Generator uses `Jinja2` templates to write strict Python `Pydantic` modules (`source_data_transform.py`).
*   These modules act as rigid checkpoints that enforce target cloud schema constraints.

### Phase 3: The Executor (The Quarantine)
The Executor streams live data through the generated Pydantic checkpoints using an optimized Python `multiprocessing` pool.
*   If a row is clean, it is written to the modern cloud database.
*   If a row contains uncoercible rot, the engine catches the exception and routes the single broken row to a dedicated Quarantine Database. The batch processing continues uninterrupted.

---

## Execution & Configuration

The engine is executed using a standard YAML configuration file. 

1. Edit the `config_template.yml` file with your database URLs:
```yaml
databases:
  source: "oracle+oracledb://admin:password@10.0.1.55:1521/?service_name=legacy_prod"
  quarantine: "postgresql://admin:password@10.0.2.100:5432/quarantine_db"
execution:
  generated_dir: "./generated"
  profile_path: "./generated/profile.json"
  workers: auto
```

2. Run the engine:
```bash
python3 executor.py --config config.yml
```

---

## Repository Structure

*   `/` (Root): The core deterministic engine (`profiler.py`, `codegen.py`, `executor.py`).
*   `/templates`: The Jinja2 templates used to dynamically generate the Pydantic modules.
*   `/crm`: Internal lead pipeline and CRM tools (`lead_pipeline.py`, `sniper.py`) to monitor UK Government Contracts Finder APIs for active SI target acquisition.
*   `/scripts`: Automation and testing scripts (e.g., local SQLite database generators for reproducible case studies).
*   `/caseStudy`: Real-world validated case studies demonstrating the engine successfully routing organically rotted open-source data into the quarantine.
*   `/k8s`: Kubernetes manifests and Dockerfiles for enterprise-grade ArgoCD deployments of the engine.