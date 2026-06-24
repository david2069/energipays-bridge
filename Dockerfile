FROM python:3.12-slim

WORKDIR /app

COPY pyproject.toml .
COPY src/ src/

# Install energipays-client from the sibling directory (mounted at build time)
# For standalone builds, pin a released version instead.
RUN pip install --no-cache-dir -e . && pip install --no-cache-dir requests pycryptodome

EXPOSE 8080

CMD ["energipays-bridge", "run"]
