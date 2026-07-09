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

# Also kill old Streamlit if running
STREAMLIT_PIDS=$(lsof -ti:8502 2>/dev/null)
if [ -n "$STREAMLIT_PIDS" ]; then
    kill $STREAMLIT_PIDS 2>/dev/null
    echo "  旧 Streamlit 已停止 (PID: $STREAMLIT_PIDS)"
fi

sleep 1
echo "✅ TickAI 已停止"
