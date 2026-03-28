FROM python:3.14-slim-bookworm

# Install Chromium and ChromeDriver.
# Both from the same apt source ensures version match (chrome.md A-3/A-4).
RUN apt-get update && apt-get install -y --no-install-recommends \
    chromium \
    chromium-driver \
    && rm -rf /var/lib/apt/lists/*

# Build-time version verification (chrome.md A-3/A-5).
RUN chromium --version && chromedriver --version

# Install uv (Astral's fast Python package manager).
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

WORKDIR /app

# Install Python dependencies first (layer-cached unless pyproject.toml/uv.lock changes).
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

# Copy application code.
COPY rotten_tomatoes.py movies.json ./

# Tell Selenium where to find the Chromium binary (chrome.md A-9).
ENV CHROME_BIN=/usr/bin/chromium

# Add venv to PATH so `python` resolves to the uv-managed interpreter.
ENV PATH="/app/.venv/bin:$PATH"

CMD ["python", "rotten_tomatoes.py"]
