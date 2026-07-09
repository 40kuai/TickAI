#!/bin/bash

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "🔄 重启 TickAI..."
"$SCRIPT_DIR/stop.sh"
sleep 1
"$SCRIPT_DIR/start.sh"