# Multi-stage: build the static bundle with Node, serve it with nginx. Only
# nginx and the built dist/ ship in the final image - no Node runtime, no
# node_modules, no source.
FROM node:20-alpine AS build
WORKDIR /app
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM nginx:alpine
COPY docker/nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html

EXPOSE 80
