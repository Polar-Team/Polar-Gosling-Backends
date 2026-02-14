# Dockerfile for MotherGoose Backend
# Multi-stage build for optimized production image

FROM python:3.13-slim AS builder

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    git \
    && rm -rf /var/lib/apt/lists/*

# Install uv for fast package management
RUN pip install --no-cache-dir uv

# Copy dependency files
COPY mothergoose/pyproject.toml mothergoose/uv.lock ./

# Install dependencies using uv
RUN uv sync --frozen --no-dev

# Production stage
FROM python:3.13-slim

WORKDIR /app

# Install runtime dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Create non-root user
RUN useradd -m -u 1000 mothergoose && \
    chown -R mothergoose:mothergoose /app

# Copy virtual environment from builder
COPY --from=builder --chown=mothergoose:mothergoose /app/.venv /app/.venv

# Copy application code
COPY --chown=mothergoose:mothergoose mothergoose/src /app/src
COPY --chown=mothergoose:mothergoose mothergoose/pyproject.toml /app/

# Set environment variables
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    MOTHERGOOSE_ENVIRONMENT=production

# Switch to non-root user
USER mothergoose

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=40s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

# Expose port
EXPOSE 8000

# Default command: Run FastAPI application
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]

# Alternative commands (override with docker run):
# Celery worker: celery -A app.celery_worker worker --loglevel=info -Q mothergoose
# Celery beat: celery -A app.celery_worker beat --loglevel=info
