#!/bin/sh
# 로컬 모드: config.yaml이 volume mount되지 않았고 setup 미완료 시 초기화
if [ "$DASHBOARD_MODE" = "local" ] && [ ! -f /app/.setup_done ]; then
  # volume mount된 config.yaml에 유효한 profile이 있으면 건드리지 않음
  if ! grep -q "profile:" /app/config.yaml 2>/dev/null; then
    echo "{}" > /app/config.yaml
  fi
fi

exec "$@"
