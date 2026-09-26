# Standalone Karma image, for running outside GitHub Actions:
#
#   docker build -t karma .
#   docker run --rm -v "$PWD:/repo" karma run --base origin/main
#
# Only pytest is installed; a project with dependencies should build its own image
# (FROM this one, or `pip install karma-test-selector` into its existing image).
FROM python:3.13-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/* \
    # The mounted checkout belongs to another user; let git operate on it.
    && git config --system --add safe.directory '*'

WORKDIR /opt/karma
COPY pyproject.toml Readme.MD LICENSE ./
COPY karma/ ./karma/
RUN pip install --no-cache-dir . pytest

WORKDIR /repo
ENTRYPOINT ["karma"]
CMD ["run"]
