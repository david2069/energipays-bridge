# Standalone Docker: uses python:3.12-slim (default)
# HA Add-on:         HA supervisor overrides BUILD_FROM with arch-specific base image
ARG BUILD_FROM=python:3.12-slim
FROM $BUILD_FROM

WORKDIR /app

COPY pyproject.toml .
COPY src/ src/

RUN pip install --no-cache-dir -e . && pip install --no-cache-dir requests pycryptodome

COPY docker-entrypoint.sh /docker-entrypoint.sh
RUN chmod +x /docker-entrypoint.sh

EXPOSE 8080

ENTRYPOINT ["/docker-entrypoint.sh"]
CMD ["energipays-bridge", "run"]
