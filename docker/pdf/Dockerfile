# Optional PDF renderer for deck reviews (DECISIONS D4): Playwright + Chromium, separate from the API image so the
# API stays small. Build context is this directory.   docker compose --profile pdf up -d pdf
FROM python:3.11-slim

ARG PLAYWRIGHT_VERSION=1.64.1
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright

# Chromium + its system libraries.
RUN pip install --no-cache-dir "playwright==${PLAYWRIGHT_VERSION}" \
 && playwright install --with-deps chromium

# Fonts review.css names, so the PDF is typeset as designed instead of falling back to Arial/Georgia: EB Garamond and
# Liberation from Debian; IBM Plex (OFL, not packaged in Debian) from its pinned GitHub release, checksum-verified.
ARG PLEX_SANS_URL=https://github.com/IBM/plex/releases/download/%40ibm/plex-sans%401.1.0/ibm-plex-sans.zip
ARG PLEX_SANS_SHA256=fb365d910566e6d199cc2c15579a7dd9a267128e18431a394ed81f1970c69200
ARG PLEX_MONO_URL=https://github.com/IBM/plex/releases/download/%40ibm/plex-mono%402.5.0/ibm-plex-mono.zip
ARG PLEX_MONO_SHA256=6d23f01257663d8cc49a0d64c22ced630b79e0e2a0ac08a0da86e9a38bbc481c
RUN apt-get update \
 && apt-get install -y --no-install-recommends fonts-ebgaramond fonts-liberation unzip curl ca-certificates \
 && mkdir -p /usr/share/fonts/truetype/ibm-plex /tmp/plex \
 && curl -fsSL -o /tmp/plex/sans.zip "$PLEX_SANS_URL" && echo "$PLEX_SANS_SHA256  /tmp/plex/sans.zip" | sha256sum -c - \
 && curl -fsSL -o /tmp/plex/mono.zip "$PLEX_MONO_URL" && echo "$PLEX_MONO_SHA256  /tmp/plex/mono.zip" | sha256sum -c - \
 && unzip -jq /tmp/plex/sans.zip "ibm-plex-sans/fonts/complete/ttf/IBMPlexSans-*.ttf" -d /usr/share/fonts/truetype/ibm-plex \
 && unzip -jq /tmp/plex/mono.zip "ibm-plex-mono/fonts/complete/ttf/IBMPlexMono-*.ttf" -d /usr/share/fonts/truetype/ibm-plex \
 && fc-cache -f \
 && apt-get purge -y unzip curl && apt-get autoremove -y \
 && rm -rf /var/lib/apt/lists/* /tmp/plex /root/.cache

WORKDIR /app
COPY render.py server.py ./

ENV PORT=8100 PDF_ALLOWED_ORIGIN=http://web:3000
EXPOSE 8100
HEALTHCHECK --interval=15s --timeout=3s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request,os;urllib.request.urlopen('http://127.0.0.1:%s/health' % os.environ.get('PORT','8100'),timeout=2)"
CMD ["python", "server.py"]
