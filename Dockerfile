# ============================================================
# TickAI Dockerfile
# ============================================================
# Build stage: install dependencies
FROM python:3.11-slim AS builder

WORKDIR /app

# Install system dependencies (for bcrypt, paramiko, etc.)
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libffi-dev \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements first for better layer caching
COPY requirements.txt .

# Install dependencies to a local directory
RUN pip install --user --no-cache-dir -r requirements.txt

# ============================================================
# Runtime stage
# ============================================================
FROM python:3.11-slim

WORKDIR /app

# Install runtime system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Copy installed dependencies from builder
COPY --from=builder /root/.local /root/.local

# Make sure local bin is in PATH
ENV PATH=/root/.local/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    STREAMLIT_SERVER_PORT=8502 \
    STREAMLIT_SERVER_HEADLESS=true \
    STREAMLIT_BROWSER_GATHER_USAGE_STATS=false

# Copy application code
COPY . .

# Create data directory for SQLite
RUN mkdir -p /app/data && chmod 700 /app/data

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
    CMD curl -f http://localhost:8502/_stcore/health || exit 1

# Expose Streamlit port
EXPOSE 8502

# Run with Alembic migration first, then Streamlit
CMD ["bash", "-c", "alembic upgrade head && streamlit run ui/app.py"]
