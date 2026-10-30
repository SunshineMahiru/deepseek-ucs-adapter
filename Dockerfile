# syntax=docker/dockerfile:1
#
# Container image published to GitHub Packages (ghcr.io).
# The image installs the *released wheel* rather than copying loose sources,
# so the container and the Release asset are guaranteed to be the same build.
#
# Local build requires `python -m build` first (produces dist/*.whl):
#   python -m build
#   docker build -t deepseek-ucs-adapter:local .

FROM python:3.12-slim

LABEL org.opencontainers.image.title="deepseek-ucs-adapter" \
      org.opencontainers.image.description="Convert DeepSeek web-export conversations.json into a UCS-compatible ChatGPT mapping file." \
      org.opencontainers.image.source="https://github.com/SunshineMahiru/deepseek-ucs-adapter" \
      org.opencontainers.image.licenses="MIT"

COPY dist/*.whl /tmp/wheels/
RUN python -m pip install --no-cache-dir --no-compile --disable-pip-version-check /tmp/wheels/*.whl \
 && rm -rf /tmp/wheels

# Run as an unprivileged user. The work directory stays writable so a bind
# mount can receive the converted file; pass --user "$(id -u):$(id -g)" on
# Linux to keep output ownership identical to the host account.
RUN useradd --create-home --uid 10001 app \
 && mkdir -p /work \
 && chown app:app /work

WORKDIR /work
USER app

ENTRYPOINT ["deepseek-ucs"]
