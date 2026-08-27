#!/bin/bash

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "🛑 停止 TickAI..."

# Kill by port
BACKEND_PIDS=$(lsof -ti:8000 2>/dev/null)
FRONTEND_PIDS=$(lsof -ti:5173 2>/dev/null)

if [ -n "$BACKEND_PIDS" ]; then
    kill $BACKEND_PIDS 2>/dev/null
    echo "  后端已停止 (PID: $BACKEND_PIDS)"
fi

if [ -n "$FRONTEND_PIDS" ]; then
    kill $FRONTEND_PIDS 2>/dev/null
    echo "  前端已停止 (PID: $FRONTEND_PIDS)"
fi

sleep 1
echo "✅ TickAI 已停止"
