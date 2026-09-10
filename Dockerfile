FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright \
    DISPLAY=:99 \
    SCREEN_GEOMETRY=1920x1080x24

# Xvfb gives Chromium a screen to draw on; x11vnc shares that screen; websockify
# + noVNC put it in your web browser so you can complete Apple SSO by hand.
RUN apt-get update && apt-get install -y --no-install-recommends \
        xvfb x11vnc fluxbox novnc websockify \
        ca-certificates procps tini \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
    && playwright install --with-deps chromium

COPY *.py ./
# accounts.json is optional; without it the watcher runs a single account.
COPY accounts.jso[n] ./
COPY entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

# Session cookies die with the browser, but the profile still holds the
# disclaimer-accepted flag and the seen-task list.
ENV STATE_DIR=/app/state
VOLUME ["/app/state"]

EXPOSE 6080

ENTRYPOINT ["/usr/bin/tini", "--", "/usr/local/bin/entrypoint.sh"]
