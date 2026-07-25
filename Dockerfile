# Companion Docker/Podman image for ovos-tui-client - see issue #23 and
# the README's "Docker/Podman companion image" section.
#
# Unlike every other OVOS Docker image (audio, listener, messagebus,
# each skill, etc), this one is NOT a background service - it's an
# interactive terminal tool. Run it with `docker run -it` (or
# `stdin_open: true` + `tty: true` in compose) - without a TTY
# attached, Textual has no terminal to actually draw into.
#
# Built from local source (COPY + pip install .), not from PyPI - the
# release workflow tags and builds this image from the SAME commit
# that gets published to PyPI, avoiding any race between "is the new
# version live on PyPI yet" and "the image build already started".
FROM python:3.11-slim

WORKDIR /app

# Only what's needed to resolve/install dependencies copied first, so
# this layer caches across rebuilds where only application code
# changed, not dependencies.
COPY setup.py version.py requirements.txt README.md ./
COPY ovos_tui_client/ ./ovos_tui_client/

RUN pip install --no-cache-dir .

# Non-root user - no reason this needs root, and it's the safer
# default for a container that (in a future iteration) may have the
# Docker/Podman socket mounted into it for service management (#21).
RUN useradd --create-home --shell /bin/bash ovos
USER ovos

ENTRYPOINT ["ovos-tui"]
