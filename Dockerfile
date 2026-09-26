FROM python:3.12-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /action
COPY pyproject.toml Readme.MD LICENSE cli.py entrypoint.sh ./
COPY karma/ ./karma/

RUN pip install --no-cache-dir . pytest \
    && chmod +x /action/entrypoint.sh

ENTRYPOINT ["/action/entrypoint.sh"]
