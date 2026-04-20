# Adding Python packages to Mycelos (Docker deployment)

Runtime `pip install` is disabled in the Docker deployment for three reasons:

1. The gateway container has no outbound internet route (Phase 1b security lockdown).
2. Runtime package installs are not persistent — they disappear on the next container restart.
3. Installing arbitrary packages on user demand is a supply-chain risk (flagged P0 in the security audit).

If you need extra Python packages (for example to process images with Pillow or scrape HTML with BeautifulSoup), build a custom image:

## Option A: One-shot Dockerfile

Create a `Dockerfile.custom` next to your `docker-compose.yml`:

```dockerfile
FROM ghcr.io/mycelos-ai/mycelos:main
RUN pip install --no-cache-dir \
    pillow>=10.0 \
    beautifulsoup4>=4.12 \
    lxml>=5.0
```

Point compose at it with a `docker-compose.override.yml`:

```yaml
services:
  gateway:
    image: mycelos-custom:local
    build:
      context: .
      dockerfile: Dockerfile.custom
  proxy:
    image: mycelos-custom:local
    build:
      context: .
      dockerfile: Dockerfile.custom
```

Then:

```bash
docker compose build
docker compose up -d
```

## Option B: The rich pre-built image (Phase 1c)

Phase 1c of Mycelos ships a `mycelos:rich` tag with a curated set of common packages (Pillow, BeautifulSoup, lxml, pypdf, python-dateutil) pre-installed. When that lands, set `image: ghcr.io/mycelos-ai/mycelos:rich` in `docker-compose.yml` instead of the slim default.

## Why not use MCP instead?

For many "I need a package" scenarios, an MCP server is the better answer — it runs as its own process, carries its own dependencies, and stays isolated from the gateway. Browser automation (Playwright MCP), image generation (via API), and CLI wrapping (GitHub, Slack, Notion) all have mature MCP servers. Only reach for a custom image when you specifically need to call a Python library from inside an agent's tool code.
