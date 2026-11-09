# syntax=docker/dockerfile:1
# Multi-stage build — SvelteKit z adapter-node dla ClickHouse (bez SQLite)

FROM node:26-alpine AS base

# ===== deps =====
FROM base AS deps
WORKDIR /app
COPY package.json ./
RUN npm install --include=dev --ignore-scripts

# ===== builder =====
FROM base AS builder
WORKDIR /app
COPY --from=deps /app/node_modules ./node_modules
COPY . .

# Przepisz pliki API tak, by w obrazie produkcyjnym nie importowały natywnego modułu sqlite3
RUN find src/routes/api -name '+server.js' -exec sed -i "s|import sqlite3 from 'sqlite3';||g" {} +

# Zainstaluj adapter-node i podmień adapter-auto w vite.config.js
RUN npm install --save-dev @sveltejs/adapter-node@^5.2.0 --legacy-peer-deps && \
    sed -i "s|import adapter from '@sveltejs/adapter-auto'|import adapter from '@sveltejs/adapter-node'|g" vite.config.js

ENV NODE_ENV=production
RUN npm run build

# ===== runner =====
FROM node:26-alpine AS runner
WORKDIR /app

# Skopiuj zbudowaną aplikację i node_modules
COPY --from=builder /app/build ./build
COPY --from=builder /app/package.json ./
COPY --from=builder /app/node_modules ./node_modules

# Konfiguracja
ENV WINDOW_TRACKER_BACKEND=clickhouse
ENV CLICKHOUSE_URL=http://signoz-telemetrystore-clickhouse-0-0:8123
ENV CLICKHOUSE_DB=signoz_logs
ENV PORT=3003
ENV HOST=0.0.0.0

EXPOSE 3003

CMD ["node", "build"]
