#!/bin/sh
# Container entrypoint: nginx serves the built frontend on :80 and proxies /api to uvicorn on 127.0.0.1:8000.
set -e
nginx
cd /app/src/
exec uvicorn backend.api:app --host 127.0.0.1 --port 8000 --proxy-headers --forwarded-allow-ips='*'
