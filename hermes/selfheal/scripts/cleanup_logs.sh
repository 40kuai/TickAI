#!/bin/bash
# 固定日志清理脚本(通道一): 确定性、幂等、安全。
# 用法: bash -s -- <category> [dry]   # 第二个位置参数非空即 dry-run 预览
#   category: system | service | docker-log | docker-prune | all
# 阈值来自环境变量(由 run_cleanup.build_cleanup_command 下发, 默认与 config.py 对齐)。
set -euo pipefail

CATEGORY="${1:-all}"
DRY="${2:-}"
JOURNAL_VACUUM_SIZE="${JOURNAL_VACUUM_SIZE_MB:-200}"
SERVICE_LOG_MAX_MB="${SERVICE_LOG_MAX_MB:-100}"
SERVICE_LOG_MAX_DAYS="${SERVICE_LOG_MAX_DAYS:-7}"
DOCKER_LOG_MAX_MB="${DOCKER_LOG_MAX_MB:-50}"

log() { echo "[cleanup][$(date '+%F %T')] $*"; }

clean_system() {
  log "system: journal vacuum-size=${JOURNAL_VACUUM_SIZE}M"
  if [ -z "$DRY" ]; then
    journalctl --vacuum-size=${JOURNAL_VACUUM_SIZE}M >/dev/null 2>&1 || true
  fi
  [ -d /var/log ] || { log "system: /var/log 不存在, 跳过"; return 0; }
  find /var/log -maxdepth 2 -type f \
    \( -name '*.gz' -o -name '*.1' -o -name '*.old' \) \
    -mtime +${SERVICE_LOG_MAX_DAYS} 2>/dev/null | while read -r f; do
    log "system: remove $f"
    [ -z "$DRY" ] && rm -f "$f" || true
  done || true
}

clean_service() {
  log "service: scan /data/*/logs (max=${SERVICE_LOG_MAX_MB}M days=${SERVICE_LOG_MAX_DAYS})"
  [ -d /data ] || { log "service: /data 不存在, 跳过"; return 0; }
  find /data -maxdepth 3 -type d -name logs 2>/dev/null | while read -r d; do
    find "$d" -maxdepth 1 -type f \( -name '*.log' -o -name '*.log.*' \) \
      -size +${SERVICE_LOG_MAX_MB}M -mtime +${SERVICE_LOG_MAX_DAYS} 2>/dev/null | while read -r f; do
      log "service: truncate $f"
      [ -z "$DRY" ] && : > "$f" || true
    done || true
  done || true
}

clean_docker_log() {
  log "docker-log: scan containers json.log (max=${DOCKER_LOG_MAX_MB}M)"
  [ -d /var/lib/docker/containers ] || { log "docker-log: docker 数据目录不存在, 跳过"; return 0; }
  find /var/lib/docker/containers -maxdepth 2 -name '*-json.log' \
    -size +${DOCKER_LOG_MAX_MB}M 2>/dev/null | while read -r f; do
    log "docker-log: truncate $f"
    [ -z "$DRY" ] && truncate -s 0 "$f" || true
  done || true
}

clean_docker_prune() {
  log "docker-prune: prune stopped containers + dangling images"
  command -v docker >/dev/null 2>&1 || { log "docker-prune: docker 不存在, 跳过"; return 0; }
  if [ -z "$DRY" ]; then
    docker container prune -f >/dev/null 2>&1 || true
    docker image prune -f >/dev/null 2>&1 || true
  fi
}

case "$CATEGORY" in
  system) clean_system ;;
  service) clean_service ;;
  docker-log) clean_docker_log ;;
  docker-prune) clean_docker_prune ;;
  all) clean_system; clean_service; clean_docker_log; clean_docker_prune ;;
  *) echo "unknown category: $CATEGORY" >&2; exit 2 ;;
esac
log "done: $CATEGORY"
