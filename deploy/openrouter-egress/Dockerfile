# Minimal OpenRouter reverse-proxy (Railway egress outside RU).
FROM caddy:2.10.0-alpine

COPY Caddyfile /etc/caddy/Caddyfile

# Railway injects PORT; EGRESS_TOKEN must be set in the service variables.
ENV PORT=8080

EXPOSE 8080

CMD ["caddy", "run", "--config", "/etc/caddy/Caddyfile", "--adapter", "caddyfile"]
