# Production image: React/Vite frontend built in stage 1, FastAPI backend + nginx in stage 2.
FROM node:22-slim AS web
WORKDIR /web
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
RUN apt-get update && apt-get install -y --no-install-recommends nginx     && rm -rf /var/lib/apt/lists/* /etc/nginx/sites-enabled/default
WORKDIR /app/src
COPY . /app/src
RUN pip install -r requirements.txt
COPY --from=web /web/dist /app/web
COPY deploy/nginx.conf /etc/nginx/conf.d/app.conf
EXPOSE 80
CMD ["sh", "/app/src/deploy/start.sh"]
