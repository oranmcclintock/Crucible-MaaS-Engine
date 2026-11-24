FROM python:3.12-slim

RUN groupadd -g 1000 appuser && useradd -m -u 1000 -g appuser appuser

WORKDIR /app

COPY pyproject.toml README.md rules.yaml cli.py profiler.py flaw_catalog.py codegen.py reportgen.py seed_dirty_gov_db.py ./
COPY templates ./templates

RUN pip install --no-cache-dir ".[postgres]" \
    && chown -R appuser:appuser /app

USER appuser

ENTRYPOINT ["crucible"]
CMD ["--help"]
