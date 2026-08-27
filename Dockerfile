# ============================================================
# TickAI Dockerfile (Vue 3 + FastAPI)
# ============================================================

# ---- Stage 1: Build Vue frontend ----
FROM node:20-slim AS frontend-builder

WORKDIR /frontend

COPY frontend/package.json frontend/package-lock.json* ./
RUN npm ci --silent 2>/dev/null || npm install --silent

COPY frontend/ ./
RUN npm run build

# ---- Stage 2: Install Python dependencies ----
FROM python:3.11-slim AS backend-builder

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential libffi-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --user --no-cache-dir -r requirements.txt

# ---- Stage 3: Runtime ----
FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Copy Python dependencies
COPY --from=backend-builder /root/.local /root/.local
ENV PATH=/root/.local/bin:$PATH \
    PYTHONUNBUFFERED=1

# Copy application code
COPY . .

# Copy Vue build output to static/ (served by FastAPI)
COPY --from=frontend-builder /frontend/dist /app/static

# Create data directory
RUN mkdir -p /app/data && chmod 700 /app/data

# Health check (FastAPI)
HEALTHCHECK --interval=30s --timeout=10s --start-period=15s --retries=3 \
    CMD curl -f http://localhost:8000/api/auth/me || exit 1

EXPOSE 8000

CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000"]
