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

# The `docker` CLI binary, copied from Docker's own official CLI-only
# image rather than apt-installing the full engine - this tool only
# ever SHELLS OUT to `docker ps`/`docker logs -f` (see services.py),
# it never needs a running daemon of its own. Real gap found via live
# testing: without this, detect_container_runtime() (which underlies
# BOTH the log bridge and the Services: palette entries) silently
# found nothing at all when this tool itself ran containerized - not
# an error, just quietly empty, since `which docker`/`which podman`
# both come back empty inside a bare python:3.11-slim image.
#
# This alone isn't enough to actually see sibling containers - the
# host's Docker socket also needs to be mounted in at run time
# (`-v /var/run/docker.sock:/var/run/docker.sock`), which is a real,
# deliberate choice with real security weight (it hands this container
# effective control over the host's whole Docker daemon) - not
# something to default to silently. See the README's own section on
# this for the tradeoff and the non-root-user permission wrinkle it
# introduces.
COPY --from=docker:cli /usr/local/bin/docker /usr/local/bin/docker

# Only what's needed to resolve/install dependencies copied first, so
# this layer caches across rebuilds where only application code
# changed, not dependencies.
COPY setup.py version.py requirements.txt README.md ./
COPY ovos_tui_client/ ./ovos_tui_client/

RUN pip install --no-cache-dir .

# Non-root user - no reason this needs root for the normal case (bus
# connection, volume-mounted config/logs). If the Docker socket IS
# mounted in for container detection, this user very likely won't
# have permission to use it as-is (the socket is normally
# root:docker-group owned on the host) - see the README for the
# pragmatic fix (--user root, or matching the host's docker group
# GID) and its own security caveat, rather than silently working
# around it here in a way that would weaken the default for everyone.
RUN useradd --create-home --shell /bin/bash ovos
USER ovos

ENTRYPOINT ["ovos-tui"]
