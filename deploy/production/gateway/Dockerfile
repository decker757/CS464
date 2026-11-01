# [5.3] #19. The gateway: the built frontend, behind the nginx that also routes
# each service's subdomain. Built from the repository root:
#
#   docker build -f deploy/production/gateway/Dockerfile --build-arg DOMAIN=example.com .
#
# The frontend's service URLs are fixed at build time (Vite inlines
# import.meta.env), so an image is built for one domain.

FROM node:20-alpine AS frontend
WORKDIR /frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
ARG DOMAIN
RUN test -n "$DOMAIN" || (echo "DOMAIN build arg is required" && exit 1)
ENV VITE_API_URL=https://auth.${DOMAIN} \
    VITE_MARKET_API_URL=https://market.${DOMAIN} \
    VITE_LEDGER_API_URL=https://ledger.${DOMAIN} \
    VITE_AUDIT_API_URL=https://audit.${DOMAIN} \
    VITE_REALTIME_WS_URL=wss://realtime.${DOMAIN}
RUN npm run build

FROM nginx:1.29-alpine
# The image renders templates/*.template into conf.d/ when it starts.
COPY deploy/production/gateway/default.conf.template /etc/nginx/templates/
COPY deploy/production/gateway/proxy-headers.inc /etc/nginx/conf.d/
COPY --from=frontend /frontend/dist /usr/share/nginx/html
