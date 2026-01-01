FROM python:3.13-slim

WORKDIR /app

# Install uv for fast package management
RUN pip install uv

# Copy dependency files
COPY mothergoose/pyproject.toml mothergoose/uv.lock ./

# Install dependencies using uv (production only, no dev dependencies)
RUN uv sync --frozen --no-dev

# Copy application code
COPY mothergoose/src ./src

# Expose port for FastAPI
EXPOSE 8000

# Run FastAPI application
CMD ["uv", "run", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
