FROM python:3.13-slim

WORKDIR /app

# Install uv for fast package management
RUN pip install uv

# Copy dependency files
COPY mothergoose/pyproject.toml mothergoose/uv.lock ./

# Install dependencies using uv
RUN uv sync --frozen

# Copy application code
COPY mothergoose/ ./mothergoose/

# Expose port for FastAPI
EXPOSE 8000

# Run FastAPI application
CMD ["uv", "run", "uvicorn", "mothergoose.src.app.main:app", "--host", "0.0.0.0", "--port", "8000"]
