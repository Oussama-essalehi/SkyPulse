# syntax=docker/dockerfile:1.7
FROM node:22-alpine AS build
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY . .
# Empty = same-origin "/api" (nginx proxy). Set to e.g. https://api.example.com for split hosting.
ARG VITE_API_URL=""
ENV VITE_API_URL=${VITE_API_URL}
RUN npm run build

FROM nginx:1.27-alpine
ENV API_UPSTREAM=api:8000 NGINX_RESOLVER=127.0.0.11
# Only these variables are substituted (nginx $variables are left alone).
ENV NGINX_ENVSUBST_FILTER="^(API_UPSTREAM|NGINX_RESOLVER)$"
COPY nginx.conf.template /etc/nginx/templates/default.conf.template
COPY --from=build /app/dist /usr/share/nginx/html
EXPOSE 80
HEALTHCHECK --interval=15s --timeout=3s --retries=5 CMD wget -qO- http://127.0.0.1/healthz || exit 1
