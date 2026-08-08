FROM python:3.11-slim

# Create a non-root user
RUN groupadd -r appuser && useradd -r -g appuser appuser

WORKDIR /app

# Install dependencies
RUN pip install --no-cache-dir sqlalchemy pydantic jinja2 psycopg2-binary pyyaml networkx

# Copy source files
COPY . /app/

# Set ownership and switch to non-root user
RUN chown -R appuser:appuser /app
USER appuser

# Placeholder entrypoint
CMD ["python", "run_pipeline.py"]
