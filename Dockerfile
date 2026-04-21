# Mycelos — The AI That Grows With You
# Docker container for running mycelos serve (Gateway + Web UI)
#
# Build:  docker build -t mycelos .
# Run:    docker run -p 9100:9100 -e LLM_API_KEY=sk-ant-... mycelos
#
# Auto-initializes on first start. Key is encrypted in DB after init.
# Remove LLM_API_KEY from env after first start (security best practice).

FROM python:3.13-slim

# Install Node.js 24 via NodeSource (Debian Trixie's default is too old —
# @n24q02m/better-email-mcp and its @modelcontextprotocol/mcp-core
# dependency both require Node >= 24.15) + gosu (entrypoint user switch)
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl ca-certificates gnupg gosu \
    && curl -fsSL https://deb.nodesource.com/setup_24.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && rm -rf /var/lib/apt/lists/*

# Create non-root user
RUN useradd -m -s /bin/bash mycelos

WORKDIR /app

# Copy project files
COPY pyproject.toml README.md LICENSE ./
COPY src/ src/
COPY docs/ docs/

# Install Python dependencies plus the agent toolkit (beautifulsoup,
# lxml, openpyxl, python-docx, Pillow, pymupdf). See pyproject.toml —
# these ship by default so a container-only user has the libraries
# agents reach for with common data shapes (HTML, Excel, Word, images,
# PDFs). Heavy numeric stacks stay opt-in via a custom image.
RUN pip install --no-cache-dir -e ".[agent-toolkit]"

# Copy entrypoint
COPY docker-entrypoint.sh /usr/local/bin/
RUN chmod +x /usr/local/bin/docker-entrypoint.sh

# Data directory (mount as volume)
RUN mkdir -p /data && chown mycelos:mycelos /data
VOLUME /data

# Entrypoint runs as root to fix volume permissions, then drops to mycelos user
# Do NOT add USER mycelos here — the entrypoint handles the user switch via gosu

# Gateway port
EXPOSE 9100

# Health check
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s \
    CMD curl -f http://localhost:9100/api/health || exit 1

ENTRYPOINT ["docker-entrypoint.sh"]
CMD ["mycelos", "serve", "--host", "0.0.0.0", "--port", "9100", "--data-dir", "/data"]
