FROM python:3.12-slim

WORKDIR /app

COPY pyproject.toml .
COPY src/ src/

# Install the bridge and its deps
RUN pip install --no-cache-dir -e . && pip install --no-cache-dir requests pycryptodome

# energipays-client is volume-mounted at /energipays-client at runtime.
# Install it at startup via an entrypoint wrapper so the editable install
# reflects the mounted source without needing a rebuild on every client change.
COPY docker-entrypoint.sh /docker-entrypoint.sh
RUN chmod +x /docker-entrypoint.sh

EXPOSE 8080

ENTRYPOINT ["/docker-entrypoint.sh"]
CMD ["energipays-bridge", "run"]
