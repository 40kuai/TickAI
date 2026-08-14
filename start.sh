#!/bin/bash

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# Backend port
BACKEND_PORT=8000
# Frontend dev port
FRONTEND_PORT=5173

echo "🚀 启动 TickAI..."

# Activate venv
if [ -d ".venv" ]; then
    source .venv/bin/activate
fi

# 加载 .env 到环境变量（JWT_SECRET / ADMIN_INITIAL_PASSWORD / CORS_ORIGINS 等）
# 保证 api.deps / api.main 中 os.environ.get 能读到，避免 JWT_SECRET 每次启动随机
if [ -f ".env" ]; then
    set -a
    source .env
    set +a
fi

# Start FastAPI backend
nohup python -m uvicorn api.main:app --reload --port "$BACKEND_PORT" > /tmp/fastapi.log 2>&1 &
BACKEND_PID=$!

# Start Vue dev server (development mode)
cd frontend
nohup npm run dev -- --port "$FRONTEND_PORT" > /tmp/vite.log 2>&1 &
FRONTEND_PID=$!
cd "$SCRIPT_DIR"

sleep 3

# Check if services are running
BACKEND_OK=$(lsof -ti:"$BACKEND_PORT" 2>/dev/null)
FRONTEND_OK=$(lsof -ti:"$FRONTEND_PORT" 2>/dev/null)

if [ -n "$BACKEND_OK" ] && [ -n "$FRONTEND_OK" ]; then
    echo "✅ TickAI 启动成功"
    echo "📡 后端 API: http://localhost:$BACKEND_PORT"
    echo "🖥️  前端 UI:  http://localhost:$FRONTEND_PORT"
    echo "📋 API 文档: http://localhost:$BACKEND_PORT/docs"
else
    echo "❌ 启动失败"
    if [ -z "$BACKEND_OK" ]; then
        echo "   后端未启动，检查 /tmp/fastapi.log"
    fi
    if [ -z "$FRONTEND_OK" ]; then
        echo "   前端未启动，检查 /tmp/vite.log"
    fi
    exit 1
fi
