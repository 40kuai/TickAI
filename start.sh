#!/bin/bash

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PORT=8502

PID=$(lsof -ti:"$PORT" 2>/dev/null)
if [ -n "$PID" ]; then
    echo "✅ OpsTicket 已在运行 (端口 $PORT, PID: $PID)"
    echo "📡 访问地址: http://localhost:$PORT"
    exit 0
fi

echo "🚀 启动 OpsTicket..."
cd "$SCRIPT_DIR"
if [ -d ".venv" ]; then
    source .venv/bin/activate
fi

nohup python -m streamlit run ui/app.py --server.port "$PORT" > /tmp/hermes_stdout.log 2> /tmp/hermes_llm.log &

sleep 3

PID=$(lsof -ti:"$PORT" 2>/dev/null)
if [ -n "$PID" ]; then
    echo "✅ OpsTicket 启动成功 (PID: $PID)"
    echo "📡 访问地址: http://localhost:$PORT"
else
    echo "❌ OpsTicket 启动失败"
    exit 1
fi