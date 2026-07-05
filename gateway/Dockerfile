# Gateway image: build the React UI, then serve it + proxy the API via nginx.
# Build context is the repo root.

FROM node:22-alpine AS ui-build
WORKDIR /ui
COPY ui/package.json ./
RUN npm install --no-audit --no-fund
COPY ui/ ./
RUN npm run build

FROM nginx:1.27-alpine
COPY gateway/nginx.conf /etc/nginx/nginx.conf
COPY gateway/proxy_params.conf /etc/nginx/proxy_params.conf
COPY --from=ui-build /ui/dist /usr/share/nginx/html
EXPOSE 8080
