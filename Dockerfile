FROM python:3.13-slim

WORKDIR /app

# Install uv for fast package management with pinned version for security
RUN pip install uv==0.5.11

# Copy dependency files
COPY mothergoose/pyproject.toml mothergoose/uv.lock ./

# Install dependencies using uv (production only, no dev dependencies)
RUN uv sync --frozen --no-dev

# Copy application code
COPY mothergoose/src ./src

# Create non-root user and adjust ownership
RUN groupadd -r app && useradd -r -g app app && chown -R app:app /app

# Expose port for FastAPI
EXPOSE 8000

# Run FastAPI application as non-root user
USER app
CMD ["uv", "run", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
