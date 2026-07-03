# Standalone Docker: uses python:3.12-slim (default)
# HA Add-on:         HA supervisor overrides BUILD_FROM with arch-specific base image
ARG BUILD_FROM=python:3.12-slim
FROM $BUILD_FROM

# Install git — needed to pip install energipays-client from GitHub
# Works on both Debian slim (apt-get) and Alpine HA base images (apk)
RUN if command -v apt-get > /dev/null 2>&1; then \
        apt-get update && apt-get install -y --no-install-recommends git \
        && rm -rf /var/lib/apt/lists/*; \
    elif command -v apk > /dev/null 2>&1; then \
        apk add --no-cache git; \
    fi

WORKDIR /app

COPY pyproject.toml .
COPY src/ src/

# Install bridge deps + energipays-client from GitHub.
# Pinned to a commit SHA: an unpinned URL hits Docker layer cache on rebuild,
# silently shipping a stale library (this hid every library fix v1.0.3-v1.1.0).
# Bump the SHA whenever energipays-client main moves.
RUN pip install --no-cache-dir -e . \
 && pip install --no-cache-dir requests pycryptodome \
 && pip install --no-cache-dir git+https://github.com/david2069/energipays-client.git@27dcc2322543c4067607fcb98107ccfd85557a8b

COPY docker-entrypoint.sh /docker-entrypoint.sh
RUN chmod +x /docker-entrypoint.sh

EXPOSE 8080

ENTRYPOINT ["/docker-entrypoint.sh"]
CMD ["energipays-bridge", "run"]
