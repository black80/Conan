#!/usr/bin/env bash
set -e

wait_for() {
  local host="$1" port="$2" name="$3"
  echo "waiting for ${name} at ${host}:${port}..."
  for _ in $(seq 1 60); do
    if python -c "import socket,sys; s=socket.socket(); s.settimeout(2); \
      sys.exit(0) if s.connect_ex(('${host}', ${port})) == 0 else sys.exit(1)"; then
      echo "${name} is up"
      return 0
    fi
    sleep 2
  done
  echo "timed out waiting for ${name}"
  exit 1
}

ROLE="${1:-api}"

wait_for "${POSTGRES_HOST}" "${POSTGRES_PORT}" postgres

case "${ROLE}" in
  api)
    wait_for "${RABBITMQ_HOST}" "${RABBITMQ_PORT}" rabbitmq
    alembic upgrade head
    python -m app.db.seed
    exec uvicorn app.main:app --host 0.0.0.0 --port 8000
    ;;
  rule-worker)
    wait_for "${RABBITMQ_HOST}" "${RABBITMQ_PORT}" rabbitmq
    exec celery -A app.workers.celery_app.celery_app worker \
      -Q rules --concurrency 2 --loglevel INFO
    ;;
  ai-worker)
    wait_for "${RABBITMQ_HOST}" "${RABBITMQ_PORT}" rabbitmq
    exec celery -A app.workers.celery_app.celery_app worker \
      -Q ai --concurrency 2 --loglevel INFO
    ;;
  *)
    exec "$@"
    ;;
esac
