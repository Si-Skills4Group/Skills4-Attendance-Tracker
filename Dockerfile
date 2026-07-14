FROM node:24-bookworm AS frontend

WORKDIR /app
RUN corepack enable

COPY package.json pnpm-lock.yaml pnpm-workspace.yaml tsconfig*.json .npmrc ./
COPY lib ./lib
COPY artifacts/skills4attendance ./artifacts/skills4attendance
COPY attached_assets ./attached_assets

ENV PORT=4173
ENV BASE_PATH=/
ENV CI=true
ENV COREPACK_ENABLE_DOWNLOAD_PROMPT=0
ARG VITE_ENTRA_CLIENT_ID
ARG VITE_ENTRA_TENANT_ID
ARG VITE_ENTRA_AUTHORITY
ARG VITE_ENTRA_REDIRECT_URI
ARG VITE_ENTRA_POST_LOGOUT_REDIRECT_URI
ARG VITE_API_SCOPE
ARG VITE_API_BASE_URL
ENV VITE_ENTRA_CLIENT_ID=${VITE_ENTRA_CLIENT_ID}
ENV VITE_ENTRA_TENANT_ID=${VITE_ENTRA_TENANT_ID}
ENV VITE_ENTRA_AUTHORITY=${VITE_ENTRA_AUTHORITY}
ENV VITE_ENTRA_REDIRECT_URI=${VITE_ENTRA_REDIRECT_URI}
ENV VITE_ENTRA_POST_LOGOUT_REDIRECT_URI=${VITE_ENTRA_POST_LOGOUT_REDIRECT_URI}
ENV VITE_API_SCOPE=${VITE_API_SCOPE}
ENV VITE_API_BASE_URL=${VITE_API_BASE_URL}
RUN pnpm install --frozen-lockfile --reporter=append-only --config.dangerouslyAllowAllBuilds=true
RUN pnpm --filter @workspace/skills4attendance run build

FROM python:3.13-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV ENV=production
ENV NODE_ENV=production
ENV STATIC_DIR=/app/static

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY artifacts/api-server/pyapp ./pyapp
COPY --from=frontend /app/artifacts/skills4attendance/dist/public ./static

EXPOSE 8000
CMD ["sh", "-c", "uvicorn pyapp.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
