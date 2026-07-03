#!/bin/bash

PORT=8502

PIDS=$(lsof -ti:"$PORT" 2>/dev/null)
if [ -z "$PIDS" ]; then
    echo "ℹ️ OpsTicket 未在运行"
    exit 0
fi

echo "🛑 停止 OpsTicket (PIDs: $PIDS)..."
kill $PIDS 2>/dev/null

sleep 2

PIDS=$(lsof -ti:"$PORT" 2>/dev/null)
if [ -z "$PIDS" ]; then
    echo "✅ OpsTicket 已停止"
else
    echo "⚠️ 强制终止中..."
    kill -9 $PIDS 2>/dev/null
    sleep 1
    PIDS=$(lsof -ti:"$PORT" 2>/dev/null)
    if [ -z "$PIDS" ]; then
        echo "✅ OpsTicket 已强制停止"
    else
        echo "❌ 停止失败，请手动终止"
        exit 1
    fi
fi