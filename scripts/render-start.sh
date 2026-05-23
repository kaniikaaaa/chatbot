#!/usr/bin/env bash
set -euo pipefail

export API_AUTH_TOKEN="${API_AUTH_TOKEN:-render-demo-token}"
export INGESTION_WRITE_KEY="${INGESTION_WRITE_KEY:-render-ingestion-key}"
export DATABASE_URL="${DATABASE_URL:-postgres://chatbot:chatbot@127.0.0.1:5432/chatbot}"
export REDIS_URL="${REDIS_URL:-redis://127.0.0.1:6379}"
export CHATBOT_API_URL="${CHATBOT_API_URL:-http://127.0.0.1:8000}"
export INGESTION_API_URL="${INGESTION_API_URL:-http://127.0.0.1:8001}"
export INGESTION_URL="${INGESTION_URL:-http://127.0.0.1:8001/v1/logs}"
export ALLOWED_ORIGINS="${ALLOWED_ORIGINS:-http://localhost:3000}"
export DEFAULT_PROVIDER="${DEFAULT_PROVIDER:-openai}"
export DEFAULT_MODEL="${DEFAULT_MODEL:-gpt-4o-mini}"
export PII_REDACT="${PII_REDACT:-1}"
export CONTEXT_TURNS="${CONTEXT_TURNS:-12}"

PGDATA="${PGDATA:-/tmp/render-postgres}"
export PGDATA

if [ ! -s "$PGDATA/PG_VERSION" ]; then
  mkdir -p "$PGDATA"
  chown -R postgres:postgres "$PGDATA"
  printf '%s\n' chatbot > /tmp/postgres-password
  chown postgres:postgres /tmp/postgres-password
  su postgres -c "/usr/lib/postgresql/*/bin/initdb -D '$PGDATA' --username=chatbot --pwfile=/tmp/postgres-password"
  rm -f /tmp/postgres-password
  su postgres -c "/usr/lib/postgresql/*/bin/pg_ctl -D '$PGDATA' -o \"-c listen_addresses='127.0.0.1'\" -w start"
  createdb -h 127.0.0.1 -U chatbot chatbot
  psql "$DATABASE_URL" -f /app/db/init.sql
else
  chown -R postgres:postgres "$PGDATA"
  su postgres -c "/usr/lib/postgresql/*/bin/pg_ctl -D '$PGDATA' -o \"-c listen_addresses='127.0.0.1'\" -w start"
fi

redis-server --save "" --appendonly no --daemonize yes

uvicorn main:app --app-dir /app/ingestion --host 127.0.0.1 --port 8001 &
uvicorn main:app --app-dir /app/chatbot-api --host 127.0.0.1 --port 8000 &

cd /app/frontend
exec node server.js
