FROM python:3.10-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /action

COPY requirements.txt cli.py entrypoint.sh ./
COPY core/ ./core/
COPY languages/ ./languages/

RUN pip install --no-cache-dir -r requirements.txt \
    && chmod +x /action/entrypoint.sh

ENTRYPOINT ["/action/entrypoint.sh"]
