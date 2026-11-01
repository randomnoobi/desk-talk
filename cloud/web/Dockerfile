# The website: built with Node, then served by nginx, which also passes /api, /auth and /v1 to the server.
# compose.yaml builds it: cd cloud && docker compose up -d --build
FROM node:22.13.0-alpine AS build
WORKDIR /web
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
RUN npm run build

# This nginx image runs as a user without root rights, and so listens on a port above 1024.
FROM nginxinc/nginx-unprivileged:1.28.0-alpine
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY nginx-proxy.conf /etc/nginx/nginx-proxy.conf
COPY --from=build /web/dist /usr/share/nginx/html
